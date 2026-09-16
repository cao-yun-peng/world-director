"""A03 世界账本与纯裁定。这里不调用模型、不写文件、不修改输入快照。"""

import json
from copy import deepcopy
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType

from app.actions import ActionProposal, normalize_action


@dataclass
class WorldState:
    session_id: str
    locations: dict[str, list[str]]
    actor_locations: dict[str, str]
    objects: dict[str, dict]
    owners: dict[str, str]
    knowledge: dict[str, list[dict]]
    revision: int = 0
    events: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class WorldSnapshot:
    """A04 查询持有的递归只读视图，与可提交的 WorldState 分离。"""

    session_id: str
    locations: MappingProxyType
    actor_locations: MappingProxyType
    objects: MappingProxyType
    owners: MappingProxyType
    knowledge: MappingProxyType
    revision: int
    events: tuple


def freeze_world(state: WorldState) -> WorldSnapshot:
    def freeze(value):
        if isinstance(value, dict):
            return MappingProxyType({key: freeze(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(freeze(item) for item in value)
        return value

    return WorldSnapshot(**{item.name: freeze(getattr(state, item.name)) for item in fields(state)})


def create_world(session_id: str) -> WorldState:
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("会话 ID 必须非空。")
    data = json.loads(Path(__file__).with_name("objects.json").read_text(encoding="utf-8"))
    objects = {}
    owners = {}
    for raw in data["objects"]:
        obj = deepcopy(raw)
        owners[obj["id"]] = "location:" + obj.pop("location_id")
        objects[obj["id"]] = obj
    owners["envelope_01"] = "actor:lin_yan"
    return WorldState(
        session_id=session_id,
        locations={"duty_room": ["storage_room"], "storage_room": ["duty_room"]},
        actor_locations=deepcopy(data["actor_locations"]),
        objects=objects, owners=owners,
        knowledge={actor: [] for actor in data["actor_locations"]},
    )


def object_location(state: WorldState, object_id: str) -> str:
    owner_type, owner_id = state.owners[object_id].split(":", 1)
    return state.actor_locations[owner_id] if owner_type == "actor" else owner_id


def inventory(state: WorldState, actor_id: str) -> list[str]:
    return [object_id for object_id, owner in state.owners.items() if owner == "actor:" + actor_id]


def result(ok: bool, code: str, message: str, **data) -> dict:
    """玩家可见回执白名单；内部检查值和后台资料不进入这里。"""
    return {"ok": ok, "code": code, "message": message, "data": data}


def adjudicate(state: WorldState, proposal: ActionProposal, *, actor_id: str, turn_id: str,
               cause_event_id: str | None = None) -> tuple[WorldState, dict, list[dict]]:
    from app.tools import ObjectUnavailable, get_visible_scene, inspect_object

    proposal = normalize_action(proposal)
    if actor_id not in state.actor_locations:
        raise ValueError("未登记的可信行动者。")
    if not isinstance(turn_id, str) or not turn_id.strip():
        raise ValueError("turn_id 必须非空。")

    def reject(code, message):
        return deepcopy(state), result(False, code, message), []

    # 在构造候选变更前检查引用，并限制在同一世界/会话。
    parent = None
    if cause_event_id is not None:
        parent = next((event for event in state.events
                       if event["event_id"] == cause_event_id
                       and event["session_id"] == state.session_id), None)
        if parent is None:
            return reject("INVALID_CAUSE", "事件前因无效。")

    location = state.actor_locations[actor_id]
    checks = {"actor_location": location}
    changes = {}
    discovery = None
    kind = proposal.kind
    if kind in ("talk", "clarify"):
        return deepcopy(state), result(True, kind.upper(), proposal.reply), []

    if kind == "move":
        destination = proposal.destination_id
        checks.update(destination=destination, adjacent=destination in state.locations[location])
        if destination not in state.locations or not checks["adjacent"]:
            return reject("INVALID_MOVE", "无法移动到该地点。")
        changes = {"actor_location": {"actor_id": actor_id, "before": location, "after": destination}}
        receipt = result(True, "MOVED", f"已移动到 {destination}。", location_id=destination)

    elif kind == "give":
        object_id, recipient = proposal.object_id, proposal.recipient_id
        # 隐藏、未知、异地对象先统一屏蔽；不借归属检查暴露隐藏对象。
        try:
            inspect_object(object_id, actor_id=actor_id, world=state)
        except ObjectUnavailable:
            return reject("OBJECT_UNAVAILABLE", "当前无法操作该对象。")
        if recipient not in state.actor_locations or recipient == actor_id:
            return reject("INVALID_RECIPIENT", "接收者无效。")
        owner = state.owners[object_id]
        checks.update(object_id=object_id, owner=owner, recipient_id=recipient,
                      recipient_location=state.actor_locations[recipient])
        if owner != "actor:" + actor_id:
            return reject("NOT_OWNER", "你未持有该物品。")
        if state.actor_locations[recipient] != location:
            return reject("NOT_COLOCATED", "接收者不在同一地点。")
        changes = {"owner": {"object_id": object_id, "before": owner, "after": "actor:" + recipient}}
        receipt = result(True, "GIVEN", f"已将{state.objects[object_id]['name']}交给 {recipient}。",
                         object_id=object_id, recipient_id=recipient)

    elif kind == "inspect":
        object_id = proposal.target_text
        if object_id == "当前场景":
            return deepcopy(state), result(True, "SCENE", "已查看当前场景目录。",
                                           scene=get_visible_scene(actor_id=actor_id, world=state)), []
        try:
            visible = inspect_object(object_id, actor_id=actor_id, world=state)
        except ObjectUnavailable:
            return reject("OBJECT_UNAVAILABLE", "当前无法查看该对象。")
        checks.update(object_id=object_id, accessible=True, owner=state.owners[object_id])
        # 只有观察自己当前持有的物品时，才以建立这次持有的转移为前因。
        # 中间夹有移动/其他观察时也不能把“上一事件”当作原因。
        if cause_event_id is None and state.owners[object_id] == "actor:" + actor_id:
            parent = next((event for event in reversed(state.events)
                           if event["session_id"] == state.session_id and event["kind"] == "TransferEvent"
                           and event["changes"]["owner"]["object_id"] == object_id
                           and event["changes"]["owner"]["after"] == "actor:" + actor_id), None)
        discovery = {"observer_id": actor_id, "object_id": object_id,
                     "facts": [visible["description"]],
                     "source_id": f"objects.json#{object_id}.description"}
        receipt = result(True, "DISCOVERED", visible["description"], **visible)
    else:
        raise ValueError("未知行动。")

    updated = deepcopy(state)
    event_id = f"{state.session_id}:E{len(state.events) + 1:04d}"
    if kind == "move":
        updated.actor_locations[actor_id] = proposal.destination_id
    elif kind == "give":
        updated.owners[proposal.object_id] = "actor:" + proposal.recipient_id
    else:
        discovery["event_id"] = event_id
        updated.knowledge[actor_id].append(deepcopy(discovery))
        changes = {"knowledge_added": deepcopy(discovery)}
    updated.revision += 1
    event = {
        "event_id": event_id, "session_id": state.session_id, "turn_id": turn_id, "actor_id": actor_id,
        "kind": {"move": "MovementEvent", "give": "TransferEvent", "inspect": "DiscoveryEvent"}[kind],
        "preconditions": checks, "changes": changes,
        "before_revision": state.revision, "after_revision": updated.revision,
        "cause_event_id": parent["event_id"] if parent else None,
    }
    if discovery:
        event.update(deepcopy(discovery))
    updated.events.append(deepcopy(event))
    return updated, receipt, [deepcopy(event)]
