"""D021 离线连续故事。只使用固定 FakeModel，不读取 .env、不联网。"""

import json
from copy import deepcopy

from app.actions import ActionProposal
from app.engine import WorldEngine
from app.runtime import run_agent_turn
from app.session import create_session
from app.tools import get_visible_scene
from app.world import create_world


class DemoModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def complete(self, messages, **options):
        self.requests.append(deepcopy({"messages": messages, **options}))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)


def text_response(text):
    return {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}


def propose(**fields):
    return text_response(json.dumps(fields, ensure_ascii=False))


def observe(object_id):
    return [
        propose(kind="inspect", target_text=object_id, reply=None),
        {"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "inspect_1", "type": "function", "function": {
                "name": "inspect_object", "arguments": json.dumps({"object_id": object_id})}}
        ]}, "finish_reason": "tool_calls"},
        text_response("我看到了这些细节。"),
    ]


def snapshot(engine):
    world = engine.world
    return {
        "revision": world.revision, "event_count": len(world.events),
        "actor_locations": world.actor_locations, "owners": world.owners,
        "knowledge": world.knowledge,
        "visible_scene": get_visible_scene(actor_id="lin_yan", world=world),
    }


def run_demo():
    session = create_session(actor_id="lin_yan", goal_id="clarify")
    session["session_id"] = "a03-demo"
    engine = WorldEngine(create_world(session["session_id"]))
    report = {"mode": "offline-scripted", "steps": []}

    def step(label, turn_id, text, responses):
        nonlocal session
        before = snapshot(engine)
        old_events = len(engine.world.events)
        model = DemoModel(responses)
        session, reply, trace = run_agent_turn(
            session, text, model, expected_actor_id="lin_yan",
            engine=engine, turn_id=turn_id)
        report["steps"].append({
            "step": label, "turn_id": turn_id, "before": before, "after": snapshot(engine),
            "new_events": engine.world.events[old_events:], "reply": reply,
            "model_requests": len(model.requests), "termination_reason": trace["termination_reason"],
            "replayed": trace["replayed"], "history_messages": len(session["history"]),
        })

    step("1 观察台灯", "T001", "观察台灯", observe("lamp_01"))
    assert len(engine.world.knowledge["lin_yan"]) == 1
    assert engine.world.knowledge["other_npc"] == []
    transfer = propose(kind="give", object_id="envelope_01", recipient_id="other_npc")
    step("2 转移信封", "T002", "把信封给 other_npc", [transfer, text_response("信封交给你。")])
    assert engine.world.owners["envelope_01"] == "actor:other_npc"
    before = engine.world
    step("3 原回合重发", "T002", "把信封给 other_npc", [])
    assert engine.world == before
    assert report["steps"][-1]["model_requests"] == 0
    step("4 新回合再次给物", "T003", "把信封给 other_npc", [transfer])
    assert engine.world == before
    step("5 移动到储物间", "T004", "去储物间", [
        propose(kind="move", destination_id="storage_room"), text_response("到了。")])
    assert get_visible_scene(actor_id="lin_yan", world=engine.world)["objects"] == [
        {"id": "cabinet_01", "name": "木柜"}]
    step("6 观察木柜", "T005", "观察木柜", observe("cabinet_01"))
    step("7 已移动但叙述失败", "T006", "回值班室", [
        propose(kind="move", destination_id="duty_room"), RuntimeError("故意模拟叙述超时")])
    assert engine.world.actor_locations["lin_yan"] == "duty_room"
    assert report["steps"][-1]["termination_reason"] == "narration_fallback"
    before = engine.world
    step("7b 重发失败叙述回合", "T006", "回值班室", [])
    assert engine.world == before
    assert report["steps"][-1]["model_requests"] == 0

    # other_npc 无对话角色卡；用可信直接行动入口验收受赠后的观察。
    before = snapshot(engine)
    receipt = engine.submit_action(ActionProposal("inspect", "envelope_01"),
                                    actor_id="other_npc", turn_id="R001")
    event = engine.world.events[-1]
    assert event["cause_event_id"] == "a03-demo:E0002"
    assert len(engine.world.knowledge["lin_yan"]) == 2
    assert len(engine.world.knowledge["other_npc"]) == 1
    report["receiver_discovery"] = {"turn_id": "R001", "before": before,
                                    "after": snapshot(engine), "event": event, "result": receipt}
    report["history"] = session["history"]
    report["assertions"] = "passed"
    return report


if __name__ == "__main__":
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))
