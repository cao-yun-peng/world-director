"""单进程、顺序执行的提交点：世界与回合记录一起替换。"""

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict

from app.actions import ActionProposal, normalize_action
from app.world import WorldState, adjudicate, result


class TurnConflict(ValueError):
    pass


def request_digest(entry: str, value) -> str:
    wire = json.dumps({"entry": entry, "request": value}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


class WorldEngine:
    def __init__(self, world: WorldState):
        self._bundle = {"world": deepcopy(world), "turns": {}}

    @property
    def world(self) -> WorldState:
        return deepcopy(self._bundle["world"])

    @property
    def turns(self) -> dict:
        return deepcopy(self._bundle["turns"])

    def lookup(self, *, actor_id: str, turn_id: str, digest: str) -> dict | None:
        world = self._bundle["world"]
        if actor_id not in world.actor_locations:
            raise ValueError("未登记的可信行动者。")
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise ValueError("turn_id 必须非空。")
        key = (world.session_id, actor_id, turn_id)
        previous = self._bundle["turns"].get(key)
        if previous and previous["request_digest"] != digest:
            raise TurnConflict("同一 turn_id 已用于不同请求。")
        return deepcopy(previous)

    def submit_action(self, proposal: ActionProposal, *, actor_id: str, turn_id: str,
                      cause_event_id: str | None = None) -> dict:
        proposal = normalize_action(proposal)
        digest = request_digest("action", {"proposal": asdict(proposal), "cause_event_id": cause_event_id})
        previous = self.lookup(actor_id=actor_id, turn_id=turn_id, digest=digest)
        if previous is not None:
            return previous
        return self.commit_turn(proposal, [proposal], actor_id=actor_id, turn_id=turn_id,
                                digest=digest, cause_event_id=cause_event_id)

    def commit_turn(self, proposal: ActionProposal, operations: list[ActionProposal], *,
                    actor_id: str, turn_id: str, digest: str,
                    cause_event_id: str | None = None, feedback: list[dict] | None = None) -> dict:
        """operations/feedback 来自可信运行时，绝不直接消费模型提供的成功标志。"""
        previous = self.lookup(actor_id=actor_id, turn_id=turn_id, digest=digest)
        if previous is not None:
            return previous
        candidate = self.world
        receipts, events = [], []
        for operation in operations:
            candidate, receipt, emitted = adjudicate(
                candidate, operation, actor_id=actor_id, turn_id=turn_id, cause_event_id=cause_event_id)
            receipts.append(receipt)
            events.extend(emitted)
        if feedback is None:
            receipt = receipts[0]
        else:
            messages = []
            for item in feedback:
                if not item["ok"]:
                    messages.append(item["error"]["message"])
                elif "description" in item["data"]:
                    messages.append(item["data"]["description"])
                else:
                    scene = item["data"]
                    names = "、".join(obj["name"] for obj in scene["objects"]) or "暂无可见物品"
                    messages.append(f"当前位置 {scene['location_id']}；可见：{names}。")
            ok = all(item["ok"] for item in feedback) and all(item["ok"] for item in receipts)
            receipt = result(ok, "OBSERVED" if ok else "OBSERVATION_ERRORS", "\n".join(messages))
        record = {
            "request_digest": digest, "proposal": asdict(proposal),
            "receipt": receipt, "results": receipts, "event_ids": [e["event_id"] for e in events],
            "before_revision": self._bundle["world"].revision, "after_revision": candidate.revision,
            "reply": receipt["message"], "narration_status": "deterministic",
            "session": None, "trace": None,
        }
        turns = deepcopy(self._bundle["turns"])
        turns[(candidate.session_id, actor_id, turn_id)] = deepcopy(record)
        # 本日仅顺序调用。这是完整候选包的单次接纳，不是并发/持久化事务。
        self._bundle = {"world": candidate, "turns": turns}
        return deepcopy(record)

    def finish_turn(self, *, actor_id: str, turn_id: str, session: dict, reply: str, trace: dict, status: str) -> None:
        """只补回执与历史，不再改动已结算的世界。"""
        candidate = deepcopy(self._bundle)
        record = candidate["turns"][(candidate["world"].session_id, actor_id, turn_id)]
        record.update(session=deepcopy(session), reply=reply, trace=deepcopy(trace),
                      narration_status=status)
        self._bundle = candidate
