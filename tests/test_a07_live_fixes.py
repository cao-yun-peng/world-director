"""真实探针暴露的问题：未提交事件误引的恢复，以及程序展示已核验的来源。"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.execution import RunLimits
from app.lore import load_lore
from app.retrieval import LoreRetriever
from app.scene_cli import chat
from app.scene_runtime import SceneStory
from app.world import create_world
from scripts.a04_demo import ScriptedModel, terminal
from scripts.a07_demo import answer, search


class LiveFixTests(unittest.IsolatedAsyncioTestCase):
    async def test_uncommitted_event_error_explains_recovery_without_accepting_it(self):
        story = SceneStory(create_world('live-fix'), lore=LoreRetriever(load_lore()))
        invalid = terminal(reply='我不知道密码。', reason='没有资料。',
                           source_refs=['live-fix:E0002'], lore_refs=[])
        def correct(messages):
            item = json.loads(messages[-1]['content'])
            self.assertEqual(item['error']['code'], 'INVALID_ARGUMENTS')
            self.assertIn('source_refs', item['error']['message'])
            self.assertIn('[]', item['error']['message'])
            self.assertIn('私语候选', item['error']['message'])
            return terminal(reply='没有可用资料，无法确定密码。', reason='资料查询为空。',
                            source_refs=[], lore_refs=[])
        model = ScriptedModel([search('地下通道密码'), invalid, correct])
        result = await story.turn('地下通道密码是什么？', model, scene_turn_id='t1')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['responses'][0]['lore_refs'], [])
        self.assertEqual(story.engine.world.owners['envelope_01'], 'actor:lin_yan')
        record = next(e for e in story.audit_records['t1']['actors'][0]['trace']['records']
                      if e['kind'] == 'terminal_tool' and e['status'] == 'validated')
        self.assertEqual(record['decision_summary']['source_refs'], [])

    async def test_cli_shows_only_checked_adopted_sources(self):
        story = SceneStory(create_world('live-source'), lore=LoreRetriever(load_lore()))
        model = ScriptedModel([search('值班室用途'), answer]); model.aclose = AsyncMock()
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(output), \
             patch('builtins.input', side_effect=['值班室用途', '/retry', '/exit']):
            await chat(model, limits=RunLimits(max_input_chars=8000), max_model_requests=8,
                       story=story, trace_path=Path(folder) / 'trace.jsonl')
        self.assertEqual(output.getvalue().count('资料依据：L02@a07-lore-v1'), 2)
        self.assertNotIn('L07', output.getvalue())
        self.assertNotIn('L10', output.getvalue())
        self.assertEqual(len(model.requests), 2)
        model.aclose.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
