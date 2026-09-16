"""A05 两条独立 Fake 分支与边界证据；默认不读取密钥、不联网。"""

import asyncio
import json
import secrets
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from app.actions import ActionProposal
from app.async_runtime import run_agent_turn
from app.engine import WorldEngine
from app.execution import RunLimits
from app.memory import (build_actor_context, check_memory_consistency, input_chars, serialize,
                        summarize_actor_memory, validate_summary, visible_records)
from app.memory_runtime import MemoryStory
from app.session import create_actor_conversations
from app.turn_tools import MEMORY_TOOL_SCHEMAS
from app.world import append_statement, create_world
from scripts.a04_demo import ScriptedModel, call, response, terminal


def relay_current(text, recipient='other_npc'):
    def choose(messages):
        source = messages[0]['content'].split('本轮玩家私语候选来源：')[1].splitlines()[0]
        return terminal('whisper', recipient_id=recipient, reply=text, cause_event_id=source)
    return choose


async def branch(relay: bool, secret: str, extra: str) -> dict:
    story = MemoryStory()
    before = asdict(story.engine.world)
    captures, turns = {}, []

    async def invoke(actor, turn_id, text, responses):
        model = ScriptedModel(responses)
        result = await story.turn(actor, text, model, turn_id=turn_id,
                                  limits=RunLimits(retry_delay_s=0, max_input_chars=8000))
        captures.setdefault(actor, []).extend(deepcopy(model.requests))
        turns.append({'actor_id': actor, 'turn_id': turn_id, 'reply': result.reply,
                      'receipt': result.receipt, 'trace': result.trace})
        return result

    text = f'交接暗号是 {secret}。信封里可能有地图，尚未核实。另有只对你说的话：{extra}。'
    choice = relay_current(f'玩家说交接暗号是 {secret}，信封里可能有地图，尚未核实。') if relay else terminal(reply='我先保留这条消息。')
    await invoke('lin_yan', 'secret', text, [choice])
    after_secret = asdict(story.engine.world)
    await invoke('lin_yan', 'secret', text, [])  # 同 ID 的实际重发
    await invoke('other_npc', 'ask-beta', '你听到了什么交接线索？', [terminal(reply=(
        '林砚转述了玩家的消息，信封可能有地图，但未核实。' if relay else '我尚未收到交接线索。'))])
    await invoke('archive_keeper', 'ask-gamma', '你知道交接暗号吗？', [terminal(reply='我没有收到暗号。')])
    if relay:
        # 获得听闻后的最小行动仍走既有只读查询和裁定。
        await invoke('other_npc', 'inspect', '核对当前可见信封的外观。', [
            response(calls=[call(object_id='envelope_01')]), terminal(reply='只看到了完整封口与外面的字样，不能证实内部。')])

    world = story.engine.world
    summaries, compressed_inputs = {}, {}
    for actor in story.conversations:
        summary = summarize_actor_memory(world, actor, keep_recent=0)
        assert not validate_summary(world, actor, summary)
        summaries[actor] = summary
        # 真实调用 Fake 并捕获压缩后的最终请求；沿用同一实现与预算。
        model = ScriptedModel([terminal(reply='根据自己的来源记录回答。')])
        result = await run_agent_turn(story.conversations[actor], '回顾你自己听到的信息。', model,
            expected_actor_id=actor, engine=story.engine, turn_id='compressed-' + actor,
            memory_mode=True, memory_summary=summary, limits=RunLimits(max_input_chars=8000))
        compressed_inputs[actor] = deepcopy(model.requests)
        captures[actor].extend(deepcopy(model.requests))
        turns.append({'actor_id': actor, 'turn_id': 'compressed-' + actor,
                      'reply': result.reply, 'receipt': result.receipt, 'trace': result.trace})
    beta_wire, gamma_wire = serialize(captures['other_npc']), serialize(captures['archive_keeper'])
    assert (secret in beta_wire) == relay
    assert secret not in gamma_wire and extra not in beta_wire and extra not in gamma_wire
    assert story.engine.world.owners == before['owners']
    for requests in captures.values():
        assert all(input_chars(r['messages'], r['tools']) <= 8000 for r in requests)
    return {'branch': 'relay' if relay else 'keep', 'mode': 'fake', 'session_id': world.session_id,
            'before': before, 'after_secret': after_secret, 'after': asdict(story.engine.world),
            'turns': turns, 'model_requests_by_actor': captures, 'summaries': summaries,
            'compressed_inputs': compressed_inputs,
            'checks': {'beta_has_secret': secret in beta_wire, 'gamma_has_secret': secret in gamma_wire,
                       'extra_reached_beta_or_gamma': extra in beta_wire or extra in gamma_wire}}


async def long_context_case():
    world = create_world('budget-' + secrets.token_hex(4))
    for index in range(30):
        world, _, _ = append_statement(world, speaker_id='player', recipient_id='lin_yan',
            text=f'旧经历{index}：' + '教学旧材料' * 45, turn_id=f'old-{index}', channel='player_dialogue')
    session = create_actor_conversations(world)['lin_yan']
    for index in range(35):
        session['history'].extend([{'role': 'user', 'content': f'旧问题{index}' + '历史' * 100},
                                   {'role': 'assistant', 'content': '旧回答' * 60}])
    original = deepcopy(session)
    engine = WorldEngine(world)
    model = ScriptedModel([response(calls=[call()]), terminal(reply='观察到了灯座编号。')])
    result = await run_agent_turn(session, '查看 lamp_01 的编号。', model, expected_actor_id='lin_yan',
        engine=engine, turn_id='long', memory_mode=True, limits=RunLimits(max_input_chars=8000))
    reports = [r for r in result.trace['records'] if r['kind'] == 'context']
    counts = [input_chars(r['messages'], r['tools']) for r in model.requests]
    assert max(counts) <= 8000 and session == original
    return {'mode': 'fake', 'unit': 'unicode_characters', 'counted_fields': ['messages', 'tools'],
            'original_history_messages': len(session['history']), 'max_input_chars': 8000,
            'actual_input_chars': counts, 'reports': reports, 'actual_requests': model.requests,
            'original_event_count': len(world.events), 'after_event_count': len(engine.world.events)}


