"""复现多轮普通对白破坏决策协议；只用 Fake，不读真实密钥。"""

import asyncio
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.async_runtime import decision_error_details, run_agent_turn
from app.engine import WorldEngine
from app.execution import RunLimits
from app.loop_cli import chat
from app.runtime import AgentTurnError
from app.session import create_session
from app.world import create_world
from scripts.a04_demo import ScriptedModel, call, decision, response


class DecisionFormatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = create_session(actor_id="lin_yan", goal_id="clarify")
        self.engine = WorldEngine(create_world(self.session["session_id"]))
        self.limits = RunLimits(retry_delay_s=0)

    async def run_turn(self, replies, *, turn_id="T1", limits=None):
        self.model = ScriptedModel(replies)
        return await run_agent_turn(self.session, "你是谁", self.model, expected_actor_id="lin_yan",
                                    engine=self.engine, turn_id=turn_id, limits=limits or self.limits)

    async def test_plain_response_after_greeting_gets_one_new_decision(self):
        first = await run_agent_turn(self.session, "你好", ScriptedModel([
            decision("talk", target_text=None, reply="……你好。")]), expected_actor_id="lin_yan",
            engine=self.engine, turn_id="greeting")
        self.session = first.session
        result = await self.run_turn([response("我是林砚。"),
                                     decision("talk", target_text=None, reply="我是林砚，灯塔的临时管理员。")])
        self.assertEqual(result.trace["model_requests"], 2)
        self.assertEqual(result.trace["termination_reason"], "completed")
        self.assertEqual(len(result.session["history"]), 4)
        self.assertEqual(self.engine.world.revision, 0)
        self.assertEqual(self.engine.world.events, [])
        self.assertEqual(len(self.session["history"]), 2)
        records = [row for row in result.trace["records"] if row["kind"] == "model"]
        self.assertEqual([(row["step_id"], row["attempt"]) for row in records], [(1, 1), (2, 1)])
        for request in self.model.requests:
            self.assertEqual(request["response_format"], {"type": "json_object"})
            self.assertEqual(request["tool_choice"], "auto")
        self.assertNotIn("结构诊断：", self.model.requests[0]["messages"][0]["content"])
        self.assertIn("结构诊断：", self.model.requests[1]["messages"][0]["content"])

    async def test_missing_null_field_can_be_corrected(self):
        result = await self.run_turn([decision("talk", reply="我是林砚。"),
                                     decision("talk", target_text=None, reply="我是林砚。")])
        detail = next(row["decision_details"] for row in result.trace["records"] if row["kind"] == "decision")
        self.assertEqual(detail["missing_fields"], ["target_text"])
        self.assertEqual(result.reply, "我是林砚。")

    async def test_second_bad_decision_stops_with_safe_diagnostics(self):
        with self.assertRaises(AgentTurnError) as caught:
            await self.run_turn([response("secret-key 杉木-7291"), response("still not JSON"),
                                 decision("talk", target_text=None, reply="不能到这里")])
        self.assertEqual(caught.exception.code, "INVALID_DECISION")
        self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(self.engine.turns, {})
        self.assertEqual(self.session["history"], [])
        details = [row["decision_details"] for row in caught.exception.trace["records"] if row["kind"] == "decision"]
        self.assertEqual([item["shape"] for item in details], ["invalid_json"] * 2)
        self.assertEqual([item["leading_form"] for item in details], ["other"] * 2)
        second_wire = json.dumps(self.model.requests[1], ensure_ascii=False)
        for text in (str(caught.exception.trace), second_wire):
            self.assertNotIn("secret-key", text)
            self.assertNotIn("杉木-7291", text)

    async def test_identity_unknown_fields_and_invalid_values_are_not_repaired(self):
        for item in (
            decision("talk", target_text=None, reply="好", actor_id="other_npc"),
            decision("talk", reply="好", **{"secret-field-7291": "secret-value"}),
            decision("talk", target_text="lamp_01", reply="好"),
            decision("finish", actor_id="other_npc"),
            decision("inspect", target_text="lamp_01", reply=None),
            response('[]'),
        ):
            with self.subTest(item=item):
                with self.assertRaises(AgentTurnError) as caught:
                    await self.run_turn([item])
                self.assertEqual(caught.exception.code, "INVALID_DECISION")
                self.assertEqual(len(self.model.requests), 1)
                self.assertEqual(self.engine.turns, {})
                self.assertNotIn("secret-field-7291", str(caught.exception.trace))
                self.assertNotIn("secret-value", str(caught.exception.trace))

    async def test_correction_cannot_exceed_step_or_request_budget(self):
        for limits, expected in (
            (replace(self.limits, max_steps=1), "STEP_LIMIT"),
            (replace(self.limits, max_model_requests=1), "MODEL_REQUEST_LIMIT"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(AgentTurnError) as caught:
                    await self.run_turn([response("我是林砚。")], limits=limits)
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(len(self.model.requests), 1)
                self.assertEqual(self.engine.turns, {})

    async def test_correction_wait_uses_original_deadline(self):
        async def blocked(messages):
            await asyncio.Event().wait()
        with self.assertRaises(AgentTurnError) as caught:
            await self.run_turn([response("我是林砚。"), blocked],
                                limits=replace(self.limits, turn_timeout_s=0.05))
        self.assertEqual(caught.exception.code, "TURN_TIMEOUT")
        self.assertEqual(caught.exception.trace["model_requests"], 2)
        self.assertEqual(self.engine.turns, {})

    async def test_failed_repair_discards_successful_pending_observation(self):
        before = self.engine.world
        with self.assertRaises(AgentTurnError) as caught:
            await self.run_turn([response(calls=[call()]), response("看到了台灯。"), response("还是普通文字。")])
        self.assertEqual(caught.exception.code, "INVALID_DECISION")
        self.assertEqual(self.engine.world, before)
        self.assertEqual(self.engine.turns, {})

    async def test_correction_can_choose_native_query_without_implicit_write(self):
        result = await self.run_turn([response("看看台灯。"), response(calls=[call()]), decision(), response("已查看。")])
        self.assertEqual(result.trace["model_requests"], 4)
        self.assertEqual(self.model.requests[2]["messages"][-1]["tool_call_id"], "q1")
        self.assertEqual(len(self.engine.world.events), 1)
        self.assertEqual(self.engine.world.events[0]["kind"], "DiscoveryEvent")
        self.assertNotIn("response_format", self.model.requests[-1])

    async def test_cli_explains_format_problem_without_generic_retry_advice(self):
        model = ScriptedModel([response("我是林砚。"), response("仍是普通文字。")])
        model.aclose = AsyncMock()
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(output), \
                patch("builtins.input", side_effect=["你是谁", "/exit"]):
            await chat(model, session=self.session, limits=self.limits, max_model_requests=8,
                       trace_path=Path(directory) / "trace.jsonl")
        self.assertIn("不是有效 JSON", output.getvalue())
        self.assertNotIn("可用 /retry 重试", output.getvalue())
        self.assertIn("剩余 6", output.getvalue())
        model.aclose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
