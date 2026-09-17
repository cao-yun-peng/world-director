"""可信本机轮边界 JSON 存档。严格版本、事件重演校验；不重发模型动作。"""
import asyncio
import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from app.actions import ActionProposal, normalize_action
from app.character import ACTOR_CONFIGS
from app.director import Director, adjudicate_scene, scene_state, ending_material, project_threads
from app.engine import request_digest
from app.memory_runtime import MemoryStory
from app.lore import authorized_chunks
from app.session import validate_session, create_actor_conversations
from app.execution import RunLimits
from app.world import WorldState, create_world, adjudicate, append_statement

SCHEMA = 'a08-scene-save-v1'
APP_VERSION = 'a08-runtime-v1'
SAVE_ROOT = Path(__file__).resolve().parent.parent / 'saves/scenes'


class BatchBudget:
    def __init__(self, max_requests=180, used=0):
        if type(max_requests) is not int or type(used) is not int or not 0 <= used <= max_requests:
            raise ValueError('INVALID_BATCH_BUDGET')
        self.max_requests, self.used = max_requests, used

    @property
    def remaining(self):
        return self.max_requests - self.used


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def lore_identity(lore):
    if lore is None:
        return None
    return {'version': lore.snapshot.version, 'manifest': digest([
        [c.chunk_id, c.text_hash, sorted(c.audience)] for c in lore.snapshot.chunks]),
        'mode': lore.mode, 'space': None if lore.index is None else asdict(lore.index.space),
        'min_score': lore.min_score, 'fallback_keyword': lore.fallback_keyword,
        'fallback_reason': lore.fallback_reason,
        'reranker': None if lore.reranker is None else lore.reranker.config_id}


def slot_path(slot, directory=SAVE_ROOT):
    if (not isinstance(slot, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', slot)
            or slot.upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(10)],*[f'LPT{i}' for i in range(10)]}):
        raise ValueError('INVALID_SAVE_SLOT')
    directory = Path(directory).resolve()
    target = (directory / (slot + '.json')).resolve()
    if target.parent != directory:
        raise ValueError('INVALID_SAVE_SLOT')
    return target


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('INVALID_SAVE_INTEGER')
    return value


def _validate_world(raw, revisions=None):
    world = WorldState(**raw)
    initial = create_world(world.session_id)
    if (world.locations != initial.locations or world.objects != initial.objects
            or set(world.actor_locations) != set(ACTOR_CONFIGS)
            or set(world.knowledge) != set(ACTOR_CONFIGS)):
        raise ValueError('SAVE_REGISTRY_MISMATCH')
    _integer(world.revision)
    if not isinstance(world.events, list) or world.revision != len(world.events):
        raise ValueError('INVALID_SAVE_REVISION')
    current = initial
    if revisions is not None:
        revisions[0] = deepcopy(current)
    for event in world.events:
        if scene_state(current)['story_status'] == 'ended':
            raise ValueError('EVENT_AFTER_ENDING')
        kind = event['kind']
        if kind == 'StatementEvent':
            current, receipt, emitted = append_statement(current, speaker_id=event['speaker_id'],
                recipient_id=event['recipient_ids'][0], text=event['text'], turn_id=event['turn_id'],
                channel=event['channel'], cause_event_id=event['cause_event_id'])
        elif kind in ('MovementEvent', 'TransferEvent', 'DiscoveryEvent'):
            if kind == 'MovementEvent':
                proposal = ActionProposal('move', destination_id=event['changes']['actor_location']['after'])
            elif kind == 'TransferEvent':
                change = event['changes']['owner']
                proposal = ActionProposal('give', object_id=change['object_id'],
                                          recipient_id=change['after'].removeprefix('actor:'))
            else:
                proposal = ActionProposal('inspect', target_text=event['object_id'])
            current, receipt, emitted = adjudicate(current, proposal, actor_id=event['actor_id'],
                turn_id=event['turn_id'], cause_event_id=event['cause_event_id'])
        else:
            operation = {'SceneStartedEvent': 'start', 'PlayerChoiceEvent': 'choice',
                         'OpportunityEvent': 'opportunity', 'EndingEvent': 'ending'}[kind]
            payload = {}
            if operation == 'choice':
                payload = {'choice': event['choice']}
            elif operation == 'opportunity':
                payload = {k: event[k] for k in ('proposal_id', 'event_type', 'source_refs',
                                                  'preconditions', 'effect_parameters', 'audience')}
                payload['based_on_revision'] = current.revision
            current, receipt, emitted = adjudicate_scene(current, operation, payload,
                                                        scene_turn_id=event['turn_id'])
        if not receipt['ok'] or canonical(emitted) != canonical([event]):
            raise ValueError('INVALID_SAVE_EVENT')
        if revisions is not None:
            revisions[current.revision] = deepcopy(current)
    if canonical(asdict(current)) != canonical(raw) or not scene_state(current)['enabled']:
        raise ValueError('SAVE_STATE_EVENT_MISMATCH')
    return world


