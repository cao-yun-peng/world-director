"""模型提议只表达意图；身份和事件前因由可信程序指定。"""

import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ActionProposal:
    kind: str
    target_text: str | None = None
    reply: str | None = None
    destination_id: str | None = None
    object_id: str | None = None
    recipient_id: str | None = None


def parse_action(text: str) -> ActionProposal:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("行动提议必须是有效 JSON。") from error
    if not isinstance(data, dict):
        raise ValueError("行动提议必须是对象。")
    kind = data.get("kind")
    if kind in ("move", "give"):
        fields = {"destination_id"} if kind == "move" else {"object_id", "recipient_id"}
        if set(data) != fields | {"kind"} or any(
                not isinstance(data[key], str) or not data[key].strip() for key in fields):
            raise ValueError("移动或给物字段不合法。")
        return ActionProposal(kind=kind, **{key: data[key].strip() for key in fields})
    if set(data) != {"kind", "target_text", "reply"}:
        raise ValueError("行动提议必须且只能包含 kind、target_text、reply。")
    target, reply = data["target_text"], data["reply"]
    if kind not in ("talk", "inspect", "clarify"):
        raise ValueError("未知行动类型。")
    if kind == "inspect":
        valid = isinstance(target, str) and bool(target.strip()) and reply is None
    else:
        valid = target is None and isinstance(reply, str) and bool(reply.strip())
    if not valid:
        raise ValueError("行动提议的字段类型或组合不合法。")
    return ActionProposal(kind, target.strip() if target else None, reply.strip() if reply else None)


def normalize_action(proposal: ActionProposal) -> ActionProposal:
    """直接 Python 入口也校验组合，不能绕过 JSON 入口的边界。"""
    if not isinstance(proposal, ActionProposal) or not isinstance(proposal.kind, str):
        raise ValueError("需要 ActionProposal。")
    fields = {
        "move": {"kind", "destination_id"},
        "give": {"kind", "object_id", "recipient_id"},
    }.get(proposal.kind, {"kind", "target_text", "reply"})
    data = asdict(proposal)
    if any(value is not None for key, value in data.items() if key not in fields):
        raise ValueError("行动包含不适用字段。")
    return parse_action(json.dumps({key: data[key] for key in fields}, ensure_ascii=False))
