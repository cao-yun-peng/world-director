"""终结工具协议的有限对照；全部离线，不加载 .env。"""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock

from scripts.a04_demo import ScriptedModel, call, terminal, response
from scripts.a04_protocol_probe import probe


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_comparison_sends_bounded_requests_and_never_executes_tools(self):
        model = ScriptedModel([response("private-secret"), terminal(reply="答复"),
                               response(calls=[call()]), RuntimeError("private-secret")])
        model.aclose = AsyncMock()
        output = io.StringIO()
        with redirect_stdout(output):
            rows = await probe(model, 4)
        self.assertEqual(len(model.requests), 4)
        self.assertEqual([row["outcome"] for row in rows],
                         ["TOOL_CALL_REQUIRED", "terminal_not_executed", "tool_calls_not_executed", "MODEL_REQUEST_FAILED"])
        self.assertNotIn("private-secret", output.getvalue())
        for index, request in enumerate(model.requests):
            self.assertIn("tools", request)
            self.assertEqual(request["tool_choice"], "required" if index % 2 == 0 else "auto")
            self.assertNotIn("response_format", request)
            self.assertEqual(request["messages"][-1]["content"], "我是谁")
            self.assertFalse(any(item["role"] == "tool" for item in request["messages"]))
        model.aclose.assert_awaited_once()

    async def test_cancellation_closes_client_and_does_not_send_next_request(self):
        import asyncio
        async def cancel(messages):
            raise asyncio.CancelledError()
        model = ScriptedModel([cancel, terminal(reply="已查看。")])
        model.aclose = AsyncMock()
        with self.assertRaises(asyncio.CancelledError):
            await probe(model, 4)
        self.assertEqual(len(model.requests), 1)
        model.aclose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
