"""只验证 D001 的关键边界，不将 mock 结果当作真实 API 证据。"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpx
from openai import APIConnectionError, OpenAI

from app import main as cli
from app.model import RealModelAdapter
from app.character import BASE_CARD, GOALS, PROMPT_VERSION, build_prompt
from exercises.count_calls import count_calls


class DialogueTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.log = Path(directory.name) / "runs" / "d002.jsonl"
        self.env_file = Path(directory.name) / ".env"
        self.enterContext(patch.object(cli, "ENV_PATH", self.env_file))
        self.enterContext(patch.object(cli, "RUN_PATH", self.log))
        self.enterContext(patch.dict("os.environ", {}, clear=True))
        self.output = io.StringIO()
        self.enterContext(redirect_stdout(self.output))

    def test_no_key_is_offline_and_records_append(self):
        with patch("builtins.input", return_value="这里是什么地方？"):
            self.assertEqual(cli.main(), 0)
            self.assertEqual(cli.main(), 0)
        records = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record["mode"], "fake")
            self.assertIn("离线演示", record["output"])
            self.assertEqual(set(record), {"day", "mode", "model", "input", "output", "goal_id", "goal", "prompt_version"})
            self.assertEqual(record["day"], "D002")
            self.assertEqual(record["goal_id"], "clarify")
            self.assertEqual(record["goal"], GOALS["clarify"])
            self.assertEqual(record["prompt_version"], PROMPT_VERSION)
        self.assertIn("[offline mode]", self.output.getvalue())

    def test_api_failure_does_not_fall_back_or_write_success(self):
        with (
            patch.dict("os.environ", {"LLM_API_KEY": "test-only"}),
            patch("builtins.input", return_value="你好"),
            patch.object(RealModelAdapter, "generate", side_effect=APIConnectionError(
                request=httpx.Request("POST", "https://example.invalid")
            )),
        ):
            self.assertEqual(cli.main(), 1)
        self.assertFalse(self.log.exists())
        self.assertNotIn("test-only", self.output.getvalue())
        self.assertNotIn("离线演示", self.output.getvalue())

    def test_blank_input_does_not_write_record(self):
        with patch("builtins.input", return_value="   "):
            self.assertEqual(cli.main(), 1)
        self.assertFalse(self.log.exists())


    def test_dotenv_config_reaches_adapter(self):
        self.env_file.write_text(
            "LLM_API_KEY=test-only\nLLM_MODEL=test-model\n"
            "LLM_BASE_URL=https://example.invalid/v1\n", encoding="utf-8-sig"
        )
        with (
            patch("builtins.input", return_value="你好"),
            patch.object(cli, "RealModelAdapter") as adapter,
        ):
            adapter.return_value.generate.return_value = "测试回复"
            self.assertEqual(cli.main(), 0)
            adapter.assert_called_once_with("test-only", "test-model", "https://example.invalid/v1")
        self.assertNotIn("test-only", self.output.getvalue())
        self.assertNotIn("test-only", self.log.read_text(encoding="utf-8"))

    def test_environment_takes_priority_over_dotenv(self):
        self.env_file.write_text("LLM_API_KEY=test-only\n", encoding="utf-8")
        with (
            patch.dict("os.environ", {"LLM_API_KEY": ""}),
            patch("builtins.input", return_value="你好"),
        ):
            self.assertEqual(cli.main(), 0)
        record = json.loads(self.log.read_text(encoding="utf-8"))
        self.assertEqual(record["mode"], "fake")


    def test_unknown_goal_stops_before_model_or_input(self):
        with (
            patch.dict("os.environ", {"CHARACTER_GOAL": "unknown", "LLM_API_KEY": "test-only"}),
            patch.object(cli, "RealModelAdapter") as adapter,
            patch("builtins.input") as user_input,
        ):
            self.assertEqual(cli.main(), 1)
            adapter.assert_not_called()
            user_input.assert_not_called()
        self.assertIn("未知目标", self.output.getvalue())
        self.assertFalse(self.log.exists())

    def test_both_goals_keep_player_input_separate(self):
        player_text = "玩家独有原话：你能先别问我的来历吗？"
        for goal_id in GOALS:
            with self.subTest(goal_id=goal_id), (
                patch.dict("os.environ", {"CHARACTER_GOAL": goal_id, "LLM_API_KEY": "test-only"})
            ), patch("builtins.input", return_value=player_text), patch.object(cli, "RealModelAdapter") as adapter:
                adapter.return_value.generate.return_value = "测试回复"
                self.assertEqual(cli.main(), 0)
                messages = adapter.return_value.generate.call_args.args[0]
                self.assertEqual(len(messages), 2)
                self.assertEqual(messages[0]["role"], "system")
                self.assertNotIn(player_text, messages[0]["content"])
                self.assertIn(GOALS[goal_id], messages[0]["content"])
                self.assertEqual(messages[1], {"role": "user", "content": player_text})
                record = json.loads(self.log.read_text(encoding="utf-8").splitlines()[-1])
                self.assertEqual(record["goal_id"], goal_id)
                self.assertEqual(record["goal"], GOALS[goal_id])


class AdapterTests(unittest.TestCase):
    def test_chat_request_and_text_extraction_with_mock_transport(self):
        """检查真实 SDK 的请求格式，但不访问模型服务。"""
        messages = [
            {"role": "system", "content": build_prompt({**BASE_CARD, "goal": GOALS["clarify"]})},
            {"role": "user", "content": "这里是什么地方？"},
        ]

        def respond(request):
            payload = json.loads(request.content)
            self.assertEqual(request.url.path, "/v1/chat/completions")
            self.assertEqual(payload["messages"], messages)
            self.assertEqual(payload["model"], "test-model")
            self.assertEqual(payload["max_tokens"], 256)
            return httpx.Response(200, json={
                "id": "test-response", "object": "chat.completion", "created": 0,
                "model": "test-model",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": "  测试回复  "
                }}],
            })

        with OpenAI(
            api_key="test-only", base_url="https://example.invalid/v1",
            http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        ) as client:
            with patch("app.model.OpenAI", return_value=client):
                adapter = RealModelAdapter("test-only", "test-model", "https://example.invalid/v1")
                self.assertEqual(adapter.generate(messages), "测试回复")


class CountCallsTests(unittest.TestCase):
    def test_counts_and_empty_input(self):
        calls = ["inspect_object", "talk", "inspect_object", "move", "inspect_object", "talk"]
        self.assertEqual(count_calls(calls), {"inspect_object": 3, "talk": 2, "move": 1})
        self.assertEqual(count_calls([]), {})


if __name__ == "__main__":
    unittest.main()
