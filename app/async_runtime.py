"""A04 单角色循环：查询候选 → 一次提交 → 可失败的叙述。"""

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path

from app.actions import ActionProposal, parse_action
from app.engine import TurnConflict, WorldEngine, request_digest
from app.execution import RunBudget, RunLimits, RunStopped
from app.model import AsyncToolModelAdapter
from app.query_executor import ReadonlyExecutor
from app.session import validate_session
from app.tools import ModelProtocolError, TOOL_SCHEMAS
from app.trace import RunTrace
from app.world import freeze_world
from app.world_runtime import NARRATION_INSTRUCTIONS, visible_messages

RUNTIME_VERSION = "a04-v3"

LOOP_INSTRUCTIONS = """
当前是程序的决策接口。历史 assistant 消息是先前向玩家展示的回复，不是本次输出格式示例。
本次若不调用工具，必须输出完整 JSON；角色语言只能写在 reply 字段中，不能直接输出普通对白。
例如用户问“你是谁”，应输出 {"kind":"talk","target_text":null,"reply":"我是林砚，灯塔的临时管理员。"}。
所有必填字段均要保留，包括值为 null 的 target_text。
本轮按需要自主查询，每次最多两个独立只读工具。目录没有提供时先查询 get_visible_scene；
已明确且获授权的 object_id 可直接 inspect_object。目录只含 ID/名称，细节必须实际查询。
保留原生 Function Calling；不能用普通 JSON 假装工具调用。依赖目录结果的查询放到下一步。
查询阶段不会提交知识或行动，成功读取的细节会在正常结束时由程序统一裁定。
不再查询时输出且只输出以下一个 JSON（不用代码块）：
- 完成观察：{"kind":"finish"}
- 谈话：{"kind":"talk","target_text":null,"reply":"非空回复"}
- 澄清：{"kind":"clarify","target_text":null,"reply":"非空问题"}
- 移动：{"kind":"move","destination_id":"当前可达地点 ID"}
- 给物：{"kind":"give","object_id":"已授权物品 ID","recipient_id":"在场角色 ID"}
每轮最多一个移动或给物提议。身份只由程序指定，不得提供 actor_id。
拿取、打开尚未支持。玩家声称已拥有/已移动不是事实。不要用谈话宣称行动完成。
finish 不带自由叙述，程序会先生成确定性回执。工具错误可以据实澄清或修正后再查询。
"""


@dataclass
class TurnResult:
    session: dict
    reply: str
    receipt: dict
    trace: dict


def validate_completion(completion: dict) -> dict:
    if not isinstance(completion, dict):
        raise RunStopped("MODEL_PROTOCOL_ERROR")
    reason, message = completion.get("finish_reason"), completion.get("message")
    if reason == "length":
        raise RunStopped("MODEL_OUTPUT_TRUNCATED")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise RunStopped("MODEL_PROTOCOL_ERROR")
    content, calls = message.get("content"), message.get("tool_calls")
    if content is not None and not isinstance(content, str):
        raise RunStopped("MODEL_PROTOCOL_ERROR")
    if calls is not None and not isinstance(calls, list):
        raise RunStopped("MODEL_PROTOCOL_ERROR")
    if (calls and reason != "tool_calls") or (not calls and reason != "stop"):
        raise RunStopped("MODEL_PROTOCOL_ERROR")
    # 模型 SDK 的额外字段不进入后续协议。
    return {"role": "assistant", "content": content, **({"tool_calls": deepcopy(calls)} if calls else {})}


