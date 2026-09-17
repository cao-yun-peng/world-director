"""先预测，再由学习者改为 True 并解释；AI 已做的边界测试不计独立成绩。"""

import asyncio
import json

from app.lore import SEED_PATH, load_records
from app.retrieval import LoreRetriever
from app.scene_runtime import SceneStory
from app.world import create_world
from scripts.a04_demo import ScriptedModel
from scripts.a07_demo import answer, budget, search

REMOVE_PLAYER_FROM_L12 = False


async def run_variant(remove_player=REMOVE_PLAYER_FROM_L12):
    payload = json.loads(SEED_PATH.read_text(encoding='utf-8'))
    if remove_player:
        payload['version'] = 'a07-lore-v2'
        for row in payload['sources']:
            row['source_version'] = payload['version']
            if row['source_id'] == 'L12':
                row['audience'] = ['lin_yan']
    snapshot = load_records(payload['sources'], version=payload['version'])
    retriever = LoreRetriever(snapshot)
    query = '交接表的栏目顺序是什么？'
    result = {}
    for recipient in ('player', 'lin_yan'):
        data = await retriever.search(query, 3, actor_id='lin_yan', recipient_id=recipient,
                                      scenario_id='handover-v1', budget=budget())
        result[recipient + '_has_L12'] = any(h['source_id'] == 'L12' for h in data['hits'])
    story = SceneStory(create_world('a07-visibility-' + snapshot.version), lore=retriever)
    model = ScriptedModel([search(query), answer])
    outcome = await story.turn(query, model, scene_turn_id='t1')
    result.update(version=snapshot.version, mode='fake', status=outcome['status'],
                  player_request_has_L12='L12' in json.dumps(model.requests, ensure_ascii=False))
    assert result['lin_yan_has_L12']
    assert result['player_has_L12'] == (not remove_player)
    assert result['player_request_has_L12'] == (not remove_player)
    return result


if __name__ == '__main__':
    print(json.dumps(asyncio.run(run_variant()), ensure_ascii=False, indent=2))
