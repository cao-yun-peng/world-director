"""模型提议只负责表达意图；校验后才交给运行时消费。"""

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ActionProposal:
    kind: str
    target_text: str | None
    reply: str | None


def parse_action(text: str) -> ActionProposal:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("行动提议必须是有效 JSON。") from error
    if not isinstance(data, dict) or set(data) != {"kind", "target_text", "reply"}:
        raise ValueError("行动提议必须且只能包含 kind、target_text、reply。")
    kind, target, reply = data["kind"], data["target_text"], data["reply"]
    if kind not in ("talk", "inspect", "clarify"):
        raise ValueError("未知行动类型。")
    if kind == "inspect":
        valid = isinstance(target, str) and bool(target.strip()) and reply is None
    else:
        valid = target is None and isinstance(reply, str) and bool(reply.strip())
    if not valid:
        raise ValueError("行动提议的字段类型或组合不合法。")
    return ActionProposal(kind, target.strip() if target else None, reply.strip() if reply else None)