def consistency_and_fallback():
    world = create_world('consistency-' + secrets.token_hex(4))
    world, _, _ = append_statement(world, speaker_id='player', recipient_id='other_npc',
        text='信封里可能有地图，尚未核实。', turn_id='first', channel='player_dialogue')
    world, _, _ = append_statement(world, speaker_id='lin_yan', recipient_id='other_npc',
        text='我说信封里没有地图。', turn_id='second', channel='whisper')
    good = summarize_actor_memory(world, 'other_npc', keep_recent=0)
    bad = deepcopy(good)
    bad['items'][0]['evidence_type'] = 'observed'
    context = build_actor_context(world, create_actor_conversations(world)['other_npc'], 'other_npc',
                                 '地图', summary=bad)
    wire, fallback = context.pack(context.wire, MEMORY_TOOL_SCHEMAS, 8000)
    assert fallback['summary_issues'] and not fallback['summary_used']
    engine = WorldEngine(world)
    engine.submit_action(ActionProposal('inspect', 'envelope_01'), actor_id='lin_yan', turn_id='observed')
    engine.submit_action(ActionProposal('give', object_id='envelope_01', recipient_id='other_npc'),
                         actor_id='lin_yan', turn_id='given')
    missing = deepcopy(good)
    missing['source_event_ids'] = ['missing']
    issues = {actor: check_memory_consistency(engine.world, actor,
        [bad, missing] if actor == 'other_npc' else []) for actor in ACTOR_IDS}
    return {'valid_summary': good, 'invalid_summary': bad, 'fallback': fallback,
            'fallback_messages': wire, 'issues_by_actor': issues,
            'world': asdict(engine.world),
            'human_review_reference': '摘录仍含“可能”“尚未核实”，evidence_type=reported；它不证明内部有地图。'}


ACTOR_IDS = ('lin_yan', 'other_npc', 'archive_keeper')


async def receiver_variant(*, move_first=True):
    story = MemoryStory()
    secret, extra = secrets.token_hex(12), secrets.token_hex(12)
    if move_first:
        await story.turn('archive_keeper', '去值班室', ScriptedModel([
            terminal('move', destination_id='duty_room'), response('已到。')]), turn_id='move')
    before = story.engine.world
    result = await story.turn('lin_yan', secret + ';' + extra,
        ScriptedModel([relay_current(secret, 'archive_keeper')]), turn_id='relay')
    gamma = visible_records(story.engine.world, 'archive_keeper')
    beta = visible_records(story.engine.world, 'other_npc')
    assert (secret in serialize(gamma)) == move_first
    assert secret not in serialize(beta) and extra not in serialize(gamma)
    if not move_first:
        assert story.engine.world == before
    return {'mode': 'fake', 'move_first': move_first, 'receipt': result.receipt,
            'gamma_received': secret in serialize(gamma), 'beta_received': secret in serialize(beta),
            'extra_reached_gamma': extra in serialize(gamma), 'events': story.engine.world.events}


def shared_history_fault():
    world = create_world('fault-' + secrets.token_hex(4))
    sessions = create_actor_conversations(world)
    secret = secrets.token_hex(12)
    sessions['lin_yan']['history'] = [{'role': 'user', 'content': secret}, {'role': 'assistant', 'content': '保留'}]
    sessions['other_npc']['history'] = sessions['lin_yan']['history']
    def absent():
        context = build_actor_context(world, sessions['other_npc'], 'other_npc', '你好')
        messages, _ = context.pack(context.wire, [], 8000)
        assert secret not in serialize(messages), '共享 history 泄露测试暗号'
    detected = False
    try:
        absent()
    except AssertionError:
        detected = True
    sessions['other_npc']['history'] = []
    absent()
    assert detected
    return {'fault': 'test-local shared history', 'isolation_assertion_failed_as_expected': detected,
            'restored_independent_history_passed': True, 'production_code_mutated': False}


async def demo(output=Path('docs/a05_memory_cases.json')):
    secret, extra = '暗号-' + secrets.token_hex(12), '额外私语-' + secrets.token_hex(12)
    evidence = {'mode': 'fake', 'runtime': 'a05-v1', 'synthetic_secret': secret, 'synthetic_extra': extra,
                'branches': [await branch(False, secret, extra), await branch(True, secret, extra)],
                'budget': await long_context_case(), 'consistency_and_fallback': consistency_and_fallback(),
                'receiver_variants': [await receiver_variant(move_first=False), await receiver_variant()],
                'fault': shared_history_fault(),
                'real_model': 'not_run', 'learner_independent_variant': 'not_yet_verified'}
    assert evidence['branches'][0]['session_id'] != evidence['branches'][1]['session_id']
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return evidence


if __name__ == '__main__':
    evidence = asyncio.run(demo())
    for item in evidence['branches']:
        print(item['branch'], item['checks'])
    print('预算（Unicode 字符，含 messages + tools）：', evidence['budget']['actual_input_chars'], '/ 8000')
    print('同地变式：', [v['receipt']['code'] for v in evidence['receiver_variants']])
    print('受控造错：', evidence['fault'])
    print('真实模型：未运行；学习者独立变式：待验收。')
