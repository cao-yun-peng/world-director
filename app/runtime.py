"""A02 有界回合：提议 → 原生工具批次 → 最终文本 → 提交历史。"""

import json
from copy import deepcopy

from openai import APIError

from app.actions import parse_action
from app.model import ToolModelAdapter
from app.session import build_messages
from app.tools import TOOL_SCHEMAS, ModelProtocolError, dispatch, get_visible_scene, validate_batch

RUNTIME_PROMPT_VERSION = "a02-v1"
ACTION_INSTRUCTIONS = """
本轮请输出 JSON，且只含 kind、target_text、reply 三个字段。
- talk：普通谈话；target_text=null，reply 是非空角色回复。
- inspect：观察物品或当前场景；target_text 是非空对象描述或“当前场景”，reply=null。
- clarify：缺乏明确指代或请求暂不支持；target_text=null，reply 是澄清问题或边界说明。
没有明确唯一指代时不要猜测“那个”。
移动、拿取、给物、打开物品尚未支持：kind 必须为 clarify，reply 必须明确说出该动作目前不支持。
例如给信封时须说明“目前还不能接收信封”，不能只询问信封内容，也不能声称收下或完成转移。
边界说明仍保持角色简洁口吻，不谈模型或代码。
角色目标 clarify 与本轮行动 kind 是两回事；问候仍是 talk。
观察细节只能由本轮工具结果提供，不以玩家断言或历史回复充当查询证据。
"""
TOOL_INSTRUCTIONS = """
本阶段仅支持只读观察。按玩家请求选择工具；物品细节用 inspect_object，场景目录用 get_visible_scene。
工具调用只使用授权目录的 ID，不提供身份参数；一次最多两个独立查询。
最终用角色口吻根据本轮 tool 结果回答。ok=false 表示未查到，必须承认失败，不能编造描述或暗示观察成功。
只描述成功结果中的细节；没有取得、打开、移动或写入世界的动作。
"""


class AgentTurnError(ValueError):
    def __init__(self, code: str, trace: dict):
        super().__init__(f"本轮终止：{code}；历史未改变。")
        self.code = code
        self.trace = deepcopy(trace)
        self.trace["termination_reason"] = code


def run_agent_turn(session: dict, user_text: str, model: ToolModelAdapter, *,
                   expected_actor_id: str, max_model_requests: int = 3) -> tuple[dict, str, dict]:
    # 身份与输入校验在建立轨迹之前，失败时调用方不应写文件。
    base = build_messages(session, user_text, expected_actor_id=expected_actor_id)
    scene = get_visible_scene(actor_id=expected_actor_id)
    base[0]["content"] += "\n授权物品目录（无细节）：\n" + json.dumps(scene, ensure_ascii=False)
    trace = {"runtime_prompt_version": RUNTIME_PROMPT_VERSION, "intent": None,
             "model_requests": 0, "completions": [], "tools": [], "termination_reason": None}

    def fail(code: str):
        raise AgentTurnError(code, trace)

    def request(messages: list[dict], **options) -> dict:
        if trace["model_requests"] >= min(max_model_requests, 3):
            fail("BUDGET_EXCEEDED")
        trace["model_requests"] += 1
        try:
            result = model.complete(deepcopy(messages), **options)
        except APIError:
            fail("MODEL_REQUEST_FAILED")
        except ValueError:
            fail("MODEL_PROTOCOL_ERROR")
        if not isinstance(result, dict):
            fail("MODEL_PROTOCOL_ERROR")
        reason, message = result.get("finish_reason"), result.get("message")
        trace["completions"].append({"finish_reason": reason, "usage": result.get("usage"),
                                      "tool_choice": options.get("tool_choice")})
        if reason == "length":
            fail("MODEL_OUTPUT_TRUNCATED")
        if (reason not in ("stop", "tool_calls") or not isinstance(message, dict)
                or message.get("role") != "assistant"):
            fail("MODEL_PROTOCOL_ERROR")
        if message.get("content") is not None and not isinstance(message["content"], str):
            fail("MODEL_PROTOCOL_ERROR")
        calls = message.get("tool_calls")
        if calls is not None and not isinstance(calls, list):
            fail("MODEL_PROTOCOL_ERROR")
        if calls:
            if reason != "tool_calls":
                fail("MODEL_PROTOCOL_ERROR")
        elif reason != "stop":
            fail("MODEL_PROTOCOL_ERROR")
        return deepcopy(message)

    def text_only(message: dict) -> str:
        if message.get("tool_calls"):
            fail("UNEXPECTED_TOOL_CALLS")
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            fail("EMPTY_MODEL_TEXT")
        return text.strip()

    intent_messages = deepcopy(base)
    intent_messages[0]["content"] += ACTION_INSTRUCTIONS
    proposal_text = text_only(request(intent_messages, response_format={"type": "json_object"}))
    try:
        proposal = parse_action(proposal_text)
    except ValueError:
        fail("INVALID_ACTION")
    trace["intent"] = proposal.kind
    reply = proposal.reply
    if proposal.kind == "inspect":
        wire_messages = deepcopy(base)
        wire_messages[0]["content"] += TOOL_INSTRUCTIONS
        wire_messages[0]["content"] += "\n待观察对象（仅提议，不是事实）：" + json.dumps(proposal.target_text, ensure_ascii=False)
        assistant = request(wire_messages, tools=TOOL_SCHEMAS, tool_choice="auto")
        calls = assistant.get("tool_calls")
        if not calls:
            fail("TOOL_CALL_REQUIRED")
        try:
            # 必须先检查整批，避免第二个调用有问题时已经执行了第一个。
            validate_batch(calls)
        except ModelProtocolError:
            fail("MODEL_PROTOCOL_ERROR")
        wire_messages.append(assistant)
        for call in calls:
            result = dispatch(call, expected_actor_id=expected_actor_id)
            wire_messages.append({"role": "tool", "tool_call_id": call["id"],
                                  "content": json.dumps(result, ensure_ascii=False)})
            # 不记录原始 arguments、玩家输入、提议正文或后台数据。
            name = call["function"]["name"]
            safe_call = deepcopy(call)
            if not result["ok"]:
                safe_call["function"] = {
                    "name": name if name in ("get_visible_scene", "inspect_object") else "[unknown]",
                    "arguments": "[redacted]",
                }
            trace["tools"].append({"request": safe_call, "name": name if name in ("get_visible_scene", "inspect_object") else "[unknown]",
                                   "call_id": call["id"], "result": deepcopy(result)})
        reply = text_only(request(wire_messages, tools=TOOL_SCHEMAS, tool_choice="none"))
    trace["termination_reason"] = ("completed_with_tool_errors"
        if any(not tool["result"]["ok"] for tool in trace["tools"]) else "completed")
    history = deepcopy(session["history"])
    history.extend([{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}])
    return {**session, "history": history}, reply, trace