def snapshot_story(story, *, model_config=None, cli=None):
    if story._lock.locked() or story.memory._lock.locked() or story.engine.turn_lock.locked():
        raise ValueError('SAVE_NOT_AT_TURN_BOUNDARY')
    bundle = story.engine._bundle
    turns = []
    for key, value in bundle['turns'].items():
        record = deepcopy(value)
        record['trace'] = None  # 派生诊断不恢复；不保存全量调试 prompt。
        turns.append({'key': list(key), 'record': record})
    return {'schema': SCHEMA, 'app_version': APP_VERSION, 'world': asdict(story.engine.world),
            'turns': turns,
            'scene_receipts': [{'key': list(k), 'record': deepcopy(v)}
                               for k, v in bundle['scene_receipts'].items()],
            'conversations': story.memory.conversations, 'records': story.records,
            'plans': story.director.plans, 'lore': lore_identity(story.lore),
            'model_config': model_config,
            'budget': {'max_story_requests': story.max_story_requests, 'model_requests': story.model_requests,
                       'chat_requests': story.chat_requests, 'embedding_requests': story.embedding_requests,
                       'rerank_requests': story.rerank_requests, 'max_story_turns': story.max_story_turns,
                       'turn_count': story.turn_count, 'turn_limits': asdict(story.turn_limits),
                       'batch': None if story.batch is None else
                                {'max_requests': story.batch.max_requests, 'used': story.batch.used}},
            'cli': deepcopy(cli)}


