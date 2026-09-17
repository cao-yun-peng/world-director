import asyncio
import json
import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from unittest.mock import AsyncMock, patch

from app.embeddings import EmbeddingCache, EmbeddingSpace, FakeEmbedding, RealEmbedding, validate_vectors
from app.execution import CallFailure, RunStopped, SharedBudget
from app.lore import (SEED_PATH, authorized_chunks, content_hash, load_lore, load_records, validate_query)
from app.retrieval import LoreRetriever, VectorIndex, build_index, cosine
from scripts.a07_demo import budget


def records():
    return json.loads(SEED_PATH.read_text(encoding='utf-8'))['sources']


def rehash(record):
    record['content_hash'] = content_hash(record['title'], record['paragraphs'])
    return record


class LoreTests(unittest.TestCase):
    def test_seed_integrity_and_immutability(self):
        snapshot = load_lore()
        self.assertEqual(len(snapshot.sources), 12)
        self.assertEqual(len(snapshot.chunks), 12)
        self.assertEqual(snapshot, load_lore())
        with self.assertRaises(FrozenInstanceError):
            snapshot.version = 'new'
        self.assertTrue(all(len(c.text) <= 400 for c in snapshot.chunks))

    def test_required_fields_and_fail_closed_acl(self):
        for acl in (None, [], ['admin'], ['public'], 'player', ['player', 'player'], [True]):
            with self.subTest(acl=acl), self.assertRaises(ValueError):
                row = records()[0]; row['audience'] = acl
                load_records([row])
        row = records()[0]; del row['audience']
        with self.assertRaises(ValueError):
            load_records([row])

    def test_unknown_subject_and_scenario_filter(self):
        snapshot = load_lore()
        for actor, recipient in [('admin', 'player'), ('lin_yan', 'admin')]:
            with self.assertRaises(ValueError):
                authorized_chunks(snapshot, actor_id=actor, recipient_id=recipient, scenario_id='handover-v1')
        self.assertEqual(authorized_chunks(snapshot, actor_id='lin_yan', recipient_id='player',
                                           scenario_id='another-scene'), ())

    def test_duplicates_hash_conflict_and_old_version(self):
        row = records()[0]
        self.assertEqual(len(load_records([row, deepcopy(row)]).sources), 1)
        other = deepcopy(row); other['audience'] = ['lin_yan']
        with self.assertRaisesRegex(ValueError, 'CONFLICTING'):
            load_records([row, other])
        other = deepcopy(row); other['paragraphs'] = ['变化']; rehash(other)
        with self.assertRaisesRegex(ValueError, 'CONFLICTING'):
            load_records([row, other])
        other['source_version'] = 'old'
        with self.assertRaisesRegex(ValueError, 'VERSION'):
            load_records([other])
        row['paragraphs'] = ['变了但哈希未改']
        with self.assertRaisesRegex(ValueError, 'HASH'):
            load_records([row])

    def test_empty_docs_rejected_blank_paragraphs_skipped(self):
        for paragraphs in ([], [''], [' \n']):
            row = records()[0]; row['paragraphs'] = paragraphs; rehash(row)
            with self.assertRaisesRegex(ValueError, 'EMPTY'):
                load_records([row])
        row = records()[0]; row['paragraphs'] = ['', '没有转移。', ' ']; rehash(row)
        chunk = load_records([row]).chunks[0]
        self.assertEqual(chunk.paragraph, 1)
        self.assertEqual(chunk.text, '没有转移。')

    def test_codepoint_offsets_newlines_and_no_permission_merging(self):
        rows = [records()[0], records()[6]]
        rows[0]['paragraphs'] = ['界' * 399 + '🙂' + '不。\n尾']; rehash(rows[0])
        rows[1]['paragraphs'] = ['私密']; rehash(rows[1])
        snapshot = load_records(rows)
        self.assertEqual([len(c.text) for c in snapshot.chunks], [400, 4, 2])
        for chunk in snapshot.chunks:
            source = next(s for s in snapshot.sources if s.source_id == chunk.source_id)
            self.assertEqual(chunk.text, source.paragraphs[chunk.paragraph][chunk.start:chunk.end])
            self.assertEqual(chunk.audience, source.audience)
        self.assertNotEqual(snapshot.chunks[0].audience, snapshot.chunks[-1].audience)
        rows[0]['paragraphs'] = ['甲\r\n乙']; rows[0]['content_hash'] = content_hash(rows[0]['title'], ['甲\n乙'])
        self.assertEqual(load_records(rows).chunks[0].text, '甲\n乙')

    def test_size_limit_and_strict_query(self):
        row = records()[0]; row['paragraphs'] = ['字' * 10001]; rehash(row)
        with self.assertRaisesRegex(ValueError, 'CHUNK_LIMIT'):
            load_records([row])
        for query, k in [('', 1), (' ' * 5, 1), ('字' * 201, 1), ('查', True), ('查', 1.0),
                         ('查', -1), ('查', 0), ('查', 4), (None, 1)]:
            with self.subTest(query=query, k=k), self.assertRaises(ValueError):
                validate_query({'query': query, 'top_k': k})
        for key in ('actor_id', 'viewer_id', 'admin', 'path', 'index_path'):
            with self.assertRaises(ValueError):
                validate_query({'query': '查', 'top_k': 1, key: 'fake'})

    def test_vector_validation_math(self):
        self.assertAlmostEqual(cosine([1, 2, 3], [1, 2, 3]), 1)
        self.assertAlmostEqual(cosine([1, 0], [0, 1]), 0)
        for vectors, count, dim in [([], 1, 2), ([[1]], 1, 2), ([[0, 0]], 1, 2),
                                  ([[float('nan'), 1]], 1, 2), ([[float('inf'), 1]], 1, 2),
                                  ([[True, 1]], 1, 2)]:
            with self.subTest(vectors=vectors), self.assertRaises(ValueError):
                validate_vectors(vectors, count, dim)
        with self.assertRaises(ValueError):
            cosine([1], [1, 2])


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def search(self, retriever, query, actor='lin_yan', recipient='player', top_k=3):
        return await retriever.search(query, top_k, actor_id=actor, recipient_id=recipient,
                                      scenario_id='handover-v1', budget=budget())

    async def backends(self, snapshot=None):
        snapshot = snapshot or load_lore()
        cache = EmbeddingCache(FakeEmbedding())
        index = await build_index(snapshot, cache, budget())
        return [LoreRetriever(snapshot), LoreRetriever(snapshot, 'vector_fake', cache, index)]

    async def test_fixed_queries_both_backends(self):
        for retriever in await self.backends():
            for query, actor, expected in [('值班室用于什么？', 'lin_yan', 'L02'),
                ('交接记录通常包括哪些内容？', 'other_npc', 'L04'),
                ('交接表的栏目顺序是什么？', 'lin_yan', 'L12')]:
                result = await self.search(retriever, query, actor)
                self.assertEqual(result['hits'][0]['source_id'], expected)
                self.assertEqual(result, await self.search(retriever, query, actor))

    async def test_empty_and_private_metadata_absent(self):
        for retriever in await self.backends():
            for query in ('地下通道密码是什么？', '沈岚旧目录规则的别名是什么？'):
                result = await self.search(retriever, query)
                self.assertEqual(result['status'], 'NO_USABLE_EVIDENCE')
                self.assertEqual(result['hits'], [])
                self.assertNotIn('candidate_count', result)
            private = await self.search(retriever, '沈岚旧目录规则的别名是什么？', 'archive_keeper', 'archive_keeper')
            self.assertEqual(private['hits'][0]['source_id'], 'L10')
            self.assertEqual((await self.search(retriever, '沈岚旧目录规则的别名是什么？', 'archive_keeper'))['hits'], [])

    async def test_prefilter_before_top_one_and_secret_mirror(self):
        outputs = []
        for secret in ('A-hidden-917', 'B-hidden-821'):
            rows = records()
            rows[6]['title'] = secret
            rows[6]['paragraphs'] = ['值班室用于什么？ 值班室用途 ' * 4 + secret]; rehash(rows[6])
            results = []
            for retriever in await self.backends(load_records(rows)):
                result = await self.search(retriever, '值班室用于什么？', top_k=1)
                self.assertEqual(result['hits'][0]['source_id'], 'L02')
                self.assertNotIn(secret, json.dumps(result))
                results.append(result)
            outputs.append(results)
        self.assertEqual(*outputs)

    async def test_empty_authorized_pool_does_not_embed_query(self):
        rows = [records()[6]]
        cache = EmbeddingCache(FakeEmbedding())
        snapshot = load_records(rows)
        index = await build_index(snapshot, cache, budget())
        calls = cache.adapter.calls
        retriever = LoreRetriever(snapshot, 'vector_fake', cache, index)
        self.assertEqual((await self.search(retriever, '代号'))['hits'], [])
        self.assertEqual(cache.adapter.calls, calls)
        empty = load_records([])
        index = await build_index(empty, cache, budget())
        self.assertEqual(index.vectors, ())

    async def test_wrong_index_space_and_fake_real_confusion_rejected(self):
        snapshot = load_lore(); cache = EmbeddingCache(FakeEmbedding())
        index = await build_index(snapshot, cache, budget())
        for wrong in (replace(index, space=replace(index.space, model='other')),
                      replace(index, vectors=((0.0,) * 256,) * 12), replace(index, chunks=())):
            with self.assertRaises(ValueError):
                LoreRetriever(snapshot, 'vector_fake', cache, wrong)
        with self.assertRaises(ValueError):
            LoreRetriever(snapshot, 'vector_real', cache, index)

    async def test_visibility_variant_new_snapshot_keeps_old_isolated(self):
        old = load_lore(); rows = records()
        for row in rows:
            row['source_version'] = 'a07-lore-v2'
        rows[-1]['audience'] = ['lin_yan']
        new = load_records(rows, version='a07-lore-v2')
        cache = EmbeddingCache(FakeEmbedding())
        old_index = await build_index(old, cache, budget()); calls = cache.adapter.calls
        new_index = await build_index(new, cache, budget())
        self.assertEqual(cache.adapter.calls, calls)  # 计算复用不代表授权复用。
        old_r = LoreRetriever(old, 'vector_fake', cache, old_index)
        new_r = LoreRetriever(new, 'vector_fake', cache, new_index)
        self.assertEqual((await self.search(old_r, '交接表的栏目顺序是什么？'))['hits'][0]['source_id'], 'L12')
        self.assertNotIn('L12', [h['source_id'] for h in (await self.search(new_r, '交接表的栏目顺序是什么？'))['hits']])
        self.assertEqual((await self.search(new_r, '交接表的栏目顺序是什么？', recipient='lin_yan'))['hits'][0]['source_id'], 'L12')


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_dedup_concurrency_and_defensive_copies(self):
        cache = EmbeddingCache(FakeEmbedding())
        results = await asyncio.gather(*[cache.embed(['同文', '同文'], purpose='query', budget=budget()) for _ in range(2)])
        self.assertEqual(cache.adapter.calls, 1)
        results[0]['vectors'][0][0] = 987
        self.assertNotEqual(results[0]['vectors'][0], results[0]['vectors'][1])
        self.assertNotEqual(results[0]['vectors'], results[1]['vectors'])
        await cache.aclose(); self.assertTrue(cache.adapter.closed)

    async def test_cache_space_text_purpose_and_preprocess_invalidation(self):
        adapter = FakeEmbedding(); cache = EmbeddingCache(adapter)
        async def embed(text='同文', purpose='query'):
            return await cache.embed([text], purpose=purpose, budget=budget())
        await embed(); await embed(); self.assertEqual(adapter.calls, 1)
        await embed('改文'); await embed(purpose='document'); self.assertEqual(adapter.calls, 3)
        for change in ({'model': 'v2'}, {'preprocessing': 'v2'}, {'mode': 'real'}, {'query_prefix': '问：'}):
            adapter.space = replace(adapter.space, **change)
            await embed()
        self.assertEqual(adapter.calls, 7)

    async def test_repeated_build_has_no_new_requests(self):
        cache = EmbeddingCache(FakeEmbedding()); snapshot = load_lore()
        first = await build_index(snapshot, cache, budget())
        calls = cache.adapter.calls
        self.assertEqual(await build_index(snapshot, cache, budget()), first)
        self.assertEqual(cache.adapter.calls, calls)

    async def test_bad_response_never_cached_and_failed_build_not_published(self):
        adapter = FakeEmbedding(); cache = EmbeddingCache(adapter)
        original = adapter.embed
        for vectors in ([], [[0.0] * 256], [[float('nan')] * 256], [[1.0]]):
            adapter.embed = AsyncMock(return_value=vectors)
            with self.assertRaises(ValueError):
                await cache.embed(['未缓存'], purpose='document', budget=budget())
            self.assertEqual(cache._entries, {})
        adapter.embed = original
        await build_index(load_lore(), cache, budget())
        previous_entries = deepcopy(cache._entries)
        rows = records(); rows[0]['paragraphs'] = ['新内容']; rehash(rows[0])
        adapter.embed = AsyncMock(side_effect=RuntimeError('failure'))
        with self.assertRaises(CallFailure):
            await build_index(load_records(rows), cache, budget())
        self.assertEqual(cache._entries, previous_entries)

    async def test_timeout_cancel_failure_not_cached_and_lock_released(self):
        adapter = FakeEmbedding(); cache = EmbeddingCache(adapter)
        entered = asyncio.Event()
        async def blocked(texts, purpose):
            entered.set(); await asyncio.Event().wait()
        adapter.embed = blocked
        task = asyncio.create_task(cache.embed(['取消'], purpose='query', budget=budget()))
        await entered.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(cache._entries, {}); self.assertFalse(cache._lock.locked())
        with self.assertRaises(CallFailure):
            await cache.embed(['超时'], purpose='query', budget=budget(query_attempt_timeout_s=.01, max_attempts=1))
        self.assertEqual(cache._entries, {})

    async def test_real_adapter_protocol_close_and_missing_config(self):
        from types import SimpleNamespace
        client = SimpleNamespace(embeddings=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(
            data=[SimpleNamespace(index=1, embedding=[0, 1]), SimpleNamespace(index=0, embedding=[1, 0])]))), close=AsyncMock())
        adapter = RealEmbedding('test-key', 'test-model', 'https://provider.invalid/v1', 2,
                                allow_upload=True, client=client)
        self.assertEqual(await adapter.embed(['甲', '乙'], 'document'), [[1, 0], [0, 1]])
        self.assertEqual(client.embeddings.create.call_args.kwargs['encoding_format'], 'float')
        await adapter.aclose(); client.close.assert_awaited_once()
        with patch.dict('os.environ', {}, clear=True), self.assertRaisesRegex(ValueError, 'NOT_CONFIGURED'):
            RealEmbedding.from_env(allow_upload=True)
        with self.assertRaisesRegex(ValueError, 'UPLOAD'):
            RealEmbedding('key', 'model', 'https://provider.invalid', 2)

    async def test_embedding_shared_limits_and_retry_charge(self):
        from app.execution import RunLimits, RunBudget, TransientFailure
        from app.trace import RunTrace
        limits = RunLimits(max_model_requests=3, max_embedding_requests=1, retry_delay_s=0)
        shared = SharedBudget(limits)
        b = RunBudget(limits, RunTrace('s', 't', mode='fake'), shared=shared)
        with self.assertRaisesRegex(RunStopped, 'EMBEDDING_REQUEST_LIMIT'):
            await b.call(AsyncMock(side_effect=TransientFailure()), kind='embedding', step_id=1)
        self.assertEqual(shared.model_requests, 1); self.assertEqual(shared.embedding_requests, 1)
        await b.call(AsyncMock(return_value={}), kind='model', step_id=2)
        self.assertEqual(shared.chat_requests, 1)
        other = RunBudget(limits, RunTrace('s', 'u', mode='fake'), shared=shared)
        with self.assertRaisesRegex(RunStopped, 'EMBEDDING_REQUEST_LIMIT'):
            await other.call(AsyncMock(), kind='embedding', step_id=1)
        self.assertEqual(shared.model_requests, 2)


if __name__ == '__main__':
    unittest.main()
