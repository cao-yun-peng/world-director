"""明确标记 Fake 的九次连续互动；不读取 .env、不联网。"""

import asyncio
import inspect
import json
from copy import deepcopy
from pathlib import Path

from app.async_runtime import run_agent_turn
from app.engine import WorldEngine
from app.execution import RunLimits
from app.session import create_session
from app.world import create_world


def response(content=None, calls=None, *, reason=None):
    return {"message": {"role": "assistant", "content": content,
                        **({"tool_calls": calls} if calls is not None else {})},
            "finish_reason": reason or ("tool_calls" if calls else "stop"), "usage": None}


def decision(kind="finish", **fields):
    return response(json.dumps({"kind": kind, **fields}, ensure_ascii=False))


def call(name="inspect_object", *, object_id="lamp_01", call_id="q1", arguments=None):
    if arguments is None:
        arguments = json.dumps({"object_id": object_id} if name == "inspect_object" else {})
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


class ScriptedModel:
    """响应可为字典、异常或读取实际 messages 的函数；输入按次深复制。"""

    mode = "fake"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    async def complete(self, messages, **options):
        self.requests.append(deepcopy({"messages": messages, **options}))
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(deepcopy(messages))
        if inspect.isawaitable(item):
            item = await item
        return deepcopy(item)


def inspect_from_directory(messages):
    """后一次 object_id 来自本轮工具返回，替换教学 ID 仍可工作。"""
    data = json.loads(messages[-1]["content"])["data"]
    return response(calls=[call(object_id=data["objects"][0]["id"], call_id="details")])


async def demo(output_dir: Path = Path("docs")) -> list[dict]:
    session = create_session(actor_id="lin_yan", goal_id="clarify")
    engine = WorldEngine(create_world(session["session_id"]))
    give = decision("give", object_id="envelope_01", recipient_id="other_npc")
    scenarios = [
        ("T1", "你好。", [decision("talk", target_text=None, reply="晚上好。")]),
        ("T2", "查看 lamp_01 的底座。", [response(calls=[call()]), decision(), response("底座上有编号。")]),
        ("T3", "比较台灯与信封外观。", [response(calls=[call(), call(object_id="envelope_01", call_id="q2")]),
                                        decision(), response("两件物品的外观已查明。")]),
        ("T4", "把信封交给 other_npc。", [give, response("给你。")]),
        ("T4", "把信封交给 other_npc。", []),
        ("T6", "再把信封交给 other_npc。", [give]),
        ("T7", "移动到储物间。", [decision("move", destination_id="storage_room"), response("到了。")]),
        ("T8", "先查当前目录，再选第一件物品看细节。", [response(calls=[call("get_visible_scene")]),
                 inspect_from_directory, decision(), response("目录中的物品已查看。")]),
        ("T9", "查看一个我还没说明的物品。", [decision("clarify", target_text=None, reply="请说明是哪件物品。")]),
    ]
    evidence, records = [], []
    for turn_id, text, responses in scenarios:
        before = engine.world
        result = await run_agent_turn(session, text, ScriptedModel(responses), expected_actor_id="lin_yan",
                                      engine=engine, turn_id=turn_id, limits=RunLimits(retry_delay_s=0))
        session = result.session
        after = engine.world
        evidence.append({"mode": "fake", "session_id": session["session_id"], "turn_id": turn_id,
                         "run_id": result.trace["run_id"], "input": text, "output": result.reply,
                         "model_requests": result.trace["model_requests"],
                         "termination_reason": result.trace["termination_reason"],
                         "before_revision": before.revision, "after_revision": after.revision,
                         "event_count": len(after.events), "envelope_owner": after.owners["envelope_01"],
                         "location": after.actor_locations["lin_yan"]})
        records.extend(result.trace["records"])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "a04_demo.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "a04_trace_sample.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    return evidence


if __name__ == "__main__":
    for item in asyncio.run(demo()):
        print(f"{item['turn_id']} {item['termination_reason']} requests={item['model_requests']} "
              f"revision={item['before_revision']}->{item['after_revision']} events={item['event_count']}")
