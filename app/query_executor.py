"""只读执行器：先校验整批，再执行；结果顺序和实际完成顺序分别保留。"""

import asyncio
import json
from copy import deepcopy
from time import monotonic

from app.execution import CallFailure, RunBudget
from app.tools import dispatch, validate_batch, validate_call
from app.trace import call_reference


class ReadonlyExecutor:
    def __init__(self, max_parallel_tools: int = 2, *, wait_for_read=None, lore=None,
                 recipient_id='player', scenario_id='handover-v1'):
        if type(max_parallel_tools) is not int or max_parallel_tools < 1:
            raise ValueError("并发数必须是正整数。")
        self.max_parallel_tools = max_parallel_tools
        self.semaphore = asyncio.Semaphore(max_parallel_tools)
        # 可信适配边界：本课用 Event 假查询模拟 I/O；本地字典不需要线程。
        self.wait_for_read = wait_for_read
        self.lore = lore
        self.recipient_id = recipient_id
        self.scenario_id = scenario_id

    async def execute_one(self, call: dict, *, snapshot, actor_id: str, budget: RunBudget, step_id: int):
        validate_call(call)
        budget.check()
        if call['function']['name'] == 'search_lore' and self.lore is not None:
            return await self.search_lore(call, actor_id=actor_id, budget=budget, step_id=step_id,
                                          snapshot_revision=snapshot.revision)
        # 名称、参数和授权统一复用旧入口；坏调用不会进入重试或等待接口。
        item = dispatch(call, expected_actor_id=actor_id, world=snapshot)
        name = call["function"]["name"]
        metadata = dict(tool_name=name if name in ("inspect_object", "get_visible_scene") else "[unknown]",
                        call_id=call_reference(call["id"]), snapshot_revision=snapshot.revision)
        if not item["ok"]:
            budget.trace.emit("tool", "rejected", step_id=step_id, attempt=1,
                              span_id=f"{step_id}:{metadata['call_id']}",
                              parent_span_id=budget.trace.run_id,
                              error_code=item["error"]["code"], **metadata)
            return item

        async def read():
            if self.wait_for_read is not None:
                await self.wait_for_read(deepcopy(call), snapshot)
            return deepcopy(item)

        try:
            return await budget.call(read, kind="tool", step_id=step_id,
                                     semaphore=self.semaphore, **metadata)
        except CallFailure as error:
            return {"call_id": call["id"], "ok": False, "data": None,
                    "error": {"code": error.code, "message": "查询暂时未完成。"}}

    async def search_lore(self, call, *, actor_id, budget, step_id, snapshot_revision):
        from app.lore import validate_query

        def failure(code):
            budget.trace.emit('tool', 'rejected', step_id=step_id, tool_name='search_lore',
                              snapshot_revision=snapshot_revision, error_code=code)
            return {'call_id': call['id'], 'ok': False, 'data': None,
                    'error': {'code': code, 'message': '资料查询未完成。'}}
        try:
            query, top_k = validate_query(json.loads(call['function']['arguments']))
        except (ValueError, TypeError):
            return failure('INVALID_ARGUMENTS')
        try:
            # 编码本身计费/重试；不再嵌套重试整个检索。
            async with asyncio.timeout_at(budget.deadline):
                async with self.semaphore:
                    data = await self.lore.search(query, top_k, actor_id=actor_id,
                        recipient_id=self.recipient_id, scenario_id=self.scenario_id,
                        budget=budget, step_id=step_id)
            data.pop('diagnostics', None)
            budget.trace.emit('lore_retrieval', data['status'], step_id=step_id,
                retrieval_mode=data['mode'], lore_query=query, snapshot_revision=snapshot_revision,
                lore_refs=[{k: v for k, v in hit.items() if k not in ('text', 'title')} for hit in data['hits']])
            return {'call_id': call['id'], 'ok': True, 'data': data, 'error': None}
        except (CallFailure, ValueError):
            return failure('RETRIEVAL_UNAVAILABLE')

    async def execute_batch(self, calls, *, snapshot, actor_id: str, budget: RunBudget, step_id: int):
        validate_batch(calls)  # 一个无效 ID 就整批拒绝，不先执行前半批。
        if len(calls) > budget.limits.max_tool_calls_per_batch:
            from app.tools import ModelProtocolError
            raise ModelProtocolError("查询数量超过本轮限制。")
        tasks = [asyncio.create_task(self.execute_one(call, snapshot=snapshot, actor_id=actor_id,
                                                     budget=budget, step_id=step_id)) for call in calls]
        try:
            # gather 返回输入顺序；每个工具的 trace 在它实际完成时记录。
            return await asyncio.gather(*tasks)
        finally:
            started = monotonic()
            pending = [task for task in tasks if not task.done()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if pending:
                budget.trace.emit("cleanup", "completed", step_id=step_id,
                                  cleanup_ms=round((monotonic() - started) * 1000, 3))