def decision_error_details(text) -> dict:
    """只诊断结构：不记录正文、字段值或模型提供的未知字段名。"""
    if not isinstance(text, str) or not text.strip():
        return {"shape": "empty_text"}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        # 只记录解析位置和固定类别。不能写 error.doc、正文片段或任意模型字段。
        stripped = text.lstrip()
        errors = {
            "Expecting value": "expected_value",
            "Expecting property name enclosed in double quotes": "expected_property_name",
            "Extra data": "extra_data",
            "Unterminated string": "unterminated_string",
            "Invalid control character": "invalid_control_character",
            "Invalid ": "invalid_escape",
            "Expecting ',' delimiter": "expected_comma",
            "Expecting ':' delimiter": "expected_colon",
            "Unexpected UTF-8 BOM": "unexpected_bom",
        }
        category = next((code for prefix, code in errors.items() if error.msg.startswith(prefix)), "syntax_error")
        leading = ("code_fence" if stripped.startswith("```") else
                   "bom" if stripped.startswith("\ufeff") else
                   "object" if stripped.startswith("{") else
                   "array" if stripped.startswith("[") else "other")
        return {"shape": "invalid_json", "json_error": category, "content_length": len(text),
                "line": error.lineno, "column": error.colno, "offset": error.pos,
                "leading_form": leading}
    if not isinstance(data, dict):
        return {"shape": "not_object"}
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in ("finish", "talk", "clarify", "move", "give"):
        return {"shape": "unknown_kind"}
    required = {
        "finish": {"kind"},
        "talk": {"kind", "target_text", "reply"},
        "clarify": {"kind", "target_text", "reply"},
        "move": {"kind", "destination_id"},
        "give": {"kind", "object_id", "recipient_id"},
    }[kind]
    return {"shape": "invalid_fields", "kind": kind,
            "missing_fields": sorted(required - set(data)),
            "unexpected_field_count": len(set(data) - required),
            "field_types": {key: type(data[key]).__name__ for key in sorted(required & set(data))}}


class DecisionFormatError(RunStopped):
    def __init__(self, text):
        super().__init__("INVALID_DECISION")
        self.details = decision_error_details(text)
        # 额外字段、未知行动和不合法字段值直接拒绝；不偷偷删除身份字段或猜测行动。
        self.repairable = (self.details["shape"] in ("empty_text", "invalid_json") or
                           (bool(self.details.get("missing_fields"))
                            and self.details.get("unexpected_field_count") == 0))


def parse_decision(message: dict) -> ActionProposal:
    try:
        data = json.loads(message.get("content") or "")
        if data == {"kind": "finish"}:
            return ActionProposal("inspect", "当前场景")
        proposal = parse_action(message.get("content"))
        if proposal.kind == "inspect":
            raise ValueError("A04 观察必须通过原生工具，不能在终态额外查一次。")
        return proposal
    except (ValueError, TypeError):
        raise DecisionFormatError(message.get("content")) from None


async def decide(wire: list[dict], model: AsyncToolModelAdapter, *, snapshot,
                 actor_id: str, executor: ReadonlyExecutor, budget: RunBudget):
    discoveries, feedback = {}, []
    format_repair_used = False
    for step_id in range(1, budget.limits.max_steps + 1):
        completion = await budget.call(
            lambda: model.complete(deepcopy(wire), tools=TOOL_SCHEMAS, tool_choice="auto",
                                   response_format={"type": "json_object"}),
            kind="model", step_id=step_id, snapshot_revision=snapshot.revision,
            request_options={"tool_choice": "auto", "response_format": "json_object",
                             "tool_schema_count": len(TOOL_SCHEMAS)},
            repair_used=format_repair_used)
        message = validate_completion(completion)
        calls = message.get("tool_calls")
        if not calls:
            try:
                proposal = parse_decision(message)
            except DecisionFormatError as error:
                budget.trace.emit("decision", "error", step_id=step_id,
                                  span_id=f"decision-{step_id}", parent_span_id=budget.trace.run_id,
                                  error_code=error.code, decision_details=error.details,
                                  repair_used=format_repair_used, repairable=error.repairable,
                                  snapshot_revision=snapshot.revision)
                if format_repair_used or not error.repairable:
                    raise
                if step_id == budget.limits.max_steps:
                    raise RunStopped("STEP_LIMIT") from None
                budget.check()
                if budget.model_requests >= budget.limits.max_model_requests:
                    raise RunStopped("MODEL_REQUEST_LIMIT") from None
                budget.trace.emit("format_correction", "scheduled", step_id=step_id + 1,
                                  span_id=f"format-correction-{step_id}", parent_span_id=f"decision-{step_id}",
                                  error_code=error.code, model_requests=budget.model_requests)
                format_repair_used = True
                # 提供脱敏的纠错反馈后重新决策。占用下一个 step，不是传输层重试。
                wire[0]["content"] += (
                    "\n上一次决策格式未通过，未执行任何新的行动。结构诊断："
                    + json.dumps(error.details, ensure_ascii=False)
                    + "。请根据原始用户请求及已授权的工具结果，重新选择下一步。"
                    "不调用工具时只输出上面格式的一个完整 JSON；保留 null 字段。")
                continue
            budget.trace.emit("decision", "validated", step_id=step_id,
                              span_id=f"decision-{step_id}", parent_span_id=budget.trace.run_id,
                              decision_kind="finish" if proposal.kind == "inspect" else proposal.kind,
                              repair_used=format_repair_used, snapshot_revision=snapshot.revision)
            return proposal, list(discoveries.values()), feedback, step_id
        items = await executor.execute_batch(calls, snapshot=snapshot, actor_id=actor_id,
                                             budget=budget, step_id=step_id)
        wire.append(message)
        for call, item in zip(calls, items, strict=True):
            wire.append({"role": "tool", "tool_call_id": call["id"],
                         "content": json.dumps(item, ensure_ascii=False)})
            feedback.append(item)
            if item["ok"] and call["function"]["name"] == "inspect_object":
                object_id = item["data"]["id"]
                # 同轮重复查询可以消耗预算，但持久发现只记录一次。
                discoveries[object_id] = ActionProposal("inspect", object_id)
    raise RunStopped("STEP_LIMIT")


