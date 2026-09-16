"""A04 单角色循环：查询候选 → 一次提交 → 可失败的叙述。"""

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from app.actions import ActionProposal
from app.engine import TurnConflict, WorldEngine, request_digest
from app.execution import RunBudget, RunLimits, RunStopped
from app.model import AsyncToolModelAdapter
from app.query_executor import ReadonlyExecutor
from app.session import validate_session
from app.tools import ModelProtocolError, TOOL_SCHEMAS, validate_batch
from app.trace import RunTrace, call_reference
from app.turn_tools import TERMINAL_TOOLS, TURN_TOOL_SCHEMAS, parse_terminal_call
from app.world import append_statement, freeze_world
from app.memory import build_actor_context
from app.turn_tools import MEMORY_TOOL_SCHEMAS, parse_memory_terminal
from app.world_runtime import NARRATION_INSTRUCTIONS, visible_messages

RUNTIME_VERSION = "a04-v6.1"

LOOP_INSTRUCTIONS = """
本轮所有决策都使用原生 Function Calling，包括最终回复。普通正文不会结束回合。
先判断当前请求需要什么信息。角色卡、对话和已有事实足以答复时，直接调用 end_turn，不需要先查询。
只有缺少完成当前请求所必需的世界事实时才查询；不为充实回复而主动探索或介绍无关物品。
需要查询物品但尚无授权 ID 时，先查 get_visible_scene；已有授权 ID 时可直接 inspect_object。
目录足以回答时就结束；只有请求涉及物品细节时才继续 inspect_object，不逐一查看目录中的全部物品。
每批最多两个独立只读工具；依赖前一次结果的查询放到下一步。知识记录编号不是物品 ID。
查询不会立即写入世界；成功读取的细节在终结时由程序统一裁定。
可以直接答复或已完成必要查询时，单独调用一个终结工具：
- end_turn(reply)：回答、澄清或总结已查询事实，reply 使用角色语言。
- move(destination_id)：提议移动到当前可达地点。
- give(object_id, recipient_id)：提议把自己持有的物品交给在场角色。
终结工具必须独占一个批次。每轮最多一个移动或给物提议，裁定成功或拒绝均结束本轮。
身份只由程序指定，不得提供 actor_id。未明确玩家对应的角色 ID 时先澄清，不猜测接收者。
拿取、打开尚未支持。玩家声称已拥有/已移动不是事实，不要用回复宣称行动完成。
工具错误可以据实澄清或修正参数后再调用，遵守原有步数和请求预算。
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


async def decide(wire: list[dict], model: AsyncToolModelAdapter, *, snapshot,
                 actor_id: str, executor: ReadonlyExecutor, budget: RunBudget, memory_mode=False):
    discoveries = {}
    for step_id in range(1, budget.limits.max_steps + 1):
        completion = await budget.call_model(
            model, wire, options={"tools": MEMORY_TOOL_SCHEMAS if memory_mode else TURN_TOOL_SCHEMAS, "tool_choice": "required"},
            kind="model", step_id=step_id, snapshot_revision=snapshot.revision)
        message = validate_completion(completion)
        calls = message.get("tool_calls")
        if not calls:
            raise RunStopped("TOOL_CALL_REQUIRED")
        validate_batch(calls)
        if len(calls) > budget.limits.max_tool_calls_per_batch:
            raise ModelProtocolError("工具数量超过本轮限制。")
        terminal_names = set(TERMINAL_TOOLS) | ({"whisper"} if memory_mode else set())
        terminal = [call for call in calls if call["function"]["name"] in terminal_names]
        if terminal:
            # 在执行任何查询之前检查整批，避免一边结束一边仍在查询。
            if len(calls) != 1:
                raise RunStopped("TERMINAL_TOOL_CONFLICT")
            call = terminal[0]
            metadata = dict(tool_name=call["function"]["name"], call_id=call_reference(call["id"]),
                            step_id=step_id, snapshot_revision=snapshot.revision,
                            span_id=f"terminal-{step_id}", parent_span_id=budget.trace.run_id)
            try:
                proposal = parse_memory_terminal(call) if memory_mode else parse_terminal_call(call)
            except ValueError as error:
                budget.trace.emit("terminal_tool", "rejected", error_code="INVALID_ARGUMENTS", **metadata)
                items = [{"call_id": call["id"], "ok": False, "data": None,
                          "error": {"code": "INVALID_ARGUMENTS", "message": str(error)}}]
            else:
                budget.trace.emit("terminal_tool", "validated", decision_kind=proposal.kind, **metadata)
                return proposal, list(discoveries.values()), step_id
        else:
            items = await executor.execute_batch(calls, snapshot=snapshot, actor_id=actor_id,
                                                 budget=budget, step_id=step_id)
        wire.append(message)
        for call, item in zip(calls, items, strict=True):
            wire.append({"role": "tool", "tool_call_id": call["id"],
                         "content": json.dumps(item, ensure_ascii=False)})
            if item["ok"] and call["function"]["name"] == "inspect_object":
                object_id = item["data"]["id"]
                # 同轮重复查询可以消耗预算，但持久发现只记录一次。
                discoveries[object_id] = ActionProposal("inspect", object_id)
    raise RunStopped("STEP_LIMIT")


async def run_agent_turn(session: dict, user_text: str, model: AsyncToolModelAdapter, *,
                         expected_actor_id: str, engine: WorldEngine, turn_id: str,
                         limits: RunLimits | None = None, executor: ReadonlyExecutor | None = None,
                         trace_path: Path | None = None, memory_mode: bool = False,
                         memory_summary: dict | None = None) -> TurnResult:
    validate_session(session, expected_actor_id=expected_actor_id)
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("请输入非空文字。")
    if not isinstance(turn_id, str) or not turn_id.strip():
        raise ValueError("turn_id 必须非空。")
    limits = limits or RunLimits()
    if memory_mode and limits.max_input_chars is None:
        limits = replace(limits, max_input_chars=8000)
    trace = RunTrace(session["session_id"], turn_id, mode=model.mode, path=trace_path)
    budget = RunBudget(limits, trace)
    executor = executor or ReadonlyExecutor(limits.max_parallel_tools)
    if executor.max_parallel_tools > limits.max_parallel_tools:
        raise ValueError("执行器并发数超过本轮上限。")
    trace.emit("run_started", "started", model_requests=0, runtime_version="a05-v1" if memory_mode else RUNTIME_VERSION,
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
        if session["session_id"] != snapshot.session_id or expected_actor_id not in snapshot.actor_locations:
            raise ValueError("会话与世界角色不匹配。")
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
            if memory_mode:
                # E1 只在临时候选上预览，提交前失败不会污染正式世界。
                preview, incoming, _ = append_statement(
                    engine.world, speaker_id="player", recipient_id=expected_actor_id, text=user_text,
                    turn_id=turn_id, channel="player_dialogue")
                budget.context = build_actor_context(preview, session, expected_actor_id, user_text,
                                                     summary=memory_summary)
                wire = deepcopy(budget.context.wire)
                wire[0]["content"] += "\n本轮玩家私语候选来源：" + incoming["data"]["event_id"]
            else:
                wire = visible_messages(session, user_text, actor_id=expected_actor_id,
                                        world=snapshot, include_objects=False)
            wire[0]["content"] += LOOP_INSTRUCTIONS
            proposal, discoveries, step_id = await decide(
                wire, model, snapshot=snapshot, actor_id=expected_actor_id, executor=executor, budget=budget,
                memory_mode=memory_mode)
            budget.check()
            operations = discoveries + [proposal]
            trace.emit("adjudication", "started", step_id=step_id, snapshot_revision=snapshot.revision)
            record = engine.commit_turn(proposal, operations, actor_id=expected_actor_id, turn_id=turn_id,
                                        digest=digest,
                                        expected_revision=snapshot.revision, before_accept=budget.check,
                                        player_text=user_text if memory_mode else None)
            committed_revision = record["after_revision"]
            receipt = record["receipt"]
            reason = "completed" if receipt["ok"] else "rejected"
            trace.emit("commit", reason, step_id=step_id, snapshot_revision=snapshot.revision,
                       committed_revision=committed_revision, error_code=None if receipt["ok"] else receipt["code"])
            reply = receipt["message"]
            updated = deepcopy(session)
            if not memory_mode or receipt["ok"]:
                updated["history"].extend([{"role": "user", "content": user_text},
                                           {"role": "assistant", "content": reply}])
            # 在下一个 await 前把确定性结果与完整历史放进幂等缓存。
            engine.finish_turn(actor_id=expected_actor_id, turn_id=turn_id, session=updated,
                               reply=reply, trace=summary(), status=narration_status)
            if receipt["ok"] and proposal.kind not in ("talk", "clarify", "statement"):
                # 叙述只看程序回执；不携带未裁定的候选工具上下文。
                narration_messages = [
                    {"role": "system", "content": NARRATION_INSTRUCTIONS},
                    {"role": "user", "content": json.dumps(receipt, ensure_ascii=False)},
                ]
                if memory_mode:
                    current_context = build_actor_context(engine.world, session, expected_actor_id, "依据已提交回执叙述。")
                    narration_messages[0]["content"] = current_context.wire[0]["content"] + NARRATION_INSTRUCTIONS
                try:
                    completion = await budget.call_model(
                        model, narration_messages, options={"tools": TOOL_SCHEMAS, "tool_choice": "none"},
                        kind="narration", step_id=step_id, committed_revision=committed_revision)
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
