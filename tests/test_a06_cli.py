"""场景 CLI 的明确选择、重放、总配额与退出；不加载真实配置。"""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.execution import RunLimits
from app.scene_cli import chat
from app.scene_runtime import SceneStory
from app.world import create_world
from scripts.a04_demo import ScriptedModel
from scripts.a06_demo import decision


class SceneCliTests(unittest.IsolatedAsyncioTestCase):
    async def test_choice_retry_ended_actions_and_close(self):
        story = SceneStory(create_world('a06-cli'))
        model = ScriptedModel([decision(story, reply='本次暂缓。')])
        model.aclose = AsyncMock()
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(output), \
                patch('builtins.input', side_effect=['/pause', '/focus other_npc', '/retry', '继续', '/status', '/exit']):
            status = await chat(model, limits=RunLimits(max_input_chars=8000), max_model_requests=2,
                                trace_path=Path(folder) / 'trace.jsonl', story=story)
        self.assertEqual(status, 0)
        self.assertEqual(len(model.requests), 1)
        self.assertIn('deferred', output.getvalue())
        self.assertIn('STORY_ENDED', output.getvalue())
        self.assertIn('剩余 1', output.getvalue())
        model.aclose.assert_awaited_once()

    async def test_new_story_does_not_reset_process_quota(self):
        story = SceneStory(create_world('a06-cli-limit'), max_story_requests=1)
        model = ScriptedModel([decision(story, reply='本次暂缓。')])
        model.aclose = AsyncMock()
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(output), \
                patch('builtins.input', side_effect=['/pause', '/new', '/offer', '/exit']):
            await chat(model, limits=RunLimits(max_input_chars=8000), max_model_requests=1,
                        trace_path=Path(folder) / 'trace.jsonl', story=story)
        self.assertEqual(len(model.requests), 1)
        self.assertIn('MODEL_REQUEST_LIMIT', output.getvalue())
        model.aclose.assert_awaited_once()

    async def test_main_scene_route_does_not_load_a_model(self):
        from app.main import main
        with patch('app.scene_cli.main', return_value=0) as entry:
            self.assertEqual(main(['--engine', 'scene', '--max-model-requests', '24']), 0)
        self.assertEqual(entry.call_args.args[0].max_model_requests, 24)


if __name__ == '__main__':
    unittest.main()
