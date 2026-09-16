"""六类场景的可读离线证据；与 unittest 独立复跑。"""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

from app.async_runtime import run_agent_turn
from app.engine import TurnConflict, WorldEngine
from app.execution import RunLimits
from app.runtime import AgentTurnError
from app.session import create_session
from app.world import create_world
from scripts.a04_demo import ScriptedModel, call, decision, response


async def collect(output_dir=Path("docs")):
    async def slow(messages):
        await asyncio.Event().wait()
    cases = [
        ("normal", [response(calls=[call()]), decision(), response("已查看。")], RunLimits()),
        ("bad_arguments", [response(calls=[call(arguments="{}")]), decision()], RunLimits()),
        ("unauthorized", [response(calls=[call(object_id="box_01")]), decision()], RunLimits()),
        ("timeout", [slow], replace(RunLimits(), turn_timeout_s=0.05)),
        ("loop", [response(calls=[call()])] * 5, RunLimits()),
        ("duplicate", [decision("give", object_id="envelope_01", recipient_id="other_npc"), response("给你。")], RunLimits()),
    ]
    rows, records = [], []
    for name, responses, limits in cases:
        session = create_session(actor_id="lin_yan", goal_id="clarify")
        engine = WorldEngine(create_world(session["session_id"]))
        before = engine.world
        async def run(responses, text="固定教学请求"):
            return await run_agent_turn(session, text, ScriptedModel(responses), expected_actor_id="lin_yan",
                                        engine=engine, turn_id="T1", limits=limits)
        try:
            result = await run(responses)
            trace = result.trace
        except AgentTurnError as error:
            trace = error.trace
        checks = {}
        if name == "duplicate":
            first = engine.world
            replay = await run([])
            checks["replay_requests"] = replay.trace["model_requests"]
            checks["replay_world_unchanged"] = engine.world == first
            records.extend(replay.trace["records"])
            try:
                await run([], "同 ID 不同请求")
            except TurnConflict:
                checks["changed_request_rejected"] = True
            assert checks == {"replay_requests": 0, "replay_world_unchanged": True, "changed_request_rejected": True}
        elif name != "normal":
            checks["world_unchanged"] = engine.world == before
            assert checks["world_unchanged"]
        after = engine.world
        rows.append({"case": name, "mode": "fake", "run_id": trace["run_id"], "turn_id": "T1",
                     "termination_reason": trace["termination_reason"], "model_requests": trace["model_requests"],
                     "before_revision": before.revision, "after_revision": after.revision,
                     "events_before": len(before.events), "events_after": len(after.events), **checks})
        records.extend(trace["records"])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "a04_scenarios.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "a04_fault_trace.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    return rows


if __name__ == "__main__":
    for row in asyncio.run(collect()):
        print(f"{row['case']}: {row['termination_reason']} requests={row['model_requests']} "
              f"revision={row['before_revision']}->{row['after_revision']}")
