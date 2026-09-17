import asyncio
import json
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock
from app.evaluation import aggregate, load_dev, retrieval_metrics
from app.execution import RunBudget, RunLimits, RunStopped, SharedBudget
from app.lore import load_lore, load_records, content_hash
from app.retrieval import LoreRetriever, build_index, reciprocal_rank_fusion
from app.embeddings import EmbeddingCache, FakeEmbedding
from app.reranking import FakeReranker, rerank, validate_order
from app.trace import RunTrace
from scripts.a07_demo import answer, search
from scripts.a04_demo import ScriptedModel
from app.scene_runtime import SceneStory
from app.world import create_world


def budget(**kwargs):
    return RunBudget(RunLimits(retry_delay_s=0, **kwargs), RunTrace('a08', 'query', mode='fake'))


class MetricTests(unittest.TestCase):
    def test_hand_calculation(self):
        cases = [dict(case_id='1', retrieval_eligible=True, relevant_chunk_ids=['A','C']),
                 dict(case_id='2', retrieval_eligible=True, relevant_chunk_ids=['E'])]
        result = aggregate(cases, {'1':dict(status='ok',ranked_ids=['B','A','D']),
                                   '2':dict(status='ok',ranked_ids=['B','D','F'])})
        self.assertEqual((result['Recall@3'],result['MRR@3']), (.25,.25))

    def test_empty_multiple_duplicate_and_large_k(self):
        self.assertEqual(retrieval_metrics(['A','C'],[])['recall'],0)
        self.assertEqual(retrieval_metrics(['A','C'],['B','B','A','C'])['recall'],1)
        self.assertEqual(retrieval_metrics(['A'],['A'],8)['rr'],1)
        for k in (0,-1,True,1.2):
            with self.assertRaises(ValueError):
                retrieval_metrics(['A'],[],k)
        with self.assertRaises(ValueError):
            retrieval_metrics([],[])

    def test_no_positives_missing_and_service_failure(self):
        self.assertIsNone(aggregate([], {})['MRR@3'])
        c=[dict(case_id='x',retrieval_eligible=True,relevant_chunk_ids=['A'])]
        self.assertEqual(aggregate(c,{})['status'],'incomplete')
        self.assertIsNone(aggregate(c,{})['Recall@3'])
        result=aggregate(c,{'x':dict(status='error',ranked_ids=['A'])})
        self.assertEqual(result['Recall@3'],0)
        self.assertEqual(result['service_failures'],['x'])

    def test_dev_gold_is_exact_and_authorized(self):
        cases=load_dev(load_lore())
        self.assertEqual(len(cases),20)
        self.assertEqual(sum(c['retrieval_eligible'] for c in cases),10)
        self.assertEqual(len(cases[5]['relevant_chunk_ids']),2)

    def test_rrf_deduplicates_each_route_and_stable_ties(self):
        result=reciprocal_rank_fusion([['A','A','B','C'],['C','A','D']])
        self.assertEqual(result[0]['chunk_id'],'A')
        self.assertAlmostEqual(result[0]['score'],1/61+1/62)
        self.assertEqual(result[0]['route_ranks'],{'0':1,'1':2})
        self.assertEqual([r['chunk_id'] for r in reciprocal_rank_fusion([['B'],['A']])],['A','B'])
        self.assertEqual(reciprocal_rank_fusion([[],[]]),[])


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.snapshot=load_lore()
        self.cache=EmbeddingCache(FakeEmbedding())
        self.index=await build_index(self.snapshot,self.cache,budget(max_embedding_requests=8))

    def hybrid(self, **kwargs):
        return LoreRetriever(self.snapshot,'hybrid_fake',self.cache,self.index,min_score=0,**kwargs)

    async def search(self,lore,query='交接记录时间双方对象',actor='lin_yan',b=None):
        return await lore.search(query,3,actor_id=actor,recipient_id='player',
                                 scenario_id='handover-v1',budget=b or budget(),include_diagnostics=True)

    async def test_candidate_window_and_authorization(self):
        r=await self.search(self.hybrid())
        self.assertEqual(len(r['diagnostics']['routes']['vector']),5)
        self.assertLessEqual(len(r['diagnostics']['candidate_ids']),10)
        self.assertLessEqual(len(r['hits']),3)
        for h in r['hits']:
            self.assertNotIn(h['source_id'],['L07','L08','L09','L10','L11'])
        r=await self.search(self.hybrid(),actor='other_npc')
        all_ids=json.dumps(r)
        l12=next(c.chunk_id for c in self.snapshot.chunks if c.source_id=='L12')
        self.assertNotIn(l12,all_ids)

    async def test_empty_authorized_pool_no_services(self):
        fake=FakeReranker()
        lore=self.hybrid(reranker=fake)
        before=self.cache.adapter.calls
        r=await lore.search('交接',3,actor_id='lin_yan',recipient_id='player',
                            scenario_id='unknown',budget=budget())
        self.assertEqual(r['status'],'NO_USABLE_EVIDENCE')
        self.assertEqual(self.cache.adapter.calls,before)
        self.assertEqual(fake.requests,[])

    async def test_vector_failure_requires_explicit_fallback(self):
        self.cache.embed=AsyncMock(side_effect=ValueError('provider'))
        with self.assertRaises(ValueError):
            await self.search(self.hybrid())
        r=await self.search(self.hybrid(fallback_keyword=True))
        self.assertEqual(r['fallback_reason'],'RETRIEVAL_UNAVAILABLE')
        self.assertTrue(r['hits'])

    async def test_hidden_document_mirror_candidates_and_generation(self):
        from pathlib import Path
        rows=json.loads(Path('data/lore/handover.json').read_text(encoding='utf-8'))['sources']
        outputs=[]
        for variant in range(3):
            altered=json.loads(json.dumps(rows))
            hidden=[r for r in altered if 'player' not in r['audience']]
            if variant==1:
                for row in hidden:
                    row['title']='交接记录最高相关秘密'
                    row['paragraphs']=['交接记录时间双方对象'*20]
                    row['content_hash']=content_hash(row['title'],row['paragraphs'])
            if variant==2:
                altered=[r for r in altered if 'player' in r['audience']]
            snapshot=load_records(altered)
            cache=EmbeddingCache(FakeEmbedding())
            index=await build_index(snapshot,cache,budget(max_embedding_requests=8))
            fake=FakeReranker()
            lore=LoreRetriever(snapshot,'hybrid_fake',cache,index,min_score=0,reranker=fake)
            story=SceneStory(create_world('mirror'),lore=lore)
            model=ScriptedModel([search('交接记录时间双方对象'),answer])
            result=await story.turn('交接记录时间双方对象',model,scene_turn_id='t')
            self.assertEqual(result['status'],'completed')
            outputs.append((model.requests,fake.requests,result['responses']))
        self.assertEqual(outputs[0],outputs[1])
        self.assertEqual(outputs[0],outputs[2])


