"""A06 显式场景入口；模型配置复用现有适配器，世界仍只在内存中。"""

import asyncio
import json
from pathlib import Path
from dataclasses import asdict, replace
from uuid import uuid4

from app.character import ACTOR_CONFIGS
from app.engine import TurnConflict
from app.execution import RunLimits, RunStopped
from app.loop_cli import load_model
from app.runtime import AgentTurnError
from app.scene_runtime import SceneStory
from app.scene_save import BatchBudget, save_scene, read_scene, restore_story, lore_identity, SAVE_ROOT

def model_identity(model, limits):
    return {'mode': model.mode, 'model': getattr(model, 'model', 'scripted'),
            'provider_host': getattr(model, 'provider_host', None),
            'max_tokens': getattr(model, 'request_defaults', {}).get('max_tokens'),
            'limits': asdict(limits)}


def saved_ui(payload, story):
    ui = payload['cli']
    if ui is None:
        return 'lin_yan', None, 1
    if (set(ui) != {'focus', 'last', 'max_responders'} or ui['focus'] not in ACTOR_CONFIGS
            or type(ui['max_responders']) is not int or ui['max_responders'] not in (1, 2)):
        raise ValueError('INVALID_SAVE_CLI')
    last = ui['last']
    if last is not None:
        if not isinstance(last, dict) or 'scene_turn_id' not in last:
            raise ValueError('INVALID_SAVE_RETRY')
        record = story.records.get(last['scene_turn_id'])
        request = {k: v for k, v in last.items() if k != 'scene_turn_id'}
        if record is None or request != record['request']:
            raise ValueError('INVALID_SAVE_RETRY')
    return ui['focus'], last, ui['max_responders']


ROOT = Path(__file__).resolve().parent.parent


async def chat(model, *, limits, max_model_requests, max_responders=1, narrate_ending=False,
               trace_path=None, story=None, save_directory=SAVE_ROOT, initial_ui=None, close_model=True):
    story = story or SceneStory(max_story_requests=max_model_requests)
    if story.batch is None:
        story.batch = BatchBudget(max_model_requests + story.model_requests, story.model_requests)
    batch = story.batch
    remaining, focus, last = batch.remaining, 'lin_yan', None
    if initial_ui is not None:
        focus, last, max_responders = initial_ui
    config = model_identity(model, limits)
    trace_path = trace_path or ROOT / 'runs' / 'a06.jsonl'
    stage = 'A07' if story.lore is not None else 'A06'
    print(f'{stage} 场景交接 [{model.mode}]；支持 A08 轮边界本机存档。')
    if story.lore is not None:
        print(f'设定检索：{story.lore.mode}；降级原因：{story.lore.fallback_reason}。')
    print('/offer 推进；/pause 暂缓；/focus 角色；/responders 1|2；/status；/retry；/save 槽位；/load 槽位；/new；/exit。')
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
            if command in ('/save', '/load'):
                try:
                    if command == '/save':
                        if last is not None:
                            record = story.records.get(last['scene_turn_id'])
                            saved_last = None if record is None else {
                                **record['request'], 'scene_turn_id': last['scene_turn_id']}
                        else:
                            saved_last = None
                        path = save_scene(story, argument, directory=save_directory, model_config=config,
                            cli={'focus': focus, 'last': saved_last, 'max_responders': max_responders})
                        print(f'已保存槽位 {argument}。')
                    else:
                        payload = read_scene(argument, directory=save_directory)
                        candidate = restore_story(payload, lore=story.lore, model_config=config)
                        ui = saved_ui(payload, candidate)
                        saved_batch = candidate.batch
                        if saved_batch is None:
                            raise ValueError('SAVE_BATCH_MISSING')
                        merged = BatchBudget(min(batch.max_requests, saved_batch.max_requests),
                                             max(batch.used, saved_batch.used))
                        candidate.batch = merged
                        story, batch = candidate, merged
                        focus, last, max_responders = ui
                        remaining = batch.remaining
                        print(f'已加载槽位 {argument}；剩余 {remaining}；当前进程用量不会回退。')
                except (ValueError, OSError) as error:
                    print(f'存档操作失败：{error}。')
                continue
            if command == '/new':
                story, last = SceneStory(max_story_requests=min(story.max_story_requests, batch.remaining),
                    max_story_turns=story.max_story_turns, lore=story.lore, batch=batch), None
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
                print('未知命令；使用 /save 槽位 或 /load 槽位。')
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
            remaining = batch.remaining
            for row in result['responses']:
                print(f"{ACTOR_CONFIGS[row['actor_id']]['card']['name']}：{row['reply']}")
                if row.get('lore_refs'):
                    sources = dict.fromkeys(f"{ref['source_id']}@{ref['source_version']}" for ref in row['lore_refs'])
                    print('资料依据：' + '、'.join(sources))
            if result['ending_text']:
                print(result['ending_text'])
            if result['error_code']:
                print(f"本轮停止：{result['error_code']}；已提交事实保留。")
            print(f"{result['status']}；本轮请求 {result['model_requests']}；剩余 {remaining}。")
    finally:
        if close_model:
            await model.aclose()


