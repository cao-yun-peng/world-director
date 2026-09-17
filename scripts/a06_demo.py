"""A06 两个独立开局及玩家改选实验；默认且仅使用 Fake，不读取密钥。"""

import asyncio
import json
from pathlib import Path

from app.scene_runtime import SceneStory
from app.world import create_world
from scripts.a04_demo import ScriptedModel, response, terminal


def decision(story, name='wait', *, reason='依据本章目标核对下一步。', **arguments):
    """Fake 固定提议，只用已授权的开局事件作为理由依据。"""
    source = next(e['event_id'] for e in story.engine.world.events if e['kind'] == 'SceneStartedEvent')
    return terminal(name, reason=reason, source_refs=[source], **arguments)


async def branch(choice):
    story = SceneStory(create_world('a06-' + choice))
    if choice == 'offer_handover':
        replies = [decision(story, 'give', object_id='envelope_01', recipient_id='other_npc'),
                   response('交接已经完成。')]
    else:
        replies = [decision(story, reply='先保留信封，交接暂缓。')]
    model = ScriptedModel(replies)
    initial = {'owners': story.engine.world.owners, 'locations': story.engine.world.actor_locations}
    result = await story.turn('请按我的选择处理交接。', model, scene_turn_id='branch', choice=choice)
    return story, result, {'mode': 'fake', 'session_id': story.engine.world.session_id,
                           'initial': initial, 'result': result, 'requests': model.requests,
                           'actor_proposals': [{'actor_id': key[1], 'turn_id': key[2],
                                                'proposal': record['proposal'], 'receipt': record['receipt']}
                                               for key, record in story.engine.turns.items()],
                           'events': story.engine.world.events, 'plans': story.director.plans,
                           'audit': story.audit_records}


async def replan_case():
    story = SceneStory(create_world('a06-player-changes-choice'))
    first = await story.turn('先核对，暂时不要给物。',
        ScriptedModel([decision(story, reply='我会先核对交接条件。')]),
        scene_turn_id='offer', choice='offer_handover')
    before = story.director.plans
    second = await story.turn('改为本次暂缓。',
        ScriptedModel([decision(story, reply='好，本次保留信封。')]),
        scene_turn_id='pause', choice='pause_handover')
    return {'first': first, 'plans_before_change': before, 'second': second,
            'plans_after_change': story.director.plans, 'events': story.engine.world.events}


async def demo(output=Path('docs/a06_branches.json')):
    offered, a, evidence_a = await branch('offer_handover')
    paused, b, evidence_b = await branch('pause_handover')
    assert evidence_a['initial'] == evidence_b['initial']
    assert a['state']['ending'] == 'handed_over' and a['state']['thread_status'] == 'resolved'
    assert b['state']['ending'] == 'deferred' and b['state']['thread_status'] != 'resolved'
    assert offered.engine.world.owners['envelope_01'] == 'actor:other_npc'
    assert paused.engine.world.owners['envelope_01'] == 'actor:lin_yan'
    changed = await replan_case()
    old = changed['plans_before_change'][-1]['plan_id']
    assert next(p for p in changed['plans_after_change'] if p['plan_id'] == old)['status'] == 'invalidated'
    assert not any(e['kind'] == 'TransferEvent' for e in changed['events'])
    evidence = {'mode': 'fake', 'branches': [evidence_a, evidence_b], 'replan': changed}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'offer': a['state'], 'pause': b['state'], 'replan': changed['second']['state'],
            'evidence': str(output)}


if __name__ == '__main__':
    print(json.dumps(asyncio.run(demo()), ensure_ascii=False, indent=2))
