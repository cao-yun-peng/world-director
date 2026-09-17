"""D036 参考演示：比较普通回复、明确等待与真实给物；只用 Fake。"""

import asyncio
import json

from app.execution import RunLimits
from app.memory_runtime import MemoryStory
from app.world import create_world
from scripts.a04_demo import ScriptedModel, response, terminal


async def demo():
    cases = [
        ('文字含等，但动作是回复', 'talk', [terminal(reply='别等了，现在就交接。')]),
        ('文字不含等，但动作是等待', 'wait', [terminal('wait', reply='先留在这里。')]),
        ('明确提议给物，由程序裁定', 'give', [
            terminal('give', object_id='envelope_01', recipient_id='other_npc'),
            response('信封已经交给周澈。')]),
    ]
    results = []
    for label, case_id, replies in cases:
        story = MemoryStory(create_world('a06-wait-demo-' + case_id))
        model = ScriptedModel(replies)
        result = await story.turn('lin_yan', '请根据当前情况决定下一步。', model,
                                  turn_id='T1', limits=RunLimits(retry_delay_s=0, max_input_chars=8000))
        world = story.engine.world
        record = next(iter(story.engine.turns.values()))
        results.append({
            'case': label, 'mode': 'fake', 'session_id': world.session_id,
            'proposal_kind': record['proposal']['kind'],
            'receipt_code': result.receipt['code'], 'reply': result.reply,
            'envelope_owner': world.owners['envelope_01'],
            'event_kinds': [event['kind'] for event in world.events],
            'model_requests': result.trace['model_requests'],
        })
    return results


if __name__ == '__main__':
    print(json.dumps(asyncio.run(demo()), ensure_ascii=False, indent=2))