async def configured_chat(args, limits):
    from app.lore_setup import prepare_lore
    from app.retrieval import build_index
    from app.execution import RunBudget
    from app.trace import RunTrace
    teaching = getattr(args, 'scene_profile', 'default') == 'a08'
    if teaching and not getattr(args, 'enable_real', False):
        raise ValueError('A08 默认不调用真实服务；离线请运行 scripts.a08_demo。互动需显式 --enable-real。')
    batch_limit = 180 if teaching else args.max_model_requests
    if teaching and getattr(args, 'request_cap_explicit', False):
        batch_limit = min(batch_limit, args.max_model_requests)
    batch = BatchBudget(batch_limit)
    payload = read_scene(args.load_slot) if getattr(args, 'load_slot', None) else None
    model = load_model()  # 只建立客户端，尚未发送请求。
    lore = cache = reranker = None
    try:
        lore, cache, _ = await prepare_lore(
            getattr(args, 'lore_mode', 'off'), build=getattr(args, 'build_lore_index', False),
            allow_upload=getattr(args, 'allow_lore_upload', False),
            fallback_keyword=getattr(args, 'lore_fallback_keyword', False),
            min_score=getattr(args, 'lore_min_score', None), max_requests=batch.remaining,
            rerank_mode=getattr(args, 'rerank_mode', 'off'), preflight=True)
        reranker = None if lore is None else lore.reranker
        config = model_identity(model, limits)
        if payload is not None:
            story = restore_story(payload, lore=lore, model_config=config)
            ui = saved_ui(payload, story)
            if story.batch is None:
                raise ValueError('SAVE_BATCH_MISSING')
            batch = BatchBudget(min(batch.max_requests, story.batch.max_requests), story.batch.used)
            story.batch = batch
        else:
            story = SceneStory(max_story_requests=min(80, batch.remaining) if teaching else batch.remaining,
                max_story_turns=20 if teaching else None, lore=lore, batch=batch)
            ui = None
        story.turn_limits = limits
        print(json.dumps({'chat': config, 'retrieval': getattr(lore, 'mode', 'off'),
            'rerank': getattr(args, 'rerank_mode', 'off'), 'lore_config': lore_identity(lore), 'story_requests': story.max_story_requests,
            'story_turns': story.max_story_turns, 'batch_requests': batch.max_requests,
            'batch_remaining': batch.remaining, 'note': '请求上限不是金额上限；真实费用取决于供应商。'},
            ensure_ascii=False))
        if cache is not None:
            build_budget = RunBudget(RunLimits(max_model_requests=min(8,batch.remaining),
                max_embedding_requests=8), RunTrace('a08-build','build',mode=cache.space.mode))
            try:
                index = await build_index(lore.snapshot, cache, build_budget)
                lore = replace(lore,index=index)
            except Exception:
                if not getattr(args, 'lore_fallback_keyword', False) or payload is not None:
                    raise
                from app.retrieval import LoreRetriever
                lore = LoreRetriever(lore.snapshot, fallback_reason='RETRIEVAL_UNAVAILABLE')
            finally:
                batch.used += build_budget.model_requests
            story._lore = lore
        return await chat(model, limits=limits, max_model_requests=batch.remaining,
                          max_responders=args.max_responders, narrate_ending=args.narrate_ending,
                          story=story, initial_ui=ui, close_model=False, trace_path=ROOT / 'runs' / 'a08.jsonl')
    finally:
        await model.aclose()
        if cache is not None:
            await cache.aclose()
        if reranker is not None:
            await reranker.aclose()


def main(args):
    try:
        if args.load:
            raise ValueError('场景存档请使用 --load-slot 槽位；不接受旧对话 JSON。')
        if args.max_model_requests < 1:
            raise ValueError('模型请求预算必须大于 0。')
        limits = RunLimits(max_steps=args.max_steps, turn_timeout_s=args.turn_timeout,
                           max_parallel_tools=args.max_parallel_tools, max_input_chars=args.max_input_chars)
        return asyncio.run(configured_chat(args, limits))
    except KeyboardInterrupt:
        return 130
    except (ValueError, RunStopped, OSError) as error:
        print(str(error))
        return 1