def restore_story(payload, *, lore=None, model_config=None):
    """所有检查在新对象上完成；调用者仅在返回成功后替换当前故事。"""
    from app.scene_runtime import SceneStory
    try:
        expected = {'schema', 'app_version', 'world', 'turns', 'scene_receipts', 'conversations',
                    'records', 'plans', 'lore', 'model_config', 'budget', 'cli'}
        if set(payload) != expected or payload['schema'] != SCHEMA or payload['app_version'] != APP_VERSION:
            raise ValueError('UNSUPPORTED_SCENE_SAVE')
        if payload['lore'] != lore_identity(lore) or payload['model_config'] != model_config:
            raise ValueError('SAVE_CONFIGURATION_MISMATCH')
        revisions = {}
        world = _validate_world(payload['world'], revisions)
        conversations = payload['conversations']
        if set(conversations) != set(ACTOR_CONFIGS):
            raise ValueError('SAVE_ACTOR_MISMATCH')
        for actor, session in conversations.items():
            validate_session(session, expected_actor_id=actor)
            if session['session_id'] != world.session_id or session['goal_id'] != ACTOR_CONFIGS[actor]['goal_id']:
                raise ValueError('SAVE_SESSION_MISMATCH')
        event_ids = {e['event_id'] for e in world.events}
        restored = {}
        for name, length in (('turns', 3), ('scene_receipts', 2)):
            items = {}
            for row in payload[name]:
                key, record = tuple(row['key']), row['record']
                if (len(key) != length or not all(isinstance(k, str) and k for k in key)
                        or key[0] != world.session_id or key in items):
                    raise ValueError('INVALID_SAVE_RECEIPT_KEY')
                if length == 3:
                    if key[1] not in ACTOR_CONFIGS:
                        raise ValueError('SAVE_ACTOR_MISMATCH')
                    normalize_action(ActionProposal(**record['proposal']))
                    if record['session'] is not None:
                        validate_session(record['session'], expected_actor_id=key[1])
                        saved = record['session']
                        current = conversations[key[1]]
                        if (saved['session_id'] != world.session_id
                                or current['history'][:len(saved['history'])] != saved['history']):
                            raise ValueError('SAVE_HISTORY_MISMATCH')
                if (not isinstance(record['request_digest'], str)
                        or not re.fullmatch('[0-9a-f]{64}', record['request_digest'])
                        or not 0 <= _integer(record['before_revision']) <=
                            _integer(record['after_revision']) <= world.revision
                        or type(record['receipt']['ok']) is not bool
                        or not isinstance(record['event_ids'], list)
                        or not set(record['event_ids']) <= event_ids):
                    raise ValueError('INVALID_SAVE_RECEIPT')
                segment = world.events[record['before_revision']:record['after_revision']]
                if [e['event_id'] for e in segment] != record['event_ids']:
                    raise ValueError('SAVE_RECEIPT_EVENT_MISMATCH')
                items[key] = deepcopy(record)
            restored[name] = items
        blank = create_actor_conversations(world)
        for actor, session in conversations.items():
            histories = [r['session'] for k, r in restored['turns'].items()
                         if k[1] == actor and r['session'] is not None]
            latest = max(histories, key=lambda s: len(s['history'])) if histories else blank[actor]
            if session != latest:
                raise ValueError('SAVE_ORPHAN_HISTORY')
        records = payload['records']
        totals = {k: 0 for k in ('model_requests', 'chat_requests', 'embedding_requests', 'rerank_requests')}
        counted_turns = 0
        for turn_id, record in records.items():
            request, result = record['request'], record['result']
            if (request_digest('scene_turn', request) != record['request_digest']
                    or result['scene_turn_id'] != turn_id or result['replayed']
                    or request['focus_actor'] not in ACTOR_CONFIGS or request['private_to'] not in ACTOR_CONFIGS
                    or not 0 <= _integer(result['before_revision']) <=
                           _integer(result['after_revision']) <= world.revision):
                raise ValueError('INVALID_SCENE_RECEIPT')
            historical = revisions[result['after_revision']]
            expected_state = {**ending_material(historical), 'threads': project_threads(historical, 'player')}
            if result['state'] != expected_state:
                raise ValueError('SAVE_SCENE_STATE_MISMATCH')
            for response in result['responses']:
                actor = response['actor_id']
                if actor not in ACTOR_CONFIGS or not isinstance(response['reply'], str):
                    raise ValueError('INVALID_SAVE_RESPONSE')
                refs = response.get('lore_refs', [])
                allowed = {} if lore is None else {c.chunk_id: c.reference()
                    for c in authorized_chunks(lore.snapshot, actor_id=actor, recipient_id='player',
                                               scenario_id='handover-v1')}
                if (not isinstance(refs, list) or len(refs) > 3
                        or any(not isinstance(ref, dict) or ref.get('chunk_id') not in allowed
                               or ref != allowed[ref['chunk_id']] for ref in refs)
                        or len({ref['chunk_id'] for ref in refs}) != len(refs)):
                    raise ValueError('SAVE_LORE_REFERENCE_UNAVAILABLE')
            if type(result['counted_turn']) is not bool:
                raise ValueError('INVALID_SAVE_TURN_COUNT')
            counted_turns += result['counted_turn']
            for field in totals:
                totals[field] += _integer(result[field])
            if result['model_requests'] != sum(result[k] for k in ('chat_requests','embedding_requests','rerank_requests')):
                raise ValueError('SAVE_REQUEST_TOTAL_MISMATCH')
        counts = payload['budget']
        if any(_integer(counts[k]) != value for k, value in totals.items()):
            raise ValueError('SAVE_REQUEST_TOTAL_MISMATCH')
        if counts['model_requests'] > _integer(counts['max_story_requests']):
            raise ValueError('SAVE_BUDGET_EXCEEDED')
        turn_count = _integer(counts['turn_count'])
        if turn_count != counted_turns or (counts['max_story_turns'] is not None
                                        and turn_count > _integer(counts['max_story_turns'], 1)):
            raise ValueError('SAVE_TURN_COUNT_MISMATCH')
        batch = None if counts['batch'] is None else BatchBudget(**counts['batch'])
        if batch is not None and batch.used < counts['model_requests']:
            raise ValueError('SAVE_BATCH_MISMATCH')
        plans = payload['plans']
        if not isinstance(plans, list) or not plans:
            raise ValueError('INVALID_SAVE_PLANS')
        refs = event_ids | {f'{world.session_id}:config:handover-v1'}
        for i, plan in enumerate(plans, 1):
            if (plan['plan_id'] != f'plan-{i}' or not 0 <= _integer(plan['based_on_revision']) <= world.revision
                    or plan['status'] not in ('active', 'completed', 'invalidated')
                    or not all(isinstance(r, str) and r in refs for r in plan['source_refs'])):
                raise ValueError('INVALID_SAVE_PLANS')
        # 构造不调用 SceneStory.__init__，避免重复 start 或重建导演副作用。
        story = SceneStory.__new__(SceneStory)
        story.memory = MemoryStory(world)
        story.engine = story.memory.engine
        story.engine._bundle.update(restored)
        story.memory._conversations = deepcopy(conversations)
        story.director = Director()
        story.director._plans = deepcopy(plans)
        story._lock, story._records, story._audits = asyncio.Lock(), deepcopy(records), {}
        story._lore, story.batch = lore, batch
        story.turn_limits = RunLimits(**counts['turn_limits'])
        for field in ('max_story_requests', 'model_requests', 'chat_requests', 'embedding_requests',
                      'rerank_requests', 'max_story_turns', 'turn_count'):
            setattr(story, field, counts[field])
        return story
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError('INVALID_SCENE_SAVE') from error


