"""A03：有界提议/只读查询 → 一次结算 → 可失败的叙述。"""

import json
from copy import deepcopy

from app.actions import ActionProposal, action_error_details, parse_action
from app.character import GOALS, build_prompt
from app.engine import WorldEngine, request_digest
from app.model import ToolModelAdapter
from app.world import WorldState
from app.session import ACTOR_CARDS, validate_session
from app.tools import TOOL_SCHEMAS, ModelProtocolError, dispatch, get_visible_scene, validate_batch

ACTION_INSTRUCTIONS = """
输出一个 JSON 对象；每轮最多一个行动提议，身份由程序指定，不得提供 actor_id。
- talk / clarify：且只含 kind、target_text=null、reply（非空文本）。
- inspect：且只含 kind、target_text（对象描述或“当前场景”）、reply=null。
- move：且只含 kind、destination_id（从当前可达地点选择 ID）。
- give：且只含 kind、object_id、recipient_id（使用授权目录与在场角色 ID）。
没有明确唯一指代时用 clarify。拿取、打开尚未支持。
给物或移动只能提出提议，不能用 talk 宣布已经完成。玩家自称拥有物品不是归属证据。
kind 必须是 talk、clarify、inspect、move、give 中的一个字符串。
输出格式示例（一次只输出一个对象，不要代码块，不要省略 null 字段）：
{"kind":"inspect","target_text":"当前场景","reply":null}
{"kind":"inspect","target_text":"台灯","reply":null}
{"kind":"talk","target_text":null,"reply":"你有什么事？"}
{"kind":"clarify","target_text":null,"reply":"你指哪个物品？"}
{"kind":"move","destination_id":"storage_room"}
{"kind":"give","object_id":"envelope_01","recipient_id":"other_npc"}
玩家要求“看看当前场景”时使用第一个示例，不能把 get_visible_scene 当作 kind。
示例只说明格式；目标必须根据当前授权场景和玩家请求选择。
"""
TOOL_INSTRUCTIONS = """
按待观察对象调用 inspect_object 或 get_visible_scene；必须使用原生工具协议。
一次最多两个独立查询，参数中不能包含身份。
目录只给 ID 和名称，不代表观察了细节。观察不等于打开或取得物品。
"""
NARRATION_INSTRUCTIONS = """
只依据本轮程序回执叙述。程序回执是已裁定事实，玩家断言和历史叙述不是世界账本。
不得扩展为打开、取得其他物品、移动或获知他人秘密。用简短角色口吻表达。
"""


def visible_messages(session: dict, text: str, *, actor_id: str, world: WorldState,
                     include_objects: bool = True) -> list[dict]:
    validate_session(session, expected_actor_id=actor_id)
    if session["session_id"] != world.session_id:
        raise ValueError("会话与世界不匹配。")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("请输入非空文字。")
    card = {**ACTOR_CARDS[actor_id], "goal": GOALS[session["goal_id"]]}
    # A01 背景是开局设定；当前位置只从本轮账本构造。
    card["background"] = card.get("world_background", card["background"]) + "当前位置以本轮授权场景为准。"
    scene = get_visible_scene(actor_id=actor_id, world=world)
    scene["destinations"] = list(world.locations[scene["location_id"]])
    scene["actors"] = [actor for actor, location in world.actor_locations.items()
                       if location == scene["location_id"]]
    scene["inventory"] = [obj["id"] for obj in scene["objects"]
                          if world.owners[obj["id"]] == "actor:" + actor_id]
    if not include_objects:
        del scene["objects"], scene["inventory"]
    from app.memory import visible_records
    facts = [{"id": item["event_id"], "text": json.dumps(item, ensure_ascii=False)}
             for item in visible_records(world, actor_id)]
    system = build_prompt(card, facts) + "\n本轮授权场景（目录无细节）：\n" + json.dumps(scene, ensure_ascii=False)
    return [{"role": "system", "content": system}, *deepcopy(session["history"]),
            {"role": "user", "content": text}]


