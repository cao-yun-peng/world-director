import asyncio
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.embeddings import EmbeddingCache, FakeEmbedding
from app.execution import CallFailure, RunBudget, RunLimits, RunStopped, SharedBudget
from app.lore import load_lore, load_records
from app.lore_setup import prepare_lore
from app.retrieval import LoreRetriever, build_index
from app.scene_cli import chat
from app.scene_runtime import SceneStory
from app.trace import RunTrace
from app.world import create_world
from scripts.a04_demo import ScriptedModel, terminal
from scripts.a07_demo import answer, budget, search
from test_a07_lore import records, rehash


class ExtraBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cold_second_batch_failure_publishes_nothing(self):
        adapter = FakeEmbedding()
        original = adapter.embed
        async def fail_second(texts, purpose):
            if adapter.calls:
                raise RuntimeError('second batch')
            return await original(texts, purpose)
        adapter.embed = fail_second
        cache = EmbeddingCache(adapter)
        with self.assertRaises(CallFailure):
            await build_index(load_lore(), cache, budget())
        self.assertEqual(cache._entries, {})

    async def test_waiting_cache_deadline_does_not_start_request(self):
        adapter = FakeEmbedding(); cache = EmbeddingCache(adapter)
        await cache._lock.acquire()
        try:
            with self.assertRaises(TimeoutError):
                await cache.embed(['超时'], purpose='query', budget=budget(turn_timeout_s=.02))
        finally:
            cache._lock.release()
        self.assertEqual(adapter.calls, 0)

    async def test_parallel_external_claims_cannot_exceed_total(self):
        limits = RunLimits(max_model_requests=1, max_embedding_requests=2)
        shared = SharedBudget(limits)
        operation = AsyncMock(return_value={'message': {}})
        budgets = [RunBudget(limits, RunTrace('s', str(i), mode='fake'), shared=shared) for i in range(2)]
        results = await asyncio.gather(*[b.call(operation, kind='embedding', step_id=1) for b in budgets],
                                       return_exceptions=True)
        self.assertEqual(shared.model_requests, 1)
        self.assertEqual(operation.await_count, 1)
        self.assertEqual(sum(isinstance(r, RunStopped) for r in results), 1)

    async def test_ties_use_stable_chunk_id_and_threshold_filters_candidates(self):
        rows = records()[:2]
        for row in rows:
            row['title'] = '相同'; row['paragraphs'] = ['相同正文']; rehash(row)
        snapshot = load_records(rows); cache = EmbeddingCache(FakeEmbedding())
        index = await build_index(snapshot, cache, budget())
        for retriever in (LoreRetriever(snapshot), LoreRetriever(snapshot, 'vector_fake', cache, index, min_score=0)):
            result = await retriever.search('相同正文', 2, actor_id='lin_yan', recipient_id='player',
                                            scenario_id='handover-v1', budget=budget())
            ids = [h['chunk_id'] for h in result['hits']]
            self.assertEqual(ids, sorted(ids))
        retriever = LoreRetriever(snapshot, 'vector_fake', cache, index, min_score=1)
        result = await retriever.search('不相干的查询', 2, actor_id='lin_yan', recipient_id='player',
                                        scenario_id='handover-v1', budget=budget())
        self.assertEqual(result['hits'], [])

    async def test_retrieval_body_limit(self):
        rows = records()[:1]; rows[0]['paragraphs'] = ['值班室' * 400]; rehash(rows[0])
        result = await LoreRetriever(load_records(rows)).search('值班室', 3, actor_id='lin_yan',
                recipient_id='player', scenario_id='handover-v1', budget=budget())
        self.assertLessEqual(sum(len(h['text']) for h in result['hits']), 1200)

    async def test_build_cancellation_and_timeout_close_adapter(self):
        for error in (asyncio.CancelledError(), TimeoutError()):
            adapter = FakeEmbedding()
            with patch('app.lore_setup.FakeEmbedding', return_value=adapter), \
                 patch('app.lore_setup.build_index', new=AsyncMock(side_effect=error)):
                with self.assertRaises((asyncio.CancelledError, RunStopped)):
                    await prepare_lore('vector_fake', build=True)
            self.assertTrue(adapter.closed)

    async def test_cli_new_keeps_lore_and_never_resets_process_budget(self):
        story = SceneStory(create_world('a07-cli'), lore=LoreRetriever(load_lore()), max_story_requests=2)
        model = ScriptedModel([search('值班室'), answer]); model.aclose = AsyncMock()
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(output), \
             patch('builtins.input', side_effect=['值班室', '/retry', '/new', '值班室', '/exit']):
            await chat(model, limits=RunLimits(max_input_chars=8000), max_model_requests=2,
                       story=story, trace_path=Path(folder) / 'trace.jsonl')
        self.assertEqual(len(model.requests), 2)
        self.assertIn('MODEL_REQUEST_LIMIT', output.getvalue())
        self.assertIn('A07', output.getvalue())
        model.aclose.assert_awaited_once()

    async def test_actor_switch_cannot_expose_private_lore_to_player(self):
        story = SceneStory(create_world('a07-archive'), lore=LoreRetriever(load_lore()))
        model = ScriptedModel([search('沈岚旧目录规则的别名是什么？'), answer])
        result = await story.turn('沈岚旧目录规则的别名是什么？', model,
                                  scene_turn_id='t1', focus_actor='archive_keeper')
        self.assertEqual(result['status'], 'completed')
        wire = json.dumps(model.requests, ensure_ascii=False)
        for private in ('L10', '潮序', '沈岚目录别名'):
            self.assertNotIn(private, wire)

    async def test_lore_ending_guard_and_request_totals(self):
        story = SceneStory(create_world('a07-ended'), lore=LoreRetriever(load_lore()))
        model = ScriptedModel([terminal('wait', reply='本次暂缓。', reason='按选择', source_refs=[], lore_refs=[])])
        result = await story.turn('暂缓', model, scene_turn_id='t1', choice='pause_handover')
        self.assertEqual(result['state']['ending'], 'deferred')
        stopped = await story.turn('值班室', ScriptedModel([]), scene_turn_id='t2')
        self.assertEqual(stopped['error_code'], 'STORY_ENDED')
        self.assertEqual(stopped['model_requests'], 0)

    async def test_sql_reference_rolls_back_explicitly(self):
        from exercises.a07_sql import run_experiment
        result = run_experiment()
        self.assertEqual(result['before_explicit_rollback_count'], 6)
        self.assertEqual(result['after_rollback_count'], 4)
        self.assertTrue(result['rollback_restored'])
        self.assertEqual(result['group']['rows'], [('lin_yan', 1, 1), ('public', 3, 2)])


if __name__ == '__main__':
    unittest.main()
