"""独立变式的观测台：先预测，再改 PARALLEL；请自己写出解释。"""

import asyncio
import json

from app.execution import RunBudget, RunLimits
from app.query_executor import ReadonlyExecutor
from app.trace import RunTrace
from app.world import create_world, freeze_world
from scripts.a04_demo import call

PARALLEL = 2  # 学习者练习：改成 1，再比较峰值、结果和快照版本。


async def experiment(parallel: int = PARALLEL):
    active, peak = 0, 0
    events = asyncio.Queue()
    releases = [asyncio.Event() for _ in range(5)]

    async def wait(query, snapshot):
        nonlocal active, peak
        index = int(query["id"])
        active += 1
        peak = max(peak, active)
        events.put_nowait(index)
        try:
            await releases[index].wait()
        finally:
            active -= 1

    executor = ReadonlyExecutor(parallel, wait_for_read=wait)
    snapshot = freeze_world(create_world("parallel-exercise"))
    trace = RunTrace("parallel-exercise", "T1", mode="fake")
    budget = RunBudget(RunLimits(max_parallel_tools=parallel), trace)
    tasks = [asyncio.create_task(executor.execute_one(call(call_id=str(index)), snapshot=snapshot,
                                  actor_id="lin_yan", budget=budget, step_id=1)) for index in range(5)]
    try:
        # 先等名额全部占满，形成真实重叠；再逐个放行，不用微小 sleep 猜并发。
        initial = [await asyncio.wait_for(events.get(), 2) for _ in range(min(parallel, 5))]
        for index in initial:
            releases[index].set()
        for _ in range(5 - len(initial)):
            releases[await asyncio.wait_for(events.get(), 2)].set()
        results = await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return {"mode": "fake", "max_parallel_tools": parallel, "max_active": peak, "active_after": active,
            "call_ids": [item["call_id"] for item in results], "all_ok": all(item["ok"] for item in results),
            "snapshot_revision": snapshot.revision,
            "descriptions": [item["data"]["description"] for item in results]}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(experiment()), ensure_ascii=False, indent=2))
