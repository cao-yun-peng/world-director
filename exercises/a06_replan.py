"""学习者先预测，再改 MOVE_RECIPIENT；AI 演示不计入独立练习。"""

import asyncio
import json

from app.actions import ActionProposal
from app.scene_runtime import SceneStory
from app.world import create_world
from scripts.a04_demo import ScriptedModel, response
from scripts.a06_demo import decision

MOVE_RECIPIENT = False


async def run_variant(move_recipient=MOVE_RECIPIENT):
    story = SceneStory(create_world('a06-replan-exercise'))
    await story.turn('先核对交接计划。', ScriptedModel([decision(story, reply='先核对。')]),
                     scene_turn_id='plan', choice='offer_handover')
    old_plan = story.director.plans[-1]
    if move_recipient:
        story.engine.submit_action(ActionProposal('move', destination_id='storage_room'),
                                    actor_id='other_npc', turn_id='receiver-leaves')
    before = story.engine.world
    model = ScriptedModel([decision(story, 'give', object_id='envelope_01', recipient_id='other_npc'),
                           response('交接完成。')])
    result = await story.turn('核对当前条件后尝试交接。', model, scene_turn_id='attempt')
    after = story.engine.world
    return {'mode': 'fake', 'move_recipient': move_recipient, 'old_plan': old_plan,
            'old_plan_after': next(p for p in story.director.plans if p['plan_id'] == old_plan['plan_id']),
            'result': result, 'owner_before': before.owners['envelope_01'],
            'owner_after': after.owners['envelope_01'],
            'receiver_location': after.actor_locations['other_npc'],
            'world_unchanged_by_attempt': before == after}


if __name__ == '__main__':
    print(json.dumps(asyncio.run(run_variant()), ensure_ascii=False, indent=2))
