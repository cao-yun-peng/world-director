"""只读工具：模型声明与本地执行分开，身份只由调用方注入。"""

import json
from pathlib import Path

from app.scene_data import KNOWN_ACTORS

_WORLD = json.loads(Path(__file__).with_name("objects.json").read_text(encoding="utf-8"))
ACTOR_LOCATIONS = _WORLD["actor_locations"]
OBJECTS = _WORLD["objects"]


class ModelProtocolError(ValueError):
    """没有可靠调用 ID 或协议外壳时，整批终止，不能伪造工具结果。"""


class ObjectUnavailable(ValueError):
    pass


def accessible_objects(actor_id: str) -> list[dict]:
    if actor_id not in KNOWN_ACTORS or actor_id not in ACTOR_LOCATIONS:
        raise ValueError("未登记角色或角色没有当前位置。")
    return [obj for obj in OBJECTS
            if obj["location_id"] == ACTOR_LOCATIONS[actor_id]
            and (obj.get("visibility") == "public"
                 or (obj.get("visibility") == "private"
                     and isinstance(obj.get("known_by"), list)
                     and all(isinstance(actor, str) for actor in obj["known_by"])
                     and actor_id in obj["known_by"]))]


def get_visible_scene(*, actor_id: str) -> dict:
    objects = accessible_objects(actor_id)
    return {"location_id": ACTOR_LOCATIONS[actor_id],
            "objects": [{"id": obj["id"], "name": obj["name"]} for obj in objects]}


def inspect_object(object_id: str, *, actor_id: str) -> dict:
    for obj in accessible_objects(actor_id):
        if obj["id"] == object_id:
            return {key: obj[key] for key in ("id", "name", "description")}
    raise ObjectUnavailable("当前无法查看该对象。")


TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_visible_scene",
        "description": "查询当前地点与有权看到的物品目录，只返回 ID 和名称，不返回细节。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "inspect_object",
        "description": "查看当前地点有权查看的物品细节。只观察，不移动、取得或打开物品。",
        "parameters": {
            "type": "object",
            "properties": {"object_id": {"type": "string", "minLength": 1,
                "description": "从已授权目录选择物品 ID，不是物品名称。"}},
            "required": ["object_id"], "additionalProperties": False,
        },
    }},
]


def validate_call(call: dict) -> None:
    if (not isinstance(call, dict) or set(call) != {"id", "type", "function"}
            or call.get("type") != "function"
            or not isinstance(call.get("id"), str) or not call["id"].strip()):
        raise ModelProtocolError("工具调用缺少有效 ID 或类型。")
    function = call.get("function")
    if (not isinstance(function, dict) or set(function) != {"name", "arguments"}
            or not isinstance(function.get("name"), str)
            or not function["name"].strip() or not isinstance(function.get("arguments"), str)):
        raise ModelProtocolError("工具调用的 function 协议不完整。")


def validate_batch(calls: list[dict]) -> None:
    if not isinstance(calls, list) or not 1 <= len(calls) <= 2:
        raise ModelProtocolError("每批必须包含 1 至 2 个工具调用。")
    ids = set()
    for call in calls:
        validate_call(call)
        if call["id"] in ids:
            raise ModelProtocolError("同批工具调用 ID 重复。")
        ids.add(call["id"])


def dispatch(call: dict, *, expected_actor_id: str) -> dict:
    validate_call(call)
    call_id = call["id"]

    def failure(code: str, message: str) -> dict:
        return {"call_id": call_id, "ok": False, "data": None,
                "error": {"code": code, "message": message}}

    # 显式白名单；模型提供的名字永远不能变成任意 Python 调用。
    handlers = {"get_visible_scene": get_visible_scene, "inspect_object": inspect_object}
    name = call["function"]["name"]
    if name not in handlers:
        return failure("UNKNOWN_TOOL", "不支持该工具。")
    try:
        arguments = json.loads(call["function"]["arguments"])
    except json.JSONDecodeError:
        return failure("INVALID_JSON", "工具参数不是有效 JSON。")
    required = {"object_id"} if name == "inspect_object" else set()
    if not isinstance(arguments, dict) or set(arguments) != required:
        return failure("INVALID_ARGUMENTS", "工具参数字段不合法。")
    if name == "inspect_object" and (not isinstance(arguments["object_id"], str)
                                     or not arguments["object_id"].strip()):
        return failure("INVALID_ARGUMENTS", "object_id 必须是非空字符串。")
    try:
        data = handlers[name](**arguments, actor_id=expected_actor_id)
    except ObjectUnavailable:
        return failure("OBJECT_UNAVAILABLE", "当前无法查看该对象。")
    except Exception:
        # 仅工具执行边界屏蔽内部异常；错误状态会进入轨迹，不能冒充成功。
        return failure("TOOL_ERROR", "工具执行失败。")
    return {"call_id": call_id, "ok": True, "data": data, "error": None}