async def run_agent_turn(session: dict, user_text: str, model: AsyncToolModelAdapter, *,
                         expected_actor_id: str, engine: WorldEngine, turn_id: str,
                         limits: RunLimits | None = None, executor: ReadonlyExecutor | None = None,
                         trace_path: Path | None = None) -> TurnResult:
    validate_session(session, expected_actor_id=expected_actor_id)
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("请输入非空文字。")
    if not isinstance(turn_id, str) or not turn_id.strip():
        raise ValueError("turn_id 必须非空。")
    limits = limits or RunLimits()
    trace = RunTrace(session["session_id"], turn_id, mode=model.mode, path=trace_path)
    budget = RunBudget(limits, trace)
    executor = executor or ReadonlyExecutor(limits.max_parallel_tools)
    if executor.max_parallel_tools > limits.max_parallel_tools:
        raise ValueError("执行器并发数超过本轮上限。")
    trace.emit("run_started", "started", model_requests=0, runtime_version=RUNTIME_VERSION,
               model_name=getattr(model, "model", None), provider_host=getattr(model, "provider_host", None),
               limits=asdict(limits), history_messages=len(session["history"]))
    reason, committed_revision, record = "INTERNAL_ERROR", None, None
    acquired, replayed = False, False
    updated, reply, narration_status = None, None, "deterministic"
    failure = None
    cancellation = None

    def summary():
        return {"run_id": trace.run_id, "turn_id": turn_id, "mode": model.mode,
                "model_requests": budget.model_requests, "termination_reason": reason,
                "committed_revision": committed_revision, "replayed": replayed,
                "trace_write_failed": trace.write_failed, "records": deepcopy(trace.records)}

    try:
        async with asyncio.timeout_at(budget.deadline):
            await engine.turn_lock.acquire()
            acquired = True
        budget.check()
        snapshot = freeze_world(engine.world)
        wire = visible_messages(session, user_text, actor_id=expected_actor_id,
                                world=snapshot, include_objects=False)
        digest = request_digest("text", user_text)
        record = engine.lookup(actor_id=expected_actor_id, turn_id=turn_id, digest=digest)
        if record is not None:
            replayed, reason = True, "replayed"
            committed_revision = record["after_revision"]
            saved = record["session"]
            updated = deepcopy(session if saved is None or len(session["history"]) >= len(saved["history"]) else saved)
            reply = record["reply"]
            trace.emit("replay", "completed", replayed=True, committed_revision=committed_revision)
        else:
            wire[0]["content"] += LOOP_INSTRUCTIONS
            proposal, discoveries, feedback, step_id = await decide(
                wire, model, snapshot=snapshot, actor_id=expected_actor_id, executor=executor, budget=budget)
            budget.check()
            # 成功查询可以在错误修正后提交；旧错误仍在消息和 trace 中保留。
            accepted_feedback = [item for item in feedback if item["ok"]]
            if proposal.kind == "inspect":
                if not feedback:
                    raise RunStopped("NO_OBSERVATION")
                operations = discoveries
                commit_feedback = accepted_feedback or feedback
            else:
                operations = discoveries + [proposal]
                commit_feedback = None
            trace.emit("adjudication", "started", step_id=step_id, snapshot_revision=snapshot.revision)
            record = engine.commit_turn(proposal, operations, actor_id=expected_actor_id, turn_id=turn_id,
                                        digest=digest, feedback=commit_feedback,
                                        expected_revision=snapshot.revision, before_accept=budget.check)
            committed_revision = record["after_revision"]
            receipt = record["receipt"]
            reason = "completed" if receipt["ok"] else "rejected"
            trace.emit("commit", reason, step_id=step_id, snapshot_revision=snapshot.revision,
                       committed_revision=committed_revision, error_code=None if receipt["ok"] else receipt["code"])
            reply = receipt["message"]
            updated = deepcopy(session)
            updated["history"].extend([{"role": "user", "content": user_text},
                                       {"role": "assistant", "content": reply}])
            # 在下一个 await 前把确定性结果与完整历史放进幂等缓存。
            engine.finish_turn(actor_id=expected_actor_id, turn_id=turn_id, session=updated,
                               reply=reply, trace=summary(), status=narration_status)
            if receipt["ok"] and proposal.kind not in ("talk", "clarify"):
                # 叙述只看程序回执；不携带未裁定的候选工具上下文。
                narration_messages = [
                    {"role": "system", "content": NARRATION_INSTRUCTIONS},
                    {"role": "user", "content": json.dumps(receipt, ensure_ascii=False)},
                ]
                try:
                    completion = await budget.call(
                        lambda: model.complete(narration_messages, tools=TOOL_SCHEMAS, tool_choice="none"),
                        kind="narration", step_id=step_id, committed_revision=committed_revision,
                        request_options={"tool_choice": "none", "response_format": "text",
                                         "tool_schema_count": len(TOOL_SCHEMAS)})
                    message = validate_completion(completion)
                    text = message.get("content")
                    if message.get("tool_calls") or not isinstance(text, str) or not text.strip():
                        raise RunStopped("INVALID_NARRATION")
                    reply += "\n" + text.strip()
                    narration_status = "generated"
                except (RunStopped, TimeoutError) as error:
                    reason, narration_status = "narration_fallback", "fallback"
                    trace.emit("narration_fallback", "deterministic", step_id=step_id,
                               error_code=error.code if isinstance(error, RunStopped) else "TURN_TIMEOUT",
                               committed_revision=committed_revision)
                updated["history"][-1]["content"] = reply
    except asyncio.CancelledError as error:
        cancellation = error
        reason = "cancelled_after_commit" if committed_revision is not None else "cancelled"
        raise
    except TurnConflict:
        reason = "TURN_CONFLICT"
        raise
    except (RunStopped, ModelProtocolError, TimeoutError) as error:
        from app.runtime import AgentTurnError

        reason = (error.code if isinstance(error, RunStopped) else
                  "TURN_TIMEOUT" if isinstance(error, TimeoutError) else "MODEL_PROTOCOL_ERROR")
        # trace 在 finally 写完后再附到异常，确保包含最终状态。
        failure = AgentTurnError(reason, {})
        failure.trace = summary()
        raise failure from None
    finally:
        trace.finish(reason, model_requests=budget.model_requests, committed_revision=committed_revision)
        if record is not None and updated is not None and not replayed:
            engine.finish_turn(actor_id=expected_actor_id, turn_id=turn_id, session=updated,
                               reply=reply, trace=summary(), status=narration_status)
        if acquired:
            engine.turn_lock.release()
        # 保留失败 trace 的最终记录，无需为它再请求模型。
        if failure is not None:
            failure.trace = summary()
        if cancellation is not None:
            cancellation.trace = summary()
    return TurnResult(deepcopy(updated), reply, deepcopy(record["receipt"]), summary())