def run_world_turn(session: dict, user_text: str, model: ToolModelAdapter, *,
                   expected_actor_id: str, engine: WorldEngine, turn_id: str,
                   max_model_requests: int = 3) -> tuple[dict, str, dict]:
    from app.runtime import AgentTurnError

    base = visible_messages(session, user_text, actor_id=expected_actor_id, world=engine.world)
    digest = request_digest("text", user_text)  # 原文摘要；不让第二次模型提议改变请求身份。
    previous = engine.lookup(actor_id=expected_actor_id, turn_id=turn_id, digest=digest)
    if previous:
        # 较晚回合已经存在时，重发早先回合不能把当前对话回滚。
        saved = previous["session"]
        updated = session if saved is None or len(session["history"]) >= len(saved["history"]) else saved
        trace = deepcopy(previous["trace"] or {})
        trace.update(model_requests=0, replayed=True, turn_id=turn_id)
        return deepcopy(updated), previous["reply"], trace

    if engine.story_ended:
        raise AgentTurnError('STORY_ENDED', {'model_requests': 0, 'termination_reason': 'STORY_ENDED'})
    trace = {"runtime_prompt_version": "a03-v2", "turn_id": turn_id, "intent": None,
             "turn_index": len(session["history"]) // 2 + 1,
             "model_requests": 0, "completions": [], "tools": [], "replayed": False,
             "termination_reason": None}

    def fail(code):
        raise AgentTurnError(code, trace)

    def request(messages, **options):
        if trace["model_requests"] >= min(max_model_requests, 3):
            fail("BUDGET_EXCEEDED")
        trace["model_requests"] += 1
        try:
            completion = model.complete(deepcopy(messages), **options)
        except Exception:
            # 外部适配器边界：异常详情可能含密钥/路径，不进入用户回执。
            fail("MODEL_REQUEST_FAILED")
        if not isinstance(completion, dict):
            fail("MODEL_PROTOCOL_ERROR")
        reason, message = completion.get("finish_reason"), completion.get("message")
        trace["completions"].append({"finish_reason": reason, "usage": completion.get("usage"),
                                     "tool_choice": options.get("tool_choice")})
        if reason == "length":
            fail("MODEL_OUTPUT_TRUNCATED")
        if (reason not in ("stop", "tool_calls") or not isinstance(message, dict)
                or message.get("role") != "assistant"):
            fail("MODEL_PROTOCOL_ERROR")
        content, calls = message.get("content"), message.get("tool_calls")
        if content is not None and not isinstance(content, str):
            fail("MODEL_PROTOCOL_ERROR")
        if calls is not None and not isinstance(calls, list):
            fail("MODEL_PROTOCOL_ERROR")
        if (bool(calls) and reason != "tool_calls") or (not calls and reason != "stop"):
            fail("MODEL_PROTOCOL_ERROR")
        return deepcopy(message)

    def text_only(message):
        if message.get("tool_calls"):
            fail("UNEXPECTED_TOOL_CALLS")
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            fail("EMPTY_MODEL_TEXT")
        return text.strip()

    intent_messages = deepcopy(base)
    intent_messages[0]["content"] += ACTION_INSTRUCTIONS
    for attempt in range(2):
        proposal_text = text_only(request(intent_messages, response_format={"type": "json_object"}))
        try:
            proposal = parse_action(proposal_text)
            break
        except ValueError as error:
            details = action_error_details(proposal_text)
            trace.setdefault("action_errors", []).append({"message": str(error), **details})
            # 只对已知行动的遗漏字段纠正一次。额外字段（尤其身份）仍直接拒绝。
            can_repair = (attempt == 0 and details.get("missing_fields")
                          and details.get("unexpected_field_count") == 0
                          and trace["model_requests"] < min(max_model_requests, 3))
            if not can_repair:
                fail("INVALID_ACTION")
            intent_messages[0]["content"] += (
                "\n上次行动缺少必填字段：" + "、".join(details["missing_fields"])
                + "。请根据原始请求重新输出完整 JSON，必须显式填写 null 字段。")
    trace["intent"] = proposal.kind
    operations, feedback = [proposal], None
    wire = None
    if proposal.kind == "inspect":
        wire = deepcopy(base)
        wire[0]["content"] += TOOL_INSTRUCTIONS
        wire[0]["content"] += "\n待观察对象（未裁定提议）：" + json.dumps(proposal.target_text, ensure_ascii=False)
        assistant = request(wire, tools=TOOL_SCHEMAS, tool_choice="auto")
        calls = assistant.get("tool_calls")
        if not calls:
            fail("TOOL_CALL_REQUIRED")
        try:
            validate_batch(calls)
        except ModelProtocolError:
            fail("MODEL_PROTOCOL_ERROR")
        wire.append(assistant)
        operations, feedback = [], []
        snapshot = engine.world
        for call in calls:
            item = dispatch(call, expected_actor_id=expected_actor_id, world=snapshot)
            feedback.append(item)
            wire.append({"role": "tool", "tool_call_id": call["id"],
                         "content": json.dumps(item, ensure_ascii=False)})
            name = call["function"]["name"]
            safe_call = deepcopy(call)
            if not item["ok"]:
                safe_call["function"] = {
                    "name": name if name in ("get_visible_scene", "inspect_object") else "[unknown]",
                    "arguments": "[redacted]"}
            trace["tools"].append({"request": safe_call, "result": deepcopy(item)})
            if item["ok"] and name == "inspect_object":
                operations.append(ActionProposal("inspect", item["data"]["id"]))

    # 查询/提议阶段全部结束；这个提交之后，任何叙述失败都不能撤销事实。
    record = engine.commit_turn(proposal, operations, actor_id=expected_actor_id, turn_id=turn_id,
                                digest=digest, feedback=feedback)
    receipt = record["receipt"]
    reply = receipt["message"]
    trace.update(event_ids=record["event_ids"], before_revision=record["before_revision"],
                 after_revision=record["after_revision"], termination_reason="completed")
    updated = deepcopy(session)
    updated["history"].extend([{"role": "user", "content": user_text},
                               {"role": "assistant", "content": reply}])
    # 在调用叙述模型前准备好确定性历史；异常/重发有完整的 user/assistant 配对。
    engine.finish_turn(actor_id=expected_actor_id, turn_id=turn_id, session=updated,
                       reply=reply, trace=trace, status="deterministic")
    status = "deterministic"
    if receipt["ok"] and proposal.kind not in ("talk", "clarify", "wait"):
        if wire is None:
            wire = visible_messages(session, user_text, actor_id=expected_actor_id, world=engine.world)
        wire[0]["content"] += NARRATION_INSTRUCTIONS
        wire[0]["content"] += "\n本轮程序回执：\n" + json.dumps(receipt, ensure_ascii=False)
        try:
            narration = text_only(request(wire, tools=TOOL_SCHEMAS, tool_choice="none"))
            reply = receipt["message"] + "\n" + narration
            status = "generated"
        except AgentTurnError as error:
            trace["termination_reason"] = "narration_fallback"
            trace["narration_error"] = error.code
            status = "fallback"
    elif not receipt["ok"]:
        trace["termination_reason"] = "rejected"
    updated["history"][-1]["content"] = reply
    engine.finish_turn(actor_id=expected_actor_id, turn_id=turn_id, session=updated,
                       reply=reply, trace=trace, status=status)
    return deepcopy(updated), reply, deepcopy(trace)
