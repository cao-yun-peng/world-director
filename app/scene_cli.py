"""A06 显式场景入口；模型配置复用现有适配器，世界仍只在内存中。"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from app.character import ACTOR_CONFIGS
from app.engine import TurnConflict
from app.execution import RunLimits
from app.loop_cli import load_model
from app.runtime import AgentTurnError
from app.scene_runtime import SceneStory

ROOT = Path(__file__).resolve().parent.parent


async def chat(model, *, limits, max_model_requests, max_responders=1, narrate_ending=False,
               trace_path=None, story=None):
    story = story or SceneStory(max_story_requests=max_model_requests)
    remaining, focus, last = max_model_requests, 'lin_yan', None
    trace_path = trace_path or ROOT / 'runs' / 'a06.jsonl'
    print(f'A06 场景交接 [{model.mode}]；世界与历史只在内存中。')
    print('/offer 推进；/pause 暂缓；/focus 角色；/responders 1|2；/status；/retry；/new；/exit。')
    print('普通文字只对焦点角色说；明确选择向交接参与者公开。切换焦点不表示玩家移动。')
    try:
        while True:
            try:
                text = input(f"你 → {ACTOR_CONFIGS[focus]['card']['name']}：").strip()
            except EOFError:
                return 0
            if text == '/exit':
                return 0
            if not text:
                continue
            command, _, argument = text.partition(' ')
            if command == '/status':
                print(json.dumps(story.view(), ensure_ascii=False))
                continue
            if command == '/new':
                story, last = SceneStory(max_story_requests=remaining), None
                print('已显式新建故事；本进程剩余请求额度不重置。')
                continue
            if command == '/focus':
                if argument in ACTOR_CONFIGS:
                    focus = argument
                else:
                    print('角色可选 lin_yan、other_npc、archive_keeper。')
                continue
            if command == '/responders':
                if argument in ('1', '2'):
                    max_responders = int(argument)
                else:
                    print('响应人数可选 1 或 2。')
                continue
            if command == '/retry':
                if last is None:
                    print('暂无可重发场景轮。')
                    continue
                request = dict(last)
            elif command in ('/offer', '/pause'):
                choice = 'offer_handover' if command == '/offer' else 'pause_handover'
                request = {'text': argument or ('请核对并推进交接。' if command == '/offer' else '本次暂缓交接。'),
                           'choice': choice, 'focus_actor': focus, 'max_responders': max_responders,
                           'narrate_ending': narrate_ending, 'scene_turn_id': str(uuid4())}
                last = dict(request)
            elif command.startswith('/'):
                print('未知命令；本课没有世界存档。')
                continue
            else:
                request = {'text': text, 'focus_actor': focus, 'max_responders': max_responders,
                           'narrate_ending': narrate_ending, 'scene_turn_id': str(uuid4())}
                last = dict(request)
            try:
                result = await story.turn(model=model, limits=limits, trace_path=trace_path, **request)
            except (TurnConflict, ValueError) as error:
                print(str(error))
                continue
            except AgentTurnError as error:
                print(f'场景尚未开始：{error.code}。')
                continue
            remaining -= result['model_requests']
            for row in result['responses']:
                print(f"{ACTOR_CONFIGS[row['actor_id']]['card']['name']}：{row['reply']}")
            if result['ending_text']:
                print(result['ending_text'])
            if result['error_code']:
                print(f"本轮停止：{result['error_code']}；已提交事实保留。")
            print(f"{result['status']}；本轮请求 {result['model_requests']}；剩余 {remaining}。")
    finally:
        await model.aclose()


async def configured_chat(args, limits):
    return await chat(load_model(), limits=limits, max_model_requests=args.max_model_requests,
                      max_responders=args.max_responders, narrate_ending=args.narrate_ending)


def main(args):
    try:
        if args.load:
            raise ValueError('A06 没有世界存档，不能加载对话 JSON。')
        if args.max_model_requests < 1:
            raise ValueError('模型请求预算必须大于 0。')
        limits = RunLimits(max_steps=args.max_steps, turn_timeout_s=args.turn_timeout,
                           max_parallel_tools=args.max_parallel_tools, max_input_chars=args.max_input_chars)
        return asyncio.run(configured_chat(args, limits))
    except KeyboardInterrupt:
        return 130
    except ValueError as error:
        print(str(error))
        return 1