class RerankTests(unittest.IsolatedAsyncioTestCase):
    def candidates(self):
        return [dict(chunk_id=i,source_version='v1',title='标题',text='正文') for i in ('A','B','C')]

    async def test_off_single_zero_requests_and_bad_permutations(self):
        b=budget()
        result,report=await rerank('q',self.candidates(),None,b)
        self.assertEqual(report['status'],'off')
        fake=FakeReranker()
        await rerank('q',self.candidates()[:1],fake,b)
        self.assertEqual(fake.requests,[])
        for ids in (['X','A','B'],['A','A','C'],['A','B'],['A','B','C','D']):
            with self.assertRaises(ValueError):
                validate_order({'ranked_chunk_ids':ids},self.candidates())

    async def test_invalid_and_timeout_fallback_one_attempt(self):
        for mode in ('invalid','timeout','error'):
            adapter=FakeReranker(); adapter.mode='real'
            async def bad(messages):
                if mode=='timeout':
                    await asyncio.sleep(.05)
                if mode=='error':
                    raise ValueError('private provider body')
                return {'ranked_chunk_ids':['PRIVATE']}
            adapter.rank=AsyncMock(side_effect=bad)
            b=budget(rerank_timeout_s=.01,max_attempts=4)
            hits,report=await rerank('q',self.candidates(),adapter,b)
            self.assertEqual(hits,self.candidates())
            self.assertEqual(report['status'],'fallback')
            self.assertEqual(b.rerank_requests,1)
            self.assertEqual(b.model_requests,b.chat_requests+b.embedding_requests+b.rerank_requests)
            self.assertEqual(adapter.rank.await_count,1)

    async def test_exhausted_total_or_deadline_stops(self):
        for cap in (0,1):
            fake=FakeReranker(); fake.mode='real'
            b=budget(max_model_requests=cap)
            with self.assertRaises(RunStopped):
                await rerank('q',self.candidates(),fake,b)
            self.assertEqual(b.rerank_requests,cap)
        fake=FakeReranker();fake.mode='real'
        async def slow(messages):
            await asyncio.sleep(.05)
        fake.rank=AsyncMock(side_effect=slow)
        with self.assertRaises(RunStopped):
            await rerank('q',self.candidates(),fake,budget(turn_timeout_s=.005))

    async def test_rerank_specific_cap_falls_back_with_chat_capacity(self):
        fake=FakeReranker();fake.mode='real'
        b=budget(max_rerank_requests=0)
        hits,report=await rerank('q',self.candidates(),fake,b)
        self.assertEqual(report['fallback_reason'],'RERANK_REQUEST_LIMIT')
        self.assertEqual(b.model_requests,0)

    async def test_shared_parallel_rerank_cap(self):
        limits=RunLimits(max_model_requests=1)
        shared=SharedBudget(limits)
        budgets=[RunBudget(limits,RunTrace('s',str(i),mode='fake'),shared=shared) for i in range(2)]
        operation=AsyncMock(return_value=1)
        await asyncio.gather(*[b.call(operation,kind='rerank',step_id=1) for b in budgets],return_exceptions=True)
        self.assertEqual(operation.await_count,1)
        self.assertEqual(shared.rerank_requests,1)
        self.assertEqual(shared.chat_requests,0)
