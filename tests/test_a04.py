"""A04：控制数据流与等待事件，不依赖真实模型或 .env。"""

import asyncio
import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.actions import ActionProposal
from app.async_runtime import run_agent_turn
from app.engine import TurnConflict, WorldEngine
from app.execution import RunBudget, RunLimits, RunStopped, TransientFailure
from app.model import AsyncRealModelAdapter
from app.query_executor import ReadonlyExecutor
from app.runtime import AgentTurnError
from app.session import create_session
from app.trace import RunTrace, call_reference
from app.world import create_world, freeze_world
from scripts.a04_demo import ScriptedModel, call, terminal, inspect_from_directory, response


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = create_session(actor_id="lin_yan", goal_id="clarify")
        self.engine = WorldEngine(create_world(self.session["session_id"]))
        self.limits = RunLimits(retry_delay_s=0)
        self.give = terminal("give", object_id="envelope_01", recipient_id="other_npc")

    async def run_turn(self, responses, *, text="教学请求", turn_id="T1", **options):
        self.model = ScriptedModel(responses)
        return await run_agent_turn(self.session, text, self.model, expected_actor_id="lin_yan",
                                    engine=self.engine, turn_id=turn_id,
                                    limits=options.pop("limits", self.limits), **options)

    def assert_untouched(self, before):
        self.assertEqual(self.engine.world, before)
        self.assertEqual(self.engine.turns, {})
        self.assertEqual(self.session["history"], [])

    async def test_direct_id_and_native_message_pairing(self):
        result = await self.run_turn([response(calls=[call()]), terminal(reply="看到了编号。")],
                                     text="查看 lamp_01。")
        self.assertEqual(result.trace["model_requests"], 2)
        wire = self.model.requests[1]["messages"]
        self.assertEqual(wire[-2]["tool_calls"][0]["id"], wire[-1]["tool_call_id"])
        self.assertEqual(json.loads(wire[-1]["content"])["data"]["id"], "lamp_01")
        self.assertEqual(self.engine.world.knowledge["other_npc"], [])
        self.assertEqual(len(self.engine.world.events), 1)
        self.assertEqual(self.session["history"], [])

    async def test_directory_dependency_follows_changed_object_id(self):
        world = self.engine.world
        world.objects["changed_id"] = world.objects.pop("lamp_01")
        world.objects["changed_id"]["id"] = "changed_id"
        world.owners["changed_id"] = world.owners.pop("lamp_01")
        # 把教学对象放在首位，Fake 的后续选择仍只读真实目录。
        world.objects = {"changed_id": world.objects["changed_id"], **world.objects}
        self.engine = WorldEngine(world)
        await self.run_turn([response(calls=[call("get_visible_scene")]), inspect_from_directory,
                             terminal(reply="已查看。")])
        self.assertNotIn("changed_id", json.dumps(self.model.requests[0]))
        query = self.model.requests[2]["messages"][-2]["tool_calls"][0]
        self.assertEqual(json.loads(query["function"]["arguments"])["object_id"], "changed_id")
        self.assertEqual(self.engine.world.knowledge["lin_yan"][0]["object_id"], "changed_id")

    async def test_step_limit_drops_all_pending_discoveries(self):
        before = self.engine.world
        with self.assertRaises(AgentTurnError) as caught:
            await self.run_turn([response(calls=[call()])] * 5)
        self.assertEqual(caught.exception.code, "STEP_LIMIT")
        self.assertEqual(len(self.model.requests), 4)
        self.assertEqual(caught.exception.trace["records"][-1]["kind"], "run_finished")
        self.assert_untouched(before)

    async def test_request_limit_no_summary_call(self):
        before = self.engine.world
        with self.assertRaises(AgentTurnError) as caught:
            await self.run_turn([response(calls=[call()]), terminal(reply="已查看。")],
                                limits=replace(self.limits, max_model_requests=1))
        self.assertEqual(caught.exception.code, "MODEL_REQUEST_LIMIT")
        self.assertEqual(len(self.model.requests), 1)
        self.assert_untouched(before)

    async def test_retry_counts_real_sends_within_same_step(self):
        result = await self.run_turn([TransientFailure(), terminal(reply="你好")])
        records = [r for r in result.trace["records"] if r["kind"] == "model"]
        self.assertEqual([(r["step_id"], r["attempt"]) for r in records], [(1, 1), (1, 2)])
        self.assertEqual(records[0]["span_id"], records[1]["span_id"])
        self.assertEqual(result.trace["model_requests"], 2)
        self.assertEqual(records[-1]["usage"], None)

    async def test_retry_exhaustion_and_permanent_failure(self):
        for failure, count in [(TransientFailure(), 2), (RuntimeError("secret-key"), 1)]:
            with self.subTest(count=count):
                before = self.engine.world
                with self.assertRaises(AgentTurnError) as caught:
                    await self.run_turn([failure] * 3)
                self.assertEqual(len(self.model.requests), count)
                self.assertNotIn("secret-key", str(caught.exception.trace))
                self.assert_untouched(before)

    async def test_retry_after_respects_total_budget(self):
        with self.assertRaises(AgentTurnError) as caught:
            await self.run_turn([TransientFailure(retry_after_s=60)])
        self.assertEqual(caught.exception.code, "RETRY_BUDGET_EXHAUSTED")
        self.assertEqual(len(self.model.requests), 1)

    async def test_attempt_timeout_can_retry_but_total_deadline_is_fixed(self):
        async def stuck(messages):
            await asyncio.Event().wait()
        # 留足调度余量；仍要求第一次超时后重试，第二次被原总 deadline 截止。
        limits = replace(self.limits, turn_timeout_s=1.5, model_attempt_timeout_s=1.0)
        before = self.engine.world
        with self.assertRaises(AgentTurnError) as caught:
            await self.run_turn([stuck, stuck], limits=limits)
        self.assertEqual(caught.exception.code, "TURN_TIMEOUT")
        self.assertEqual(len(self.model.requests), 2)
        self.assert_untouched(before)

    async def test_slow_query_rolls_back_and_reclaims_tasks(self):
        ended = asyncio.Event()
        async def slow(call, snapshot):
            try:
                await asyncio.Event().wait()
            finally:
                ended.set()
        before = self.engine.world
        with self.assertRaises(AgentTurnError) as caught:
            await self.run_turn([response(calls=[call()])], executor=ReadonlyExecutor(wait_for_read=slow),
                                limits=replace(self.limits, turn_timeout_s=0.05))
        self.assertEqual(caught.exception.code, "TURN_TIMEOUT")
        self.assertTrue(ended.is_set())
        self.assert_untouched(before)

    async def test_entire_batch_rejected_before_any_tool_runs(self):
        waiter = AsyncMock()
        broken = call(call_id="q2")
        del broken["id"]
        for calls in ([call(), broken], [call(), call()], [call()] * 3):
            with self.subTest(calls=calls):
                before = self.engine.world
                with self.assertRaises(AgentTurnError):
                    await self.run_turn([response(calls=calls)], executor=ReadonlyExecutor(wait_for_read=waiter))
                self.assert_untouched(before)
        waiter.assert_not_awaited()

    async def test_bad_arguments_and_access_denials_are_not_retried(self):
        cases = [("{", "INVALID_JSON"), ("{}", "INVALID_ARGUMENTS"),
                 ('{"object_id":"box_01"}', "OBJECT_UNAVAILABLE"),
                 ('{"object_id":"lamp_01","actor_id":"other_npc"}', "INVALID_ARGUMENTS")]
        for index, (arguments, code) in enumerate(cases):
            with self.subTest(code=code):
                waiter = AsyncMock()
                before = self.engine.world
                result = await self.run_turn([response(calls=[call(arguments=arguments)]), terminal(reply="目前未能查看，请明确物品。")],
                                             turn_id=f"bad-{index}", executor=ReadonlyExecutor(wait_for_read=waiter))
                waiter.assert_not_awaited()
                self.assertEqual(result.trace["termination_reason"], "completed")
                self.assertEqual(self.engine.world, before)
                records = [r for r in result.trace["records"] if r["kind"] == "tool"]
                self.assertEqual([(r["attempt"], r["error_code"]) for r in records], [(1, code)])
                self.assertNotIn("box_01", str(result.trace))

    async def test_corrected_call_is_new_step_not_transport_retry(self):
        result = await self.run_turn([response(calls=[call(arguments="{}")]), response(calls=[call(call_id="fixed")]),
                                     terminal(reply="查到了。")])
        self.assertEqual(result.trace["model_requests"], 3)
        self.assertEqual(self.engine.world.revision, 1)
        records = [r for r in result.trace["records"] if r["kind"] == "tool"]
        self.assertEqual([r["step_id"] for r in records], [1, 2])
        self.assertEqual([r["attempt"] for r in records], [1, 1])

    async def test_unknown_write_tool_never_enters_query_pool(self):
        waiter = AsyncMock()
        result = await self.run_turn([response(calls=[call("open")]), terminal(reply="尚不支持打开物品。")],
                                     executor=ReadonlyExecutor(wait_for_read=waiter))
        waiter.assert_not_awaited()
        self.assertEqual(result.receipt["ok"], True)
        self.assertEqual(self.engine.world.revision, 0)

    async def test_plain_content_and_incomplete_protocol_do_not_commit(self):
        for item, code in (
            (response("bad json"), "TOOL_CALL_REQUIRED"),
            (response('{"kind":"finish"}'), "TOOL_CALL_REQUIRED"),
            (response("partial", reason="length"), "MODEL_OUTPUT_TRUNCATED"),
            (response(calls=[call()], reason="stop"), "MODEL_PROTOCOL_ERROR"),
        ):
            with self.subTest(code=code):
                before = self.engine.world
                with self.assertRaises(AgentTurnError) as caught:
                    await self.run_turn([item])
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(len(self.model.requests), 1)
                self.assert_untouched(before)

    async def test_rejected_action_discards_prior_discovery(self):
        before = self.engine.world
        result = await self.run_turn([response(calls=[call()]),
                                     terminal("give", object_id="lamp_01", recipient_id="other_npc")])
        self.assertEqual(result.receipt["code"], "NOT_OWNER")
        self.assertEqual(self.engine.world, before)
        self.assertEqual(next(iter(self.engine.turns.values()))["event_ids"], [])

    async def test_observation_and_action_commit_together(self):
        result = await self.run_turn([response(calls=[call()]), self.give, response("给你。")])
        self.assertEqual(result.receipt["code"], "GIVEN")
        self.assertEqual([e["kind"] for e in self.engine.world.events], ["DiscoveryEvent", "TransferEvent"])
        self.assertEqual(len(self.engine.turns), 1)

    async def test_narration_retry_and_replay_never_repeat_action(self):
        def check_committed(messages):
            self.assertEqual(self.engine.world.owners["envelope_01"], "actor:other_npc")
            self.assertEqual(len(self.engine.world.events), 1)
            self.assertEqual(len(next(iter(self.engine.turns.values()))["session"]["history"]), 2)
            raise TransientFailure()
        result = await self.run_turn([self.give, check_committed, response("给你。")])
        before = self.engine.world
        replay = await self.run_turn([], limits=replace(self.limits, max_model_requests=0))
        self.assertEqual(replay.reply, result.reply)
        self.assertEqual(replay.trace["model_requests"], 0)
        self.assertNotEqual(result.trace["run_id"], replay.trace["run_id"])
        self.assertEqual(self.engine.world, before)
        with self.assertRaises(TurnConflict):
            await self.run_turn([], text="同 ID 改请求")
        self.assertEqual(self.engine.world, before)

    async def test_narration_budget_timeout_protocol_and_error_use_receipt(self):
        async def slow(messages):
            await asyncio.Event().wait()
        for suffix, replies, limits in (
            ("budget", [self.give], replace(self.limits, max_model_requests=1)),
            ("timeout", [self.give, slow], replace(self.limits, turn_timeout_s=0.05)),
            ("protocol", [self.give, response(calls=[call()])], self.limits),
            ("error", [self.give, RuntimeError("secret")], self.limits),
        ):
            with self.subTest(suffix=suffix):
                self.engine = WorldEngine(create_world(self.session["session_id"]))
                result = await self.run_turn(replies, turn_id=suffix, limits=limits)
                self.assertEqual(result.trace["termination_reason"], "narration_fallback")
                self.assertEqual(result.reply, result.receipt["message"])
                self.assertEqual(len(self.engine.world.events), 1)
                self.assertEqual(result.session["history"][-1]["content"], result.reply)

    async def test_cancel_before_and_after_commit(self):
        for committed in (False, True):
            with self.subTest(committed=committed):
                self.engine = WorldEngine(create_world(self.session["session_id"]))
                entered = asyncio.Event()
                async def wait(messages):
                    entered.set()
                    await asyncio.Event().wait()
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "trace.jsonl"
                    task = asyncio.create_task(self.run_turn([self.give, wait] if committed else [wait], trace_path=path))
                    await asyncio.wait_for(entered.wait(), 2)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                    self.assertEqual(records[-1]["termination_reason"], "cancelled_after_commit" if committed else "cancelled")
                self.assertEqual(len(self.engine.world.events), int(committed))
                self.assertFalse(self.engine.turn_lock.locked())
                if committed:
                    result = await self.run_turn([])
                    self.assertIn("已将信封", result.reply)
                else:
                    self.assertEqual(self.engine.turns, {})

    async def test_same_engine_serializes_duplicate_turns(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(messages):
            entered.set()
            await release.wait()
            return self.give
        first = asyncio.create_task(self.run_turn([delayed, response("给你。")]))
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(self.run_turn([]))
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(first, second)
        self.assertEqual([r.trace["model_requests"] for r in results], [2, 0])
        self.assertEqual(len(self.engine.world.events), 1)

    async def test_waiting_for_turn_lock_uses_total_deadline(self):
        await self.engine.turn_lock.acquire()
        try:
            with self.assertRaises(AgentTurnError) as caught:
                await self.run_turn([], limits=replace(self.limits, turn_timeout_s=0.03))
            self.assertEqual(caught.exception.code, "TURN_TIMEOUT")
            self.assertTrue(self.engine.turn_lock.locked())
            self.assertEqual(self.model.requests, [])
        finally:
            self.engine.turn_lock.release()

    async def test_exact_deadline_does_not_send_or_accept_world(self):
        trace = RunTrace(self.session["session_id"], "T", mode="fake")
        budget = RunBudget(self.limits, trace)
        budget.deadline = asyncio.get_running_loop().time()
        operation = AsyncMock()
        with self.assertRaises(RunStopped):
            await budget.call(operation, kind="model", step_id=1)
        operation.assert_not_awaited()
        before = self.engine.world
        with self.assertRaises(RunStopped):
            self.engine.commit_turn(ActionProposal("inspect", "lamp_01"), [ActionProposal("inspect", "lamp_01")],
                                    actor_id="lin_yan", turn_id="expired", digest="test", before_accept=budget.check)
        self.assert_untouched(before)

    async def test_trace_failure_does_not_reexecute_action(self):
        with tempfile.TemporaryDirectory() as directory:
            # 把已有目录当文件写，稳定触发 OSError。
            result = await self.run_turn([self.give, response("给你。")], trace_path=Path(directory))
        self.assertTrue(result.trace["trace_write_failed"])
        self.assertEqual(len(self.engine.world.events), 1)
        replay = await self.run_turn([])
        self.assertEqual(replay.trace["model_requests"], 0)

    async def test_session_identity_and_trace_privacy(self):
        before = self.engine.world
        other = deepcopy(self.session)
        other["actor_id"] = "other_npc"
        with self.assertRaises(ValueError):
            await run_agent_turn(other, "你好", ScriptedModel([]), expected_actor_id="lin_yan",
                                 engine=self.engine, turn_id="T")
        self.assert_untouched(before)
        result = await self.run_turn([response(calls=[call(call_id="secret-key-box_01")]), terminal(reply="好")])
        text = json.dumps(result.trace, ensure_ascii=False)
        for marker in ("secret-key", "box_01", "internal_note", "A02-SECRET", "L-17", "杉木-7291"):
            self.assertNotIn(marker, text)
        rows = result.trace["records"]
        self.assertEqual([r["record_seq"] for r in rows], list(range(1, len(rows) + 1)))
        self.assertEqual({r["run_id"] for r in rows}, {result.trace["run_id"]})

    async def test_fresh_session_has_no_previous_observations(self):
        await self.run_turn([response(calls=[call()]), terminal(reply="L-17")])
        self.session = create_session(actor_id="lin_yan", goal_id="clarify")
        self.engine = WorldEngine(create_world(self.session["session_id"]))
        await self.run_turn([terminal(reply="哪件物品？")])
        self.assertNotIn("L-17", json.dumps(self.model.requests))
        self.assertEqual(self.engine.world.knowledge["lin_yan"], [])


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.snapshot = freeze_world(create_world("executor"))
        self.trace = RunTrace("executor", "T", mode="fake")
        self.budget = RunBudget(RunLimits(retry_delay_s=0), self.trace)

    async def batch(self, executor, calls):
        return await executor.execute_batch(calls, snapshot=self.snapshot, actor_id="lin_yan",
                                            budget=self.budget, step_id=1)

    async def test_snapshot_recursively_readonly_and_private_permissions_preserved(self):
        with self.assertRaises(FrozenInstanceError):
            self.snapshot.revision = 9
        with self.assertRaises(TypeError):
            self.snapshot.objects["lamp_01"]["description"] = "tamper"
        with self.assertRaises(TypeError):
            self.snapshot.owners["lamp_01"] = "actor:lin_yan"
        items = await self.batch(ReadonlyExecutor(), [call()])
        self.assertTrue(items[0]["ok"])

    async def test_out_of_order_completion_keeps_call_pairing_and_snapshot(self):
        first_started, second_done = asyncio.Event(), asyncio.Event()
        async def wait(call, snapshot):
            self.assertIs(snapshot, self.snapshot)
            if call["id"] == "first":
                first_started.set()
                await second_done.wait()
            else:
                await first_started.wait()
                second_done.set()
        calls = [call(call_id="first"), call(object_id="envelope_01", call_id="second")]
        parallel = await self.batch(ReadonlyExecutor(wait_for_read=wait), calls)
        serial = await self.batch(ReadonlyExecutor(1), calls)
        self.assertEqual(parallel, serial)
        self.assertEqual([i["call_id"] for i in parallel], ["first", "second"])
        completed = [r for r in self.trace.records if r["kind"] == "tool"][:2]
        self.assertEqual([r["call_id"] for r in completed], [call_reference("second"), call_reference("first")])
        self.assertEqual({r["snapshot_revision"] for r in completed}, {0})

    async def test_five_tasks_limit_cancel_waiter_and_running_then_reuse(self):
        active, peak = 0, 0
        two_running, release = asyncio.Event(), asyncio.Event()
        async def wait(call, snapshot):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_running.set()
            try:
                await release.wait()
            finally:
                active -= 1
        executor = ReadonlyExecutor(2, wait_for_read=wait)
        def start(index):
            return asyncio.create_task(executor.execute_one(call(call_id=str(index)), snapshot=self.snapshot,
                                       actor_id="lin_yan", budget=self.budget, step_id=1))
        tasks = [start(i) for i in range(5)]
        await asyncio.wait_for(two_running.wait(), 2)
        tasks[4].cancel()  # 等待名额
        tasks[0].cancel()  # 已在运行
        outcomes = await asyncio.gather(tasks[0], tasks[4], return_exceptions=True)
        self.assertTrue(all(isinstance(item, asyncio.CancelledError) for item in outcomes))
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual((active, peak), (0, 2))
        self.assertTrue((await asyncio.wait_for(self.batch(executor, [call()]), 2))[0]["ok"])
        self.assertEqual(active, 0)

    async def test_cancel_batch_cleans_children_and_next_batch_runs(self):
        active = 0
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait(call, snapshot):
            nonlocal active
            active += 1
            entered.set()
            try:
                await release.wait()
            finally:
                active -= 1
        executor = ReadonlyExecutor(1, wait_for_read=wait)
        task = asyncio.create_task(self.batch(executor, [call(), call(call_id="q2")]))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(active, 0)
        release.set()
        self.assertTrue((await asyncio.wait_for(self.batch(executor, [call()]), 2))[0]["ok"])

    async def test_transient_query_retry_releases_slot_during_backoff(self):
        order = []
        second_done = asyncio.Event()
        async def wait(call, snapshot):
            order.append(call["id"])
            if call["id"] == "first" and order.count("first") == 1:
                raise TransientFailure()
            if call["id"] == "second":
                second_done.set()
            else:
                self.assertTrue(second_done.is_set())
        items = await self.batch(ReadonlyExecutor(1, wait_for_read=wait),
                                 [call(call_id="first"), call(call_id="second")])
        self.assertEqual(order, ["first", "second", "first"])
        self.assertTrue(all(i["ok"] for i in items))
        self.assertEqual([r["attempt"] for r in self.trace.records
                          if r["kind"] == "tool" and r["call_id"] == call_reference("first")], [1, 2])

    async def test_one_query_failure_does_not_cancel_other_business_result(self):
        async def wait(call, snapshot):
            if call["id"] == "bad":
                raise RuntimeError("secret")
        items = await self.batch(ReadonlyExecutor(wait_for_read=wait), [call(call_id="bad"), call(call_id="good")])
        self.assertEqual([i["ok"] for i in items], [False, True])
        self.assertEqual(self.snapshot.revision, 0)
        self.assertNotIn("secret", str(self.trace.records))


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_async_wire_retry_classification_and_close(self):
        requests = []
        async def transport(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "r", "object": "chat.completion", "created": 0,
                "model": "test", "choices": [{"index": 0, "finish_reason": "tool_calls",
                 "message": {"role": "assistant", "content": None, "tool_calls": [call()]}}]})
        from openai import AsyncOpenAI
        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        native = AsyncOpenAI(api_key="dummy", base_url="https://example.invalid/v1", max_retries=0, http_client=client)
        with patch("app.model.AsyncOpenAI", return_value=native) as factory:
            adapter = AsyncRealModelAdapter("dummy", "test", "https://example.invalid/v1")
        factory.assert_called_once_with(api_key="dummy", base_url="https://example.invalid/v1", timeout=10.0, max_retries=0)
        result = await adapter.complete([{"role": "user", "content": "Return JSON"}], tool_choice="auto",
                                        response_format={"type": "json_object"})
        self.assertEqual(result["message"]["tool_calls"][0]["id"], "q1")
        self.assertEqual(requests[0]["tool_choice"], "auto")
        self.assertEqual(requests[0]["response_format"], {"type": "json_object"})
        self.assertEqual(adapter.client.max_retries, 0)
        await adapter.aclose()
        self.assertTrue(client.is_closed)

    async def test_only_explicit_transient_status_codes_retry(self):
        from openai import AsyncOpenAI, APIStatusError
        for status in (429, 503, 400, 401, 403, 422):
            count = 0
            async def transport(request):
                nonlocal count
                count += 1
                return httpx.Response(status, headers={"retry-after": "2"},
                                      json={"error": {"message": "secret-key"}})
            native = AsyncOpenAI(api_key="dummy", base_url="https://example.invalid/v1", max_retries=0,
                                  http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
            with patch("app.model.AsyncOpenAI", return_value=native):
                adapter = AsyncRealModelAdapter("dummy", "test", "https://example.invalid/v1")
            expected = TransientFailure if status in (429, 503) else APIStatusError
            with self.assertRaises(expected) as caught:
                await adapter.complete([{"role": "user", "content": "test"}])
            self.assertEqual(count, 1)
            if expected is TransientFailure:
                self.assertEqual(caught.exception.retry_after_s, 2)
            await adapter.aclose()


if __name__ == "__main__":
    unittest.main()
