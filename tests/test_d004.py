"""D004：用记录型假模型检查输入快照和完整 CLI 流程。"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app import main as cli
from app.character import GOALS
from app.scene_data import FACTS


class RecordingFakeModel:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    def generate(self, messages: list[dict[str, str]]) -> str:
        # 新建列表，再复制每个字典；当前字段值都是不可变的字符串。
        snapshot = []
        for message in messages:
            snapshot.append(message.copy())
        self.calls.append(snapshot)
        return self.reply


class RecordingFakeModelTests(unittest.TestCase):
    def test_records_independent_snapshot(self):
        model = RecordingFakeModel(reply="【测试回复】你好")
        messages = [{"role": "user", "content": "你好"}]

        reply = model.generate(messages)
        self.assertEqual(messages, [{"role": "user", "content": "你好"}])
        messages[0]["content"] = "后来修改的内容"
        messages.append({"role": "user", "content": "追加的一句话"})

        self.assertEqual(reply, "【测试回复】你好")
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(model.calls[0]), 1)
        self.assertEqual(model.calls[0][0]["content"], "你好")
        self.assertEqual(model.calls[0], [{"role": "user", "content": "你好"}])

    def test_cli_uses_recording_fake_and_filtered_messages(self):
        player_text = "你知道备用抹布放在哪里吗？"
        model = RecordingFakeModel(reply="【测试回复】这是记录器返回的固定文本。")
        output = io.StringIO()

        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "runs" / "dialogue.jsonl"
            with (
                patch.dict("os.environ", {}, clear=True),
                patch.object(cli, "ENV_PATH", Path(directory) / ".env"),
                patch.object(cli, "RUN_PATH", log_path),
                patch("builtins.input", side_effect=[player_text, "/exit"]),
                patch.object(cli, "FakeModelAdapter", return_value=model),
                patch.object(cli, "RealModelAdapter", side_effect=AssertionError("测试禁止构造真实模型")),
                redirect_stdout(output),
            ):
                exit_code = cli.main([])

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(model.calls), 1)
            messages = model.calls[0]
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[1], {"role": "user", "content": player_text})
            self.assertEqual(messages[0]["role"], "system")
            self.assertNotIn(player_text, messages[0]["content"])
            for fact in FACTS[:2]:
                self.assertIn(fact["id"], messages[0]["content"])
                self.assertIn(fact["text"], messages[0]["content"])

            # 检查完整请求，不只检查 system；玩家原话不含这些隐藏标记。
            request_text = json.dumps(messages, ensure_ascii=False)
            forbidden = [
                FACTS[2]["id"], FACTS[2]["text"],
                FACTS[3]["id"], FACTS[3]["text"], FACTS[0]["internal_note"],
                "杉木-7291", "晚潮-6158", "鹭羽-3841",
                "internal_note", "visibility", "known_by",
            ]
            for text in forbidden:
                self.assertNotIn(text, request_text)

            self.assertIn("[offline mode]", output.getvalue())
            self.assertIn(f"林砚：{model.reply}", output.getvalue())
            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertTrue(record["session_id"])
            self.assertEqual(record, {
                "session_id": record["session_id"], "turn_index": 1,
                "day": "A01",
                "actor_id": "lin_yan",
                "visible_fact_ids": ["F_PUBLIC", "F_LIN"],
                "goal_id": "clarify",
                "goal": GOALS["clarify"],
                "prompt_version": "a01-v1",
                "mode": "fake",
                "model": "FakeLLM",
                "input": player_text,
                "output": model.reply,
            })


if __name__ == "__main__":
    unittest.main()
