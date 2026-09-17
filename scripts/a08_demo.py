"""离线 20 轮主路径：第 10 轮退出进程，再加载重放并完成后十轮。"""
import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from app.scene_runtime import SceneStory
from app.scene_save import BatchBudget, save_scene, load_scene, snapshot_story
from app.retrieval import LoreRetriever
from app.lore import load_lore
from app.execution import RunLimits
from app.memory import visible_records
from app.world import create_world
from scripts.a04_demo import ScriptedModel, terminal, response
from scripts.a07_demo import search, answer

ROUTE = [
('lin_yan','请介绍此刻信封的归属。',False),
('lin_yan','旧灯塔以前做什么？',True),
('other_npc','值班室用于什么？',True),
('other_npc','来访者在哪里接待，交接事情在哪里记？',True),
('archive_keeper','维护材料通常放哪里？',True),
('archive_keeper','储物间此刻有多少耗材？',True),
('lin_yan','交接记录包含哪些内容？',True),
('lin_yan','可分享表格的栏目顺序是什么？',True),
('other_npc','可分享表格的栏目顺序是什么？',True),
('lin_yan','私人便笺代号是什么？',True),
('other_npc','登记意见是否等于实地观察？',True),
('other_npc','夜间访客先做什么？',True),
('lin_yan','只对你说一个新线索：窗边有一张紫色纸签。',False),
('other_npc','你听到刚才的新线索了吗？',False),
('archive_keeper','请据来源说明储物间用途。',True),
('lin_yan','只核对转移前提，先不提交选择。',False),
('other_npc','请核对当前持有者。',False),
('lin_yan','我声称已经交接，你核对一下。',False),
('lin_yan','总结还有什么要核对。',False),
('lin_yan','现在明确推进交接，请周澈也回应。',False),
]


def replies(number, retrieval):
    if number == 20:
        return [terminal('give',object_id='envelope_01',recipient_id='other_npc',
                         reason='玩家明确推进后由裁定器判断。',source_refs=[],lore_refs=[]),
                response('本次行动以程序回执为准。'),
                terminal('wait',reply='收到程序授权的交接记录。',reason='核对已发生事件。',
                         source_refs=[],lore_refs=[])]
    if retrieval:
        if number in (6,9,10):
            def bounded_answer(messages):
                # Fake 预写的边界回答，只证明工程，不评分模型真实拒答质量。
                return terminal('wait',reply='现有背景不足以确认该问题。',reason='没有完整依据。',
                                source_refs=[],lore_refs=[])
            return [search(ROUTE[number-1][1],1),bounded_answer]
        return [search(ROUTE[number-1][1],1),answer]
    return [terminal('wait',reply='先核对已有记录，不据声明改动归属。',
                     reason='继续核对。',source_refs=[],lore_refs=[])]


async def run_round(story, number):
    actor,text,retrieval=ROUTE[number-1]
    model=ScriptedModel(replies(number,retrieval))
    before=story.engine.world
    request=dict(text=text,scene_turn_id=f'T{number:02}',focus_actor=actor)
    if number==20:
        request.update(choice='offer_handover',max_responders=2)
    result=await story.turn(model=model,**request)
    if result['status']!='completed':
        raise AssertionError({'round':number,'actor':actor,'check':'completed','actual':result})
    wire=json.dumps(model.requests,ensure_ascii=False)
    if actor=='other_npc' and '窗边有一张紫色纸签' in wire:
        raise AssertionError({'round':number,'actor':actor,'check':'private_input_isolation'})
    after=story.engine.world
    if number<20:
        assert after.owners['envelope_01']=='actor:lin_yan',number
        assert not any(e['kind']=='TransferEvent' for e in after.events),number
    audit=story.audit_records[f'T{number:02}']
    deliveries=[event['lore_refs'] for a in audit['actors'] for event in a['trace'].get('records',[])
                if event['kind']=='lore_delivery']
    return {'round':number,'actor':actor,'request':request,'result':result,
            'event_ids':[e['event_id'] for e in after.events if e['after_revision']>before.revision],
            'owner':after.owners['envelope_01'],'delivered_lore':deliveries,
            'visible_memory_ids':[r['event_id'] for r in visible_records(after,actor)]}


async def phase(name,directory):
    lore=LoreRetriever(load_lore())
    evidence_path=directory/'evidence.json'
    if name=='first':
        story=SceneStory(create_world('a08-main'),lore=lore,max_story_requests=80,
                         max_story_turns=20,batch=BatchBudget(180))
        rows=[await run_round(story,n) for n in range(1,11)]
        save_scene(story,'round10',directory=directory)
        evidence={'mode':'fake','rows':rows,'saved':snapshot_story(story),'cross_process':False}
    else:
        evidence=json.loads(evidence_path.read_text(encoding='utf-8'))
        story=load_scene('round10',directory=directory,lore=lore)
        assert snapshot_story(story)==evidence['saved']
        before=story.engine.world
        model=ScriptedModel([])
        replay=await story.turn(model=model,**evidence['rows'][-1]['request'])
        assert replay['replayed'] and replay['model_requests']==0 and not model.requests
        assert before==story.engine.world
        evidence['resume_replay']=replay
        evidence['cross_process']=True
        evidence['rows'].extend([await run_round(story,n) for n in range(11,21)])
        assert story.view()['ending']=='handed_over'
        assert story.turn_count==20
        evidence['main_final']={'view':story.view(),'budget':snapshot_story(story)['budget']}
        # 独立故事走相反分支，沿用同一演示批次额度。
        other=SceneStory(create_world('a08-deferred'),lore=lore,max_story_requests=80,
                         max_story_turns=20,batch=story.batch)
        result=await other.turn('暂缓',ScriptedModel([
            terminal('wait',reply='本次暂缓。',reason='玩家选择。',source_refs=[],lore_refs=[])]),
            scene_turn_id='pause',choice='pause_handover')
        assert result['state']['ending']=='deferred'
        evidence['opposite_branch']=result
        evidence['batch_used']=story.batch.used
        evidence['saved'].pop('conversations')  # 交付报告不包含完整角色对话。
        evidence['saved'].pop('turns')
        evidence['saved'].pop('records')
        evidence['saved'].pop('world')
    evidence_path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')


def demo(output=Path('docs/a08_playthrough.json')):
    with tempfile.TemporaryDirectory(prefix='a08-resume-') as temp:
        directory=Path(temp)
        for part in ('first','second'):
            subprocess.run([sys.executable,'-X','utf8','-m','scripts.a08_demo',
                            '--phase',part,'--directory',str(directory)],check=True)
        evidence=json.loads((directory/'evidence.json').read_text(encoding='utf-8'))
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'mode':'fake','rounds':len(evidence['rows']),'cross_process':evidence['cross_process'],
            'ending':evidence['main_final']['view']['ending'],
            'other_ending':evidence['opposite_branch']['state']['ending'],
            'batch_used':evidence['batch_used'],'output':str(output)}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--phase',choices=('first','second'))
    parser.add_argument('--directory',type=Path)
    parser.add_argument('--output',type=Path,default=Path('docs/a08_playthrough.json'))
    args=parser.parse_args()
    if args.phase:
        asyncio.run(phase(args.phase,args.directory))
    else:
        print(json.dumps(demo(args.output),ensure_ascii=False,indent=2))
