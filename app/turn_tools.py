"""A04 终结工具：只解析行动提议，提交仍由 WorldEngine 负责。"""

import json

from app.actions import ActionProposal
from app.tools import TOOL_SCHEMAS


TERMINAL_TOOLS = {
    "end_turn": {
        "description": "回复玩家并结束本轮。已有信息足够时直接调用，无须先查询；也可澄清或总结必要的查询结果。只回答当前请求，不能宣称未执行的行动成功。",
        "properties": {"reply": {"type": "string", "minLength": 1, "description": "给玩家的非空角色回复。"}},
    },
    "move": {
        "description": "提议移动到当前可达地点，程序裁定后结束本轮。不能与其他工具同批调用。",
        "properties": {"destination_id": {"type": "string", "minLength": 1, "description": "当前可达地点的 ID。"}},
    },
    "give": {
        "description": "提议把自己持有的物品交给在场角色，程序裁定后结束本轮。不能与其他工具同批调用。",
        "properties": {
            "object_id": {"type": "string", "minLength": 1, "description": "已授权的物品 ID，不是知识记录编号。"},
            "recipient_id": {"type": "string", "minLength": 1, "description": "在场且已登记的接收角色 ID。"},
        },
    },
}

TURN_TOOL_SCHEMAS = TOOL_SCHEMAS + [
    {"type": "function", "function": {
        "name": name, "description": spec["description"],
        "parameters": {"type": "object", "properties": spec["properties"],
                       "required": list(spec["properties"]), "additionalProperties": False},
    }}
    for name, spec in TERMINAL_TOOLS.items()
]


def parse_terminal_call(call: dict) -> ActionProposal:
    """调用外壳由入口校验；此处统一校验终结工具的必填非空字符串参数。"""
    name = call["function"]["name"]
    try:
        arguments = json.loads(call["function"]["arguments"])
    except json.JSONDecodeError:
        raise ValueError("工具参数必须是 JSON 对象。") from None
    fields = TERMINAL_TOOLS[name]["properties"]
    if (not isinstance(arguments, dict) or set(arguments) != set(fields)
            or any(not isinstance(value, str) or not value.strip() for value in arguments.values())):
        raise ValueError("必须且只能提供工具定义中的字段，值为非空字符串。")
    arguments = {key: value.strip() for key, value in arguments.items()}
    return ActionProposal("talk" if name == "end_turn" else name, **arguments)


# A05 扩展沿用 A04 的终结批次约束；旧课程的工具集合保持原样。
WHISPER_TOOL = {"type": "function", "function": {
    "name": "whisper", "description": "向同地的一个角色私语并结束回合。只投递 reply 原文；来源必须自己可见，不读取父事件。",
    "parameters": {"type": "object", "properties": {
        "recipient_id": {"type": "string", "minLength": 1},
        "reply": {"type": "string", "minLength": 1},
        "cause_event_id": {"type": ["string", "null"], "description": "转述所据的可见事件 ID；原创发言用 null。"}},
        "required": ["recipient_id", "reply", "cause_event_id"], "additionalProperties": False}}}
MEMORY_TOOL_SCHEMAS = TURN_TOOL_SCHEMAS + [WHISPER_TOOL]


def parse_memory_terminal(call):
    if call["function"]["name"] != "whisper":
        return parse_terminal_call(call)
    from app.actions import parse_action
    try:
        arguments = json.loads(call["function"]["arguments"])
    except json.JSONDecodeError:
        raise ValueError("私语参数必须是 JSON 对象。") from None
    if not isinstance(arguments, dict) or set(arguments) != {"recipient_id", "reply", "cause_event_id"}:
        raise ValueError("私语必须且只能提供接收者、正文和来源。")
    return parse_action(json.dumps({"kind": "statement", **arguments}, ensure_ascii=False))
