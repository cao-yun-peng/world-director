"""A04 完整入口验证：不读真实配置、不发送模型请求。"""

import asyncio
import io
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import loop_cli, main as cli
from app.execution import RunLimits
from app.session import create_session
from scripts.a04_demo import ScriptedModel, decision, response


class CliTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_replay_and_client_close(self):
        model = ScriptedModel([decision("give", object_id="envelope_01", recipient_id="other_npc"), response("给你。")])
        model.aclose = AsyncMock()
        session = create_session(actor_id="lin_yan", goal_id="clarify")
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(output), \
                patch("builtins.input", side_effect=["给信封", "/retry", "你好", "/exit"]):
            code = await loop_cli.chat(model, session=session, limits=RunLimits(), max_model_requests=2,
                                        trace_path=Path(directory) / "trace.jsonl")
        self.assertEqual(code, 0)
        self.assertEqual(len(model.requests), 2)
        self.assertIn("replayed", output.getvalue())
        self.assertIn("MODEL_REQUEST_LIMIT", output.getvalue())
        self.assertNotIn("剩余 -", output.getvalue())
        model.aclose.assert_awaited_once()

    async def test_cancel_after_commit_prints_receipt_and_closes_client(self):
        started = asyncio.Event()
        async def wait(messages):
            started.set()
            await asyncio.Event().wait()
        model = ScriptedModel([decision("give", object_id="envelope_01", recipient_id="other_npc"), wait])
        model.aclose = AsyncMock()
        session = create_session(actor_id="lin_yan", goal_id="clarify")
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(output), patch("builtins.input", return_value="给信封"):
            task = asyncio.create_task(loop_cli.chat(model, session=session, limits=RunLimits(), max_model_requests=8,
                                                       trace_path=Path(directory) / "trace.jsonl"))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIn("已提交", output.getvalue())
        self.assertIn("已将信封", output.getvalue())
        model.aclose.assert_awaited_once()


class EntryTests(unittest.TestCase):
    def test_main_routes_loop_and_invalid_limits_do_not_load_model(self):
        with patch("app.loop_cli.main", return_value=0) as routed:
            self.assertEqual(cli.main(["--engine", "loop", "--max-steps", "3"]), 0)
        self.assertEqual(routed.call_args.args[0].max_steps, 3)
        args = Namespace(load=None, max_model_requests=8, max_steps=0, turn_timeout=30, max_parallel_tools=2)
        with patch("app.loop_cli.load_model") as factory, redirect_stdout(io.StringIO()):
            self.assertEqual(loop_cli.main(args), 1)
        factory.assert_not_called()

    def test_real_smoke_requires_explicit_flag(self):
        from scripts.a04_smoke import main
        with patch("scripts.a04_smoke.load_model") as factory, \
                patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            main([])
        factory.assert_not_called()
