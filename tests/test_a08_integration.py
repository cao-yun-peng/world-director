import asyncio
import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app.evaluation import load_dev, DEV_PATH
from app.execution import RunLimits
from app.lore import load_lore
from app.lore_tools import pack_lore, parse_lore_terminal, LORE_TOOL_SCHEMAS
from app.memory import input_chars
from app.retrieval import LoreRetriever, build_index
from app.embeddings import EmbeddingCache, FakeEmbedding
from app.reranking import FakeReranker, RealReranker
from app.scene_runtime import SceneStory
from app.scene_save import snapshot_story, restore_story, BatchBudget
from app.scene_cli import configured_chat, model_identity
from app.world import create_world
from scripts.a04_demo import ScriptedModel, terminal, call
from scripts.a07_demo import search, answer, budget
from scripts.a08_cases import boundary_cases


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_ten_boundary_cases(self):
        rows=await boundary_cases()
        self.assertEqual(len(rows),10)
        self.assertTrue(all(r['status']=='passed' for r in rows),rows)

    async def test_same_scene_second_actor_has_different_lore_acl(self):
        snapshot=load_lore()
        cache=EmbeddingCache(FakeEmbedding())
        index=await build_index(snapshot,cache,budget())
        reranker=FakeReranker()
        lore=LoreRetriever(snapshot,'hybrid_fake',cache,index,min_score=0,reranker=reranker)
        story=SceneStory(create_world('two-actors'),lore=lore)
        model=ScriptedModel([search('可分享表格栏目顺序'),answer,search('可分享表格栏目顺序'),answer])
        result=await story.turn('先核对表格',model,scene_turn_id='t',choice='offer_handover',max_responders=2)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['selected'],['lin_yan','other_npc'])
        l12=next(c.chunk_id for c in snapshot.chunks if c.source_id=='L12')
        self.assertIn(l12,json.dumps(model.requests[:2]))
        self.assertNotIn(l12,json.dumps(model.requests[2:]))
        self.assertIn(l12,json.dumps(reranker.requests[0]))
        self.assertNotIn(l12,json.dumps(reranker.requests[1]))
        self.assertEqual(result['model_requests'],result['chat_requests']+result['embedding_requests']+result['rerank_requests'])

    async def test_cropped_first_hit_cannot_be_cited(self):
        chunk=load_lore().chunks[0]
        hit={**chunk.reference(),'title':chunk.title,'text':chunk.text,'rank':1,'score':1}
        item={'ok':True,'data':{'status':'OK','hits':[hit]},'error':None,'call_id':'q'}
        messages=[{'role':'assistant','content':None,'tool_calls':[call('search_lore',call_id='q',
                  arguments='{"query":"灯塔","top_k":1}')]},
                  {'role':'tool','tool_call_id':'q','content':json.dumps(item,ensure_ascii=False)}]
        packed,delivered=pack_lore(messages,[],input_chars(messages,[])-1)
        self.assertEqual(delivered,{})
        bad=terminal('wait',reply='使用被裁掉的依据',reason='依据',source_refs=[],lore_refs=[chunk.chunk_id])
        with self.assertRaises(ValueError):
            parse_lore_terminal(bad['message']['tool_calls'][0],[],delivered)

    async def test_real_reranker_protocol_has_no_tools_and_fixed_token_limit(self):
        completion=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(tool_calls=None,content='{"ranked_chunk_ids":["A"]}'))])
        create=AsyncMock(return_value=completion)
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),close=AsyncMock())
        real=RealReranker('dummy','configured-model','https://example.test/v1',client=client)
        result=await real.rank([{'role':'user','content':'fixture'}])
        self.assertEqual(result,{'ranked_chunk_ids':['A']})
        self.assertEqual(create.call_args.kwargs['max_tokens'],512)
        self.assertNotIn('tools',create.call_args.kwargs)
        await real.aclose()
        client.close.assert_awaited_once()

    async def test_invalid_startup_save_sends_no_embedding_or_chat_requests(self):
        lore=LoreRetriever(load_lore())
        story=SceneStory(create_world('invalid'),lore=lore,batch=BatchBudget(180))
        model=ScriptedModel([]);model.aclose=AsyncMock()
        limits=RunLimits(max_input_chars=8000)
        payload=snapshot_story(story,model_config=model_identity(model,limits))
        payload['world']['owners']['envelope_01']='actor:other_npc'
        args=SimpleNamespace(scene_profile='a08',enable_real=True,load_slot='slot',max_model_requests=12,
            lore_mode='keyword',max_responders=1,narrate_ending=False)
        with patch('app.scene_cli.read_scene',return_value=payload),patch('app.scene_cli.load_model',return_value=model),\
                patch('app.retrieval.build_index',new=AsyncMock()) as build:
            with self.assertRaises(ValueError):
                await configured_chat(args,limits)
        build.assert_not_awaited()
        self.assertEqual(model.requests,[])
        model.aclose.assert_awaited_once()

    async def test_teaching_profile_requires_explicit_real_enable(self):
        args=SimpleNamespace(scene_profile='a08',enable_real=False)
        with patch('app.scene_cli.load_model') as model:
            with self.assertRaises(ValueError):
                await configured_chat(args,RunLimits())
            model.assert_not_called()

    async def test_save_limits_and_orphan_history_validation(self):
        story=SceneStory(create_world('limits-save'))
        limits=RunLimits(max_model_requests=3,turn_timeout_s=12,max_input_chars=8000)
        await story.turn('hello',ScriptedModel([
            terminal('wait',reply='你好',reason='问候',source_refs=[])]),scene_turn_id='t',limits=limits)
        payload=snapshot_story(story)
        restored=restore_story(payload)
        self.assertEqual(restored.turn_limits,limits)
        payload['conversations']['other_npc']['history']=[
            {'role':'user','content':'foreign'},{'role':'assistant','content':'injected'}]
        with self.assertRaises(ValueError):
            restore_story(payload)

    async def test_invalid_gold_is_rejected_before_evaluation(self):
        rows=[json.loads(line) for line in DEV_PATH.read_text(encoding='utf-8').splitlines()]
        rows[8]['actor_id']='other_npc'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'invalid.jsonl'
            path.write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
            with self.assertRaises(ValueError):
                load_dev(load_lore(),path)

    async def test_concurrent_stories_share_one_batch_request(self):
        batch=BatchBudget(1)
        stories=[SceneStory(create_world(f'batch-{i}'),batch=batch) for i in range(2)]
        async def delayed(messages):
            await asyncio.sleep(.01)
            return terminal('wait',reply='hello',reason='wait',source_refs=[])
        models=[ScriptedModel([delayed]) for _ in stories]
        results=await asyncio.gather(*[
            story.turn('hello',model,scene_turn_id='t') for story,model in zip(stories,models)])
        self.assertEqual(batch.used,1)
        self.assertEqual(sum(len(m.requests) for m in models),1)
        self.assertEqual(sum(r['model_requests'] for r in results),1)
        self.assertIn('MODEL_REQUEST_LIMIT',[r['error_code'] for r in results])

    async def test_save_rejects_unauthorized_citations_and_false_scene_projection(self):
        lore=LoreRetriever(load_lore())
        story=SceneStory(create_world('saved-refs'),lore=lore)
        await story.turn('值班室',ScriptedModel([search('值班室'),answer]),scene_turn_id='t')
        payload=snapshot_story(story)
        private=next(c for c in lore.snapshot.chunks if c.source_id=='L07')
        payload['records']['t']['result']['responses'][0]['lore_refs']=[private.reference()]
        with self.assertRaisesRegex(ValueError,'SAVE_LORE_REFERENCE_UNAVAILABLE'):
            restore_story(payload,lore=lore)
        payload=snapshot_story(story)
        payload['records']['t']['result']['state']['ending']='handed_over'
        with self.assertRaisesRegex(ValueError,'SAVE_SCENE_STATE_MISMATCH'):
            restore_story(payload,lore=lore)

    async def test_explicit_lower_request_cap_is_honored_in_teaching_profile(self):
        import io
        from contextlib import redirect_stdout
        from app.main import main
        with patch('app.scene_cli.main',return_value=0) as entry:
            main(['--engine','scene','--scene-profile','a08','--max-model-requests','1'])
        self.assertTrue(entry.call_args.args[0].request_cap_explicit)
        args=SimpleNamespace(scene_profile='a08',enable_real=True,load_slot=None,max_model_requests=1,
            request_cap_explicit=True,lore_mode='off',max_responders=1,narrate_ending=False)
        model=ScriptedModel([]);model.aclose=AsyncMock()
        with redirect_stdout(io.StringIO()),patch('app.scene_cli.load_model',return_value=model),                patch('app.scene_cli.chat',new=AsyncMock(return_value=0)) as run:
            await configured_chat(args,RunLimits(max_input_chars=8000))
        story=run.call_args.kwargs['story']
        self.assertEqual(story.batch.max_requests,1)
        self.assertEqual(story.max_story_requests,1)
        self.assertEqual(model.requests,[])
