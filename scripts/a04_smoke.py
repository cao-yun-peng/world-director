"""显式 --real 才执行的九次真实互动；失败也计数并保存证据。"""

import argparse
import asyncio
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.async_runtime import run_agent_turn
from app.engine import WorldEngine
from app.execution import RunLimits
from app.loop_cli import ROOT, load_model
from app.query_executor import ReadonlyExecutor
from app.runtime import AgentTurnError
from app.session import create_session
from app.world import create_world

PROMPTS = [
    "你好。",
    "请查看 lamp_01 的底座。",
    "比较 lamp_01 和 envelope_01 的外观。",
    "把 envelope_01 交给 other_npc。",
    None,  # 使用上一请求原文和 turn_id 重发。
    "再次把 envelope_01 交给 other_npc。",
    "移动到 storage_room。",
    "先查询当前目录，再选择目录中的第一件物品查看细节，告诉我结果。",
    "帮我查看一件我还没有告诉你名称的物品。",
]


def state_summary(world):
    return {"revision": world.revision, "event_count": len(world.events),
            "location": world.actor_locations["lin_yan"], "envelope_owner": world.owners["envelope_01"],
            "discovery_count": len(world.knowledge["lin_yan"])}


async def smoke(max_requests: int, output: Path) -> int:
    session = create_session(actor_id="lin_yan", goal_id="clarify")
    engine = WorldEngine(create_world(session["session_id"]))
    limits = RunLimits()
    executor = ReadonlyExecutor(limits.max_parallel_tools)
    rows, remaining, last_turn = [], max_requests, None
    output.parent.mkdir(parents=True, exist_ok=True)
    model = load_model()
    report = {"mode": "real", "model": model.model, "started_at": datetime.now(timezone.utc).isoformat(),
              "max_model_requests": max_requests, "limits": asdict(limits), "rows": rows,
              "acceptance": "needs_review"}
    try:
        for prompt in PROMPTS:
            if prompt is None:
                turn_id, prompt = last_turn
            else:
                turn_id = str(uuid4())
                last_turn = turn_id, prompt
            before = state_summary(engine.world)
            try:
                result = await run_agent_turn(session, prompt, model, expected_actor_id="lin_yan",
                    engine=engine, turn_id=turn_id, executor=executor,
                    limits=replace(limits, max_model_requests=min(8, remaining)),
                    trace_path=ROOT / "runs" / "a04_real.jsonl")
                session, reply, trace = result.session, result.reply, result.trace
                receipt_code = result.receipt["code"]
            except AgentTurnError as error:
                reply, trace, receipt_code = None, error.trace, None
            remaining -= trace["model_requests"]
            rows.append({"session_id": session["session_id"], "turn_id": turn_id,
                         "run_id": trace["run_id"], "input": prompt, "output": reply,
                         "receipt_code": receipt_code, "model_requests": trace["model_requests"],
                         "termination_reason": trace["termination_reason"],
                         "trace_write_failed": trace["trace_write_failed"],
                         "before": before, "after": state_summary(engine.world)})
            print(f"interaction={len(rows)} reason={trace['termination_reason']} remaining={remaining}")
    finally:
        report["actual_model_requests"] = model.request_count
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        try:
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            await model.aclose()
    # 真实语言、依赖查询与剧情仍需人工阅读；脚本成功不自动判阶段通过。
    return 0 if all(r["termination_reason"] in ("completed", "replayed", "rejected") for r in rows) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="明确启用真实 API 调用")
    parser.add_argument("--max-model-requests", type=int, default=48)
    parser.add_argument("--output", type=Path, default=ROOT / "runs" / "a04_real_evidence.json")
    args = parser.parse_args(argv)
    if not args.real:
        parser.error("真实调用必须显式传 --real；离线演示请运行 scripts.a04_demo。")
    if not 1 <= args.max_model_requests <= 48:
        parser.error("真实演示预算必须在 1—48 之间。")
    try:
        return asyncio.run(smoke(args.max_model_requests, args.output))
    except KeyboardInterrupt:
        return 130
    except ValueError as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
