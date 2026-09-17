"""A07 三条路径；Fake 只验证程序边界，不验证真实语义与生成质量。"""

import asyncio
import json
from pathlib import Path

from app.embeddings import EmbeddingCache, FakeEmbedding
from app.execution import RunBudget, RunLimits
from app.lore import load_lore
from app.retrieval import LoreRetriever, build_index
from app.scene_runtime import SceneStory
from app.trace import RunTrace
from app.world import create_world
from scripts.a04_demo import ScriptedModel, call, response, terminal


def budget(**overrides):
    return RunBudget(RunLimits(retry_delay_s=0, **overrides), RunTrace('a07', 'probe', mode='fake'))


def search(query, top_k=3, call_id='lore-query'):
    return response(calls=[call('search_lore', call_id=call_id,
                               arguments=json.dumps({'query': query, 'top_k': top_k}, ensure_ascii=False))])


def answer(messages):
    item = json.loads(messages[-1]['content'])
    hits = item['data']['hits'] if item['ok'] else []
    if hits:
        hit = hits[0]
        reply = hit['text'] + f" [{hit['source_id']}@{hit['source_version']}]"
    else:
        reply = '没有可用资料，暂时无法回答；请提供可核对的说明。'
    return terminal(reply=reply, reason='只采用实际收到的资料，资料不证明世界事件。',
                    source_refs=[], lore_refs=[hit['chunk_id'] for hit in hits[:1]])


async def demo(output=Path('docs/a07_retrieval_probe.json')):
    snapshot = load_lore()
    evidence = {'mode': 'fake', 'version': snapshot.version, 'cases': []}
    cache = EmbeddingCache(FakeEmbedding())
    try:
        index = await build_index(snapshot, cache, budget(max_embedding_requests=8))
        for mode in ('keyword', 'vector_fake'):
            lore = LoreRetriever(snapshot) if mode == 'keyword' else LoreRetriever(
                snapshot, mode, cache, index, min_score=0.25)
            for name, query in [('supported', '值班室用于什么？'), ('unknown', '地下通道密码是什么？'),
                                ('unauthorized', '沈岚旧目录规则的别名是什么？')]:
                story = SceneStory(create_world('a07-' + mode + '-' + name), lore=lore)
                before = story.engine.world
                model = ScriptedModel([search(query), answer])
                result = await story.turn(query, model, scene_turn_id='T1')
                assert result['status'] == 'completed', result
                wire = json.dumps(model.requests, ensure_ascii=False)
                assert all(secret not in wire for secret in ('潮序', '沈岚目录别名', 'L10', '青榆', 'L07'))
                assert story.engine.world.owners == before.owners
                assert story.engine.world.actor_locations == before.actor_locations
                assert not any(e['kind'] in ('DiscoveryEvent', 'TransferEvent') for e in story.engine.world.events)
                replay_model = ScriptedModel([])
                replay = await story.turn(query, replay_model, scene_turn_id='T1')
                assert replay['replayed'] and replay['model_requests'] == 0 and not replay_model.requests
                evidence['cases'].append({'name': name, 'retrieval_mode': mode, 'query': query,
                    'result': result, 'requests': model.requests, 'audit': story.audit_records})
        evidence['fake_embedding_calls'] = cache.adapter.calls
    finally:
        await cache.aclose()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'cases': len(evidence['cases']), 'mode': 'fake', 'evidence': str(output)}


if __name__ == '__main__':
    print(json.dumps(asyncio.run(demo()), ensure_ascii=False, indent=2))
