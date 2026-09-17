import asyncio
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch, AsyncMock
from app.execution import RunLimits
from app.scene_runtime import SceneStory
from app.scene_save import (BatchBudget, load_scene, save_scene, snapshot_story,
                            restore_story, read_scene, slot_path)
from app.world import create_world
from app.retrieval import LoreRetriever
from app.lore import load_lore
from app.engine import TurnConflict
from scripts.a04_demo import ScriptedModel, terminal, response
from app.scene_cli import chat
from scripts.a08_demo import demo


def wait():
    return terminal('wait',reply='继续核对。',reason='查看记录。',source_refs=[])


class SaveTests(unittest.IsolatedAsyncioTestCase):
    async def make_story(self):
        story=SceneStory(create_world('save-test'),max_story_requests=80,max_story_turns=20,
                         batch=BatchBudget(180))
        await story.turn('私密测试原句',ScriptedModel([wait()]),scene_turn_id='t1')
        return story

    async def test_roundtrip_replay_conflict_and_continue(self):
        story=await self.make_story()
        with tempfile.TemporaryDirectory() as folder:
            save_scene(story,'slot',directory=folder)
            restored=load_scene('slot',directory=folder)
        self.assertEqual(snapshot_story(story),snapshot_story(restored))
        model=ScriptedModel([])
        before=restored.engine.world
        result=await restored.turn('私密测试原句',model,scene_turn_id='t1')
        self.assertTrue(result['replayed'])
        for key in ('model_requests','chat_requests','embedding_requests','rerank_requests'):
            self.assertEqual(result[key],0)
        self.assertEqual(before,restored.engine.world)
        with self.assertRaises(TurnConflict):
            await restored.turn('changed',model,scene_turn_id='t1')
        model=ScriptedModel([wait()])
        await restored.turn('下一轮',model,scene_turn_id='t2',focus_actor='other_npc')
        self.assertNotIn('私密测试原句',json.dumps(model.requests,ensure_ascii=False))
        self.assertEqual(restored.model_requests,2)

    async def test_corrupt_unknown_schema_and_configuration_rejected(self):
        story=await self.make_story()
        with tempfile.TemporaryDirectory() as folder:
            target=save_scene(story,'s',directory=folder)
            target.write_text('{broken',encoding='utf-8')
            with self.assertRaises(ValueError):
                load_scene('s',directory=folder)
        payload=snapshot_story(story)
        for field,value in [('schema','old-chat'),('app_version','future')]:
            bad=json.loads(json.dumps(payload));bad[field]=value
            with self.assertRaises(ValueError):
                restore_story(bad)
        with self.assertRaises(ValueError):
            restore_story(payload,lore=LoreRetriever(load_lore()))

    async def test_world_event_budget_and_actor_corruption_rejected(self):
        payload=snapshot_story(await self.make_story())
        mutations=[
            lambda p:p['world']['owners'].update(envelope_01='actor:other_npc'),
            lambda p:p['budget'].update(model_requests=0),
            lambda p:p['budget']['batch'].update(used=0),
            lambda p:p['world']['events'][1].update(recipient_ids=['other_npc']),
            lambda p:p['conversations']['other_npc'].update(actor_id='lin_yan'),
            lambda p:p['records']['t1']['request'].update(text='changed'),
        ]
        for mutation in mutations:
            bad=json.loads(json.dumps(payload));mutation(bad)
            with self.assertRaises(ValueError):
                restore_story(bad)

    async def test_paths_inflight_and_atomic_failure_keep_old_file(self):
        story=await self.make_story()
        with tempfile.TemporaryDirectory() as folder:
            target=save_scene(story,'s',directory=folder)
            old=target.read_bytes()
            for slot in ('../x','C:\\x','a/b','a.b','', 'CON/'):
                with self.assertRaises(ValueError):
                    slot_path(slot,folder)
            await story._lock.acquire()
            try:
                with self.assertRaises(ValueError):
                    save_scene(story,'s',directory=folder)
            finally:
                story._lock.release()
            with patch('app.scene_save.os.replace',side_effect=OSError('simulated write failure')):
                with self.assertRaises(OSError):
                    save_scene(story,'s',directory=folder)
            self.assertEqual(target.read_bytes(),old)
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])

    async def test_ended_and_partial_stories_replay_without_requests(self):
        for partial in (False,True):
            story=SceneStory(create_world('partial' if partial else 'ended'))
            if partial:
                responses=[terminal('give',object_id='envelope_01',recipient_id='other_npc',
                                    reason='推进',source_refs=[]),response('按回执。'),TimeoutError()]
                request=dict(text='推进',scene_turn_id='t',choice='offer_handover',max_responders=2)
            else:
                responses=[wait()]
                request=dict(text='暂缓',scene_turn_id='t',choice='pause_handover')
            result=await story.turn(model=ScriptedModel(responses),limits=RunLimits(max_attempts=1),**request)
            self.assertEqual(result['status'],'partial' if partial else 'completed')
            restored=restore_story(snapshot_story(story))
            before=restored.engine.world
            replay=await restored.turn(model=ScriptedModel([]),**request)
            self.assertTrue(replay['replayed'])
            self.assertEqual(replay['model_requests'],0)
            self.assertEqual(restored.engine.world,before)
            if partial:
                self.assertEqual(before.owners['envelope_01'],'actor:other_npc')
            else:
                stopped=await restored.turn('再来',ScriptedModel([]),scene_turn_id='new')
                self.assertEqual(stopped['error_code'],'STORY_ENDED')

    async def test_turn_limit_replay_and_batch_limit(self):
        story=SceneStory(create_world('limits'),max_story_turns=1,batch=BatchBudget(1))
        await story.turn('one',ScriptedModel([wait()]),scene_turn_id='one')
        replay=await story.turn('one',ScriptedModel([]),scene_turn_id='one')
        self.assertTrue(replay['replayed'])
        result=await story.turn('two',ScriptedModel([]),scene_turn_id='two')
        self.assertEqual(result['error_code'],'STORY_TURN_LIMIT')
        self.assertEqual(story.turn_count,1)
        new=SceneStory(batch=story.batch)
        result=await new.turn('new',ScriptedModel([]),scene_turn_id='new')
        self.assertEqual(result['error_code'],'MODEL_REQUEST_LIMIT')

    async def test_cli_load_old_slot_cannot_refill_process_quota(self):
        story=SceneStory(create_world('cli-save'),max_story_requests=3)
        model=ScriptedModel([wait(),wait(),wait()]);model.aclose=AsyncMock()
        output=io.StringIO()
        with tempfile.TemporaryDirectory() as folder,redirect_stdout(output),patch('builtins.input',
            side_effect=['one','/save s','two','/load s','/retry','three','/new','four','/exit']):
            await chat(model,limits=RunLimits(max_input_chars=8000),max_model_requests=3,
                       story=story,save_directory=folder,trace_path=Path(folder)/'trace.jsonl')
        self.assertEqual(len(model.requests),3)
        self.assertIn('MODEL_REQUEST_LIMIT',output.getvalue())
        self.assertNotIn('存档操作失败',output.getvalue())


class ProcessTests(unittest.TestCase):
    def test_twenty_rounds_cross_process_resume_and_both_endings(self):
        with tempfile.TemporaryDirectory() as folder:
            result=demo(Path(folder)/'report.json')
        self.assertEqual(result['rounds'],20)
        self.assertTrue(result['cross_process'])
        self.assertEqual(result['ending'],'handed_over')
        self.assertEqual(result['other_ending'],'deferred')