def save_scene(story, slot, *, directory=SAVE_ROOT, model_config=None, cli=None):
    target = slot_path(slot, directory)
    payload = snapshot_story(story, model_config=model_config, cli=cli)
    restore_story(payload, lore=story.lore, model_config=model_config)
    wire = canonical({'payload': payload, 'sha256': digest(payload)})
    if len(wire.encode('utf-8')) > 8_000_000:
        raise ValueError('SAVE_FILE_TOO_LARGE')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         prefix=slot + '-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(wire)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return target


def read_scene(slot, *, directory=SAVE_ROOT):
    path = slot_path(slot, directory)
    if path.stat().st_size > 8_000_000:
        raise ValueError('SAVE_FILE_TOO_LARGE')
    try:
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('DUPLICATE_SAVE_KEY')
                result[key] = value
            return result
        envelope = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_pairs)
        if (set(envelope) != {'payload', 'sha256'} or digest(envelope['payload']) != envelope['sha256']):
            raise ValueError('SAVE_CHECKSUM_MISMATCH')
        return envelope['payload']
    except (UnicodeError, TypeError) as error:
        raise ValueError('INVALID_SCENE_SAVE') from error


def load_scene(slot, *, directory=SAVE_ROOT, lore=None, model_config=None):
    return restore_story(read_scene(slot, directory=directory), lore=lore, model_config=model_config)
