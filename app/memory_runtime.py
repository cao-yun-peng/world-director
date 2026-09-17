"""A05 故事入口：调用方只选择已登记角色，历史容器由程序持有。"""

import asyncio
from copy import deepcopy
from dataclasses import replace
from app.execution import RunLimits
from app.runtime import AgentTurnError
from uuid import uuid4

from app.async_runtime import run_agent_turn
from app.engine import WorldEngine, request_digest
from app.memory import SummaryCache
from app.session import create_actor_conversations
from app.world import create_world


class MemoryStory:
    def __init__(self, world=None):
        world = create_world(str(uuid4())) if world is None else world
        self.engine = WorldEngine(world)
        self._conversations = create_actor_conversations(world)
        self._summaries = SummaryCache()
        self._lock = asyncio.Lock()

    @property
    def conversations(self):
        return deepcopy(self._conversations)

    async def turn(self, actor_id, text, model, *, turn_id, **options):
        if actor_id not in self._conversations:
            raise ValueError("未登记的对话角色。")
        # 排队也计入同一回合总时限；拿锁后把剩余秒数交给 A04。
        limits = options.pop("limits", None) or RunLimits()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limits.turn_timeout_s
        shared = options.get('shared_budget')
        if shared is not None:
            deadline = min(deadline, shared.deadline)
        try:
            async with asyncio.timeout_at(deadline):
                await self._lock.acquire()
        except TimeoutError:
            raise AgentTurnError("TURN_TIMEOUT", {"model_requests": 0, "termination_reason": "TURN_TIMEOUT",
                                                   "trace_write_failed": False, "records": []}) from None
        try:
            summary = self._summaries.get(self.engine.world, actor_id)
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AgentTurnError("TURN_TIMEOUT", {"model_requests": 0, "termination_reason": "TURN_TIMEOUT",
                                                       "trace_write_failed": False, "records": []})
            options["limits"] = replace(limits, turn_timeout_s=remaining)
            try:
                result = await run_agent_turn(
                    self._conversations[actor_id], text, model, expected_actor_id=actor_id,
                    engine=self.engine, turn_id=turn_id, memory_mode=True,
                    memory_summary=summary, **options)
            except asyncio.CancelledError:
                record = self.engine.lookup(actor_id=actor_id, turn_id=turn_id,
                                            digest=request_digest("text", text))
                if record and record["session"] is not None:
                    self._conversations[actor_id] = deepcopy(record["session"])
                raise
            self._conversations[actor_id] = deepcopy(result.session)
            return result
        finally:
            self._lock.release()
