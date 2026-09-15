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
from exercises.count_calls import count_calls


class DialogueTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.log = Path(directory.name) / "runs" / "d001.jsonl"
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
            self.assertEqual(set(record), {"day", "mode", "model", "input", "output"})
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


class AdapterTests(unittest.TestCase):
    def test_chat_request_and_text_extraction_with_mock_transport(self):
        """检查真实 SDK 的请求格式，但不访问模型服务。"""
        messages = [
            {"role": "system", "content": cli.CHARACTER_PROMPT},
            {"role": "user", "content": "这里是什么地方？"},
        ]

        def respond(request):
            payload = json.loads(request.content)
            self.assertEqual(request.url.path, "/v1/chat/completions")
            self.assertEqual(payload["messages"], messages)
            self.assertEqual(payload["model"], "test-model")
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
