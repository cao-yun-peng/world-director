"""从落盘 JSONL 验证诊断链，而不只检查内存中的 trace。"""

import json
import tempfile
import unittest
from pathlib import Path

from app.async_runtime import RUNTIME_VERSION, run_agent_turn
from app.engine import WorldEngine
from app.execution import RunLimits, TransientFailure
from app.runtime import AgentTurnError
from app.session import create_session
from scripts.a04_demo import ScriptedModel, call, terminal, response
from app.world import create_world


class TraceDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def run_trace(self, responses):
        session = create_session(actor_id="lin_yan", goal_id="clarify")
        model = ScriptedModel(responses)
        model.model, model.provider_host = "test-model", "example.invalid"
        engine = WorldEngine(create_world(session["session_id"]))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "trace.jsonl"
            try:
                result = await run_agent_turn(session, "private-user-text", model, engine=engine,
                                              expected_actor_id="lin_yan", turn_id="test-turn",
                                              limits=RunLimits(retry_delay_s=0), trace_path=path)
                trace = result.trace
            except AgentTurnError as error:
                trace = error.trace
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows, trace["records"])
        self.assertTrue(all(row["trace_version"] == 3 for row in rows))
        self.assertNotIn("private-", json.dumps(rows))
        return rows

    async def test_terminal_argument_feedback_has_distinct_trace_records(self):
        rows = await self.run_trace([
            response(calls=[call("end_turn", arguments='{"reply":"private-reply')]),
            terminal(reply="private-reply")])
        started = rows[0]
        self.assertEqual(started["runtime_version"], RUNTIME_VERSION)
        self.assertEqual(started["model_name"], "test-model")
        self.assertEqual(started["provider_host"], "example.invalid")
        self.assertEqual(started["limits"]["max_steps"], 4)
        self.assertEqual(started["history_messages"], 0)
        models = [row for row in rows if row["kind"] == "model"]
        for model in models:
            self.assertEqual(model["status"], "ok")
            self.assertEqual(model["response_details"]["finish_reason"], "tool_calls")
            self.assertEqual(model["response_details"]["tool_call_count"], 1)
            self.assertEqual(model["request_options"]["response_format"], "text")
            self.assertEqual(model["request_options"]["tool_choice"], "required")
        terminals = [row for row in rows if row["kind"] == "terminal_tool"]
        self.assertEqual([row["status"] for row in terminals], ["rejected", "validated"])
        self.assertEqual(terminals[0]["error_code"], "INVALID_ARGUMENTS")
        self.assertEqual(terminals[1]["tool_name"], "end_turn")
        self.assertEqual(rows[-1]["termination_reason"], "completed")

    async def test_protocol_failure_is_visible_without_echoing_unknown_fields(self):
        rows = await self.run_trace([{"finish_reason": "private-unknown", "message":
                                     {"role": "assistant", "content": "private-reply"}}])
        model = next(row for row in rows if row["kind"] == "model")
        self.assertEqual(model["response_details"]["finish_reason"], "unknown")
        self.assertEqual(rows[-1]["termination_reason"], "MODEL_PROTOCOL_ERROR")
        self.assertFalse(any(row["kind"] == "commit" for row in rows))

    async def test_transport_retry_preserves_attempt_and_span(self):
        rows = await self.run_trace([TransientFailure(), terminal(reply="private-reply")])
        models = [row for row in rows if row["kind"] == "model"]
        self.assertEqual([row["attempt"] for row in models], [1, 2])
        self.assertNotIn("response_details", models[0])
        self.assertIn("response_details", models[1])
        self.assertTrue(any(row["kind"] == "retry_wait" for row in rows))
        self.assertEqual(models[0]["span_id"], models[1]["span_id"])

    async def test_native_tool_and_narration_counts_and_formats_are_visible(self):
        rows = await self.run_trace([response(calls=[call()]), terminal("give", object_id="envelope_01", recipient_id="other_npc"), response("private-narration")])
        models = [row for row in rows if row["kind"] == "model"]
        self.assertEqual(models[0]["response_details"]["tool_call_count"], 1)
        self.assertEqual(models[0]["response_details"]["finish_reason"], "tool_calls")
        self.assertEqual(next(row for row in rows if row["kind"] == "terminal_tool")["tool_name"], "give")
        narration = next(row for row in rows if row["kind"] == "narration")
        self.assertEqual(narration["request_options"]["response_format"], "text")
        self.assertEqual(narration["request_options"]["tool_choice"], "none")
        self.assertTrue(any(row["kind"] == "tool" for row in rows))


if __name__ == "__main__":
    unittest.main()
