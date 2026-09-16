"""A04 终结工具契约：通过循环验证提交、失败和重发，不联网。"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.async_runtime import run_agent_turn
from app.engine import WorldEngine
from app.execution import RunLimits
from app.loop_cli import chat
from app.query_executor import ReadonlyExecutor
from app.runtime import AgentTurnError
from app.session import create_session
from app.world import create_world
from scripts.a04_demo import ScriptedModel, call, terminal, response


class TerminalToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = create_session(actor_id="lin_yan", goal_id="clarify")
        self.engine = WorldEngine(create_world(self.session["session_id"]))
        self.limits = RunLimits(retry_delay_s=0)

    async def run_turn(self, replies, *, limits=None, executor=None):
        self.model = ScriptedModel(replies)
        return await run_agent_turn(self.session, "你是谁", self.model, expected_actor_id="lin_yan",
                                    engine=self.engine, turn_id="T1", limits=limits or self.limits,
                                    executor=executor)

    async def test_end_turn_answers_directly_without_narration_or_body_schema(self):
        result = await self.run_turn([terminal(reply="我是林砚。")])
        self.assertEqual(result.reply, "我是林砚。")
        self.assertEqual(result.trace["model_requests"], 1)
        self.assertEqual(self.engine.world.revision, 0)
        self.assertEqual(result.session["history"][-1]["content"], result.reply)
        request = self.model.requests[0]
        self.assertEqual(request["tool_choice"], "required")
        self.assertNotIn("response_format", request)
        self.assertEqual({tool["function"]["name"] for tool in request["tools"]},
                         {"get_visible_scene", "inspect_object", "end_turn", "move", "give"})
        self.assertFalse(any(row["kind"] == "narration" for row in result.trace["records"]))

    async def test_successful_queries_commit_only_after_end_turn(self):
        def end(messages):
            self.assertEqual(self.engine.world.revision, 0)
            self.assertEqual(self.engine.turns, {})
            self.assertEqual([message["tool_call_id"] for message in messages if message["role"] == "tool"],
                             ["lamp", "letter"])
            return terminal(reply="台灯有编号；信封仍封着。")
        result = await self.run_turn([
            response(calls=[call(call_id="lamp"), call(object_id="envelope_01", call_id="letter")]), end])
        self.assertEqual(result.trace["model_requests"], 2)
        self.assertEqual([event["kind"] for event in self.engine.world.events], ["DiscoveryEvent"] * 2)
        self.assertEqual(len(self.engine.turns), 1)

    async def test_bad_terminal_arguments_feedback_is_paired_and_can_be_corrected(self):
        cases = ["{", "null", '"text"', "[]", "{}", '{"reply":3}', '{"reply":" "}',
                 '{"reply":"答复","actor_id":"other_npc"}', '{"reply":"答复","private-key":"secret"}']
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.engine = WorldEngine(create_world(self.session["session_id"]))
                result = await self.run_turn([
                    response(calls=[call("end_turn", call_id="bad-end", arguments=arguments)]),
                    terminal(reply="请说明具体物品。")])
                wire = self.model.requests[1]["messages"]
                self.assertEqual(wire[-2]["tool_calls"][0]["id"], "bad-end")
                self.assertEqual(wire[-1]["tool_call_id"], "bad-end")
                self.assertEqual(json.loads(wire[-1]["content"])["error"]["code"], "INVALID_ARGUMENTS")
                self.assertNotIn("secret", wire[-1]["content"])
                self.assertEqual(result.trace["model_requests"], 2)
                models = [row for row in result.trace["records"] if row["kind"] == "model"]
                self.assertEqual([(row["step_id"], row["attempt"]) for row in models], [(1, 1), (2, 1)])
                self.assertEqual(self.engine.world.revision, 0)

    async def test_bad_action_arguments_do_not_reach_commit_or_query_executor(self):
        for bad in (terminal("move", destination_id=5), terminal("give", object_id="envelope_01"),
                    terminal("give", object_id="envelope_01", recipient_id="other_npc", actor_id="other_npc")):
            with self.subTest(bad=bad):
                self.engine = WorldEngine(create_world(self.session["session_id"]))
                before = self.engine.world
                with patch.object(self.engine, "commit_turn", wraps=self.engine.commit_turn) as commit:
                    with self.assertRaises(AgentTurnError) as caught:
                        await self.run_turn([bad], limits=replace(self.limits, max_steps=1))
                self.assertEqual(caught.exception.code, "STEP_LIMIT")
                commit.assert_not_called()
                self.assertEqual(self.engine.world, before)
                self.assertEqual(self.engine.turns, {})

    async def test_mixed_or_multiple_terminal_calls_reject_whole_batch(self):
        end = terminal(reply="答复")["message"]["tool_calls"][0]
        give = call("give", call_id="give", arguments='{"object_id":"envelope_01","recipient_id":"other_npc"}')
        for calls in ([call(), end], [end, call()], [give, end], [end, call("unknown", call_id="unknown")]):
            with self.subTest(calls=calls):
                before = self.engine.world
                waiter = AsyncMock()
                with self.assertRaises(AgentTurnError) as caught:
                    await self.run_turn([response(calls=calls)], executor=ReadonlyExecutor(wait_for_read=waiter))
                self.assertEqual(caught.exception.code, "TERMINAL_TOOL_CONFLICT")
                waiter.assert_not_awaited()
                self.assertEqual(self.engine.world, before)
                self.assertEqual(self.engine.turns, {})

    async def test_invalid_terminal_uses_existing_step_and_request_limits(self):
        for limits, expected, requests in (
            (replace(self.limits, max_steps=2), "STEP_LIMIT", 2),
            (replace(self.limits, max_model_requests=1), "MODEL_REQUEST_LIMIT", 1),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(AgentTurnError) as caught:
                    await self.run_turn([terminal(reply="")] * 4, limits=limits)
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(len(self.model.requests), requests)
                self.assertEqual(self.engine.turns, {})

    async def test_plain_text_after_query_does_not_commit_pending_discovery(self):
        before = self.engine.world
        with self.assertRaises(AgentTurnError) as caught:
            await self.run_turn([response(calls=[call()]), response("看到了台灯。")])
        self.assertEqual(caught.exception.code, "TOOL_CALL_REQUIRED")
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(self.engine.world, before)
        self.assertEqual(self.engine.turns, {})

    async def test_business_rejection_is_terminal_and_replayable(self):
        result = await self.run_turn([terminal("give", object_id="discovery-0", recipient_id="other_npc")])
        self.assertEqual(result.trace["termination_reason"], "rejected")
        self.assertEqual(result.receipt["code"], "OBJECT_UNAVAILABLE")
        self.assertEqual(self.engine.world.revision, 0)
        replay = await self.run_turn([])
        self.assertEqual(replay.trace["model_requests"], 0)
        self.assertEqual(replay.receipt, result.receipt)

    async def test_cli_retry_of_failed_turn_then_replay_keeps_turn_id(self):
        model = ScriptedModel([response("普通文字"), terminal(reply="我是林砚。")])
        model.aclose = AsyncMock()
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(output), \
                patch("builtins.input", side_effect=["你是谁", "/retry", "/retry", "/exit"]):
            path = Path(directory) / "trace.jsonl"
            await chat(model, session=self.session, limits=self.limits, max_model_requests=8, trace_path=path)
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        ends = [row for row in rows if row["kind"] == "run_finished"]
        self.assertEqual([row["termination_reason"] for row in ends], ["TOOL_CALL_REQUIRED", "completed", "replayed"])
        self.assertEqual(len({row["turn_id"] for row in ends}), 1)
        self.assertEqual(len({row["run_id"] for row in ends}), 3)
        self.assertEqual(len(model.requests), 2)
        self.assertIn("模型未调用工具", output.getvalue())
        self.assertIn("剩余 6", output.getvalue())
        model.aclose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
