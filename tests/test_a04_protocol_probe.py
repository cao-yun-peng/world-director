"""语法诊断及有限对照；全部离线，不加载 .env。"""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock

from app.async_runtime import decision_error_details
from scripts.a04_demo import ScriptedModel, call, decision, response
from scripts.a04_protocol_probe import probe


class SyntaxDiagnosticTests(unittest.TestCase):
    def test_syntax_categories_and_offsets_do_not_echo_content(self):
        for content, category, leading in (
            ("private-secret", "expected_value", "other"),
            ('```json\n{"private-secret": true}\n```', "expected_value", "code_fence"),
            ('\ufeff{"private-secret":true}', "unexpected_bom", "bom"),
            ('{"reply":"private-secret\n"}', "invalid_control_character", "object"),
            ('{"reply":"private-secret"} trailing', "extra_data", "object"),
        ):
            with self.subTest(category=category):
                details = decision_error_details(content)
                self.assertEqual(details["json_error"], category)
                self.assertEqual(details["leading_form"], leading)
                self.assertEqual(details["content_length"], len(content))
                self.assertIsInstance(details["offset"], int)
                self.assertNotIn("private-secret", str(details))


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_comparison_sends_bounded_requests_and_never_executes_tools(self):
        model = ScriptedModel([response("private-secret"), decision("talk", target_text=None, reply="答复"),
                               response(calls=[call()]), RuntimeError("private-secret")])
        model.aclose = AsyncMock()
        output = io.StringIO()
        with redirect_stdout(output):
            rows = await probe(model, 4)
        self.assertEqual(len(model.requests), 4)
        self.assertEqual([row["outcome"] for row in rows],
                         ["INVALID_DECISION", "valid_decision", "tool_calls_not_executed", "MODEL_REQUEST_FAILED"])
        self.assertNotIn("private-secret", output.getvalue())
        for index, request in enumerate(model.requests):
            self.assertEqual("tools" in request, index % 2 == 0)
            self.assertEqual("tool_choice" in request, index % 2 == 0)
            self.assertEqual(request["response_format"], {"type": "json_object"})
            self.assertEqual(request["messages"][-1]["content"], "我是谁")
            self.assertFalse(any(item["role"] == "tool" for item in request["messages"]))
        model.aclose.assert_awaited_once()

    async def test_cancellation_closes_client_and_does_not_send_next_request(self):
        import asyncio
        async def cancel(messages):
            raise asyncio.CancelledError()
        model = ScriptedModel([cancel, decision()])
        model.aclose = AsyncMock()
        with self.assertRaises(asyncio.CancelledError):
            await probe(model, 4)
        self.assertEqual(len(model.requests), 1)
        model.aclose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
