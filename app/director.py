"""A06 规则导演：只投影已提交进度、安排机会，不直接修改世界。"""

from copy import deepcopy

from app.character import ACTOR_CONFIGS
from app.world import result


SCENE_VERSION = 'handover-v1'
SCENE_AUDIENCE = ('player', 'lin_yan', 'other_npc')
CHOICES = ('offer_handover', 'pause_handover')
SCENE_EVENTS = {'SceneStartedEvent', 'PlayerChoiceEvent', 'OpportunityEvent', 'EndingEvent'}
CHOICE_TEXT = {'offer_handover': '玩家愿意推进信封交接；角色仍可核对或拒绝。',
               'pause_handover': '玩家选择本次暂缓信封交接。'}


def config_ref(world):
    return f'{world.session_id}:config:{SCENE_VERSION}'


def scene_state(world):
    """可信程序的投影；不把整份 world 或后台事件交给模型。"""
    started = next((e for e in world.events if e['kind'] == 'SceneStartedEvent'), None)
    choice = next((e for e in reversed(world.events) if e['kind'] == 'PlayerChoiceEvent'), None)
    ending = next((e for e in world.events if e['kind'] == 'EndingEvent'), None)
    transfer = next((e for e in reversed(world.events) if e['kind'] == 'TransferEvent'
                     and e['session_id'] == world.session_id
                     and e['changes']['owner'] == {'object_id': 'envelope_01',
                         'before': 'actor:lin_yan', 'after': 'actor:other_npc'}), None)
    owner = world.owners.get('envelope_01')
    resolved = transfer is not None and owner == 'actor:other_npc'
    together = world.actor_locations.get('lin_yan') == world.actor_locations.get('other_npc')
    status = 'resolved' if resolved else 'open' if owner == 'actor:lin_yan' and together else 'blocked'
    return {'enabled': started is not None, 'choice': choice['choice'] if choice else None,
            'choice_ref': choice['event_id'] if choice else None,
            'story_status': 'ended' if ending else 'active',
            'ending': ending['ending'] if ending else None,
            'ending_ref': ending['event_id'] if ending else None,
            'thread_status': status, 'transfer_ref': transfer['event_id'] if resolved else None,
            'owner_status': 'sender' if owner == 'actor:lin_yan' else
                            'recipient' if owner == 'actor:other_npc' else 'unavailable'}


def project_threads(world, viewer):
    state = scene_state(world)
    if not state['enabled'] or viewer not in SCENE_AUDIENCE:
        return []
    return [{'thread_id': 'envelope_handover', 'topic': '信封交接',
             'origin_ref': config_ref(world), 'visibility_scope': list(SCENE_AUDIENCE),
             'status': state['thread_status'],
             'next_opportunity': None if state['story_status'] == 'ended' or state['thread_status'] == 'resolved'
                                 else '核对交接意愿与同地条件',
             'resolved_by_event_ids': [state['transfer_ref']] if state['transfer_ref'] else [],
             'updated_revision': world.revision}]


def director_view(world):
    state = scene_state(world)
    return {'revision': world.revision, 'scene_version': SCENE_VERSION,
            'locations': dict(world.actor_locations),
            **state, 'threads': project_threads(world, 'player')}


def scene_records(world, viewer):
    """只返回模板生成的公开文字，不展开来源或复制任意候选参数。"""
    records = []
    for event in world.events:
        if (event['session_id'] == world.session_id and event['kind'] in SCENE_EVENTS
                and viewer in event['audience']):
            records.append({'event_id': event['event_id'], 'event_revision': event['after_revision'],
                            'source_event_ids': [event['event_id']], 'evidence_type': 'observed',
                            'text': event['text']})
    return records


def ending_material(world, viewer='player'):
    if viewer not in SCENE_AUDIENCE:
        return {'story_status': 'unavailable'}
    state = scene_state(world)
    return {key: state[key] for key in ('choice', 'story_status', 'ending', 'ending_ref',
                                      'thread_status', 'owner_status', 'transfer_ref')}


def eligible_ending(world):
    state = scene_state(world)
    if not state['enabled'] or state['story_status'] == 'ended':
        return None
    if state['choice'] == 'offer_handover' and state['thread_status'] == 'resolved':
        return 'handed_over'
    if (state['choice'] == 'pause_handover' and state['owner_status'] == 'sender'
            and state['thread_status'] != 'resolved'):
        return 'deferred'
    return None


def _append(world, kind, scene_turn_id, text, **payload):
    candidate = deepcopy(world)
    candidate.revision += 1
    event = {'kind': kind, 'event_id': f'{world.session_id}:E{len(world.events) + 1:04d}',
             'session_id': world.session_id, 'turn_id': scene_turn_id,
             'before_revision': world.revision, 'after_revision': candidate.revision,
             'audience': list(SCENE_AUDIENCE), 'text': text, **payload}
    candidate.events.append(event)
    return candidate, result(True, 'SCENE_ACCEPTED', text, event_id=event['event_id']), [event]


def adjudicate_scene(world, operation, payload, *, scene_turn_id):
    """只供 WorldEngine 提交入口调用；所有影响在候选副本上生成。"""
    def reject(code):
        return deepcopy(world), result(False, code, '场景操作未接纳。'), []

    if not isinstance(payload, dict):
        return reject('INVALID_SCENE_PROPOSAL')
    state = scene_state(world)
    if operation == 'start':
        if payload or state['enabled']:
            return reject('INVALID_SCENE_START')
        return _append(world, 'SceneStartedEvent', scene_turn_id, '本章目标是核对并完成信封交接。',
                       scene_version=SCENE_VERSION, config_ref=config_ref(world))
    if not state['enabled']:
        return reject('SCENE_NOT_STARTED')
    if state['story_status'] == 'ended':
        return reject('STORY_ENDED')
    if operation == 'choice':
        if set(payload) != {'choice'} or payload['choice'] not in CHOICES:
            return reject('INVALID_CHOICE')
        return _append(world, 'PlayerChoiceEvent', scene_turn_id, CHOICE_TEXT[payload['choice']],
                       choice=payload['choice'], source_refs=[config_ref(world)])
    if operation == 'ending':
        if payload:
            return reject('INVALID_SCENE_PROPOSAL')
        ending = eligible_ending(world)
        if ending is None:
            return reject('ENDING_NOT_READY')
        refs = [state['choice_ref']] + ([state['transfer_ref']] if ending == 'handed_over' else [])
        text = ('信封已交给周澈，交接线已解决。' if ending == 'handed_over'
                else '本章暂缓交接；信封仍归林砚，交接线尚未解决。')
        return _append(world, 'EndingEvent', scene_turn_id, text, ending=ending, source_refs=refs)
    if operation != 'opportunity':
        return reject('UNKNOWN_SCENE_OPERATION')
    required = {'proposal_id', 'event_type', 'based_on_revision', 'source_refs',
                'preconditions', 'effect_parameters', 'audience'}
    if (set(payload) != required or not isinstance(payload['proposal_id'], str)
            or not 1 <= len(payload['proposal_id']) <= 100
            or type(payload['based_on_revision']) is not int
            or not isinstance(payload['preconditions'], dict) or payload['effect_parameters'] != {}
            or not isinstance(payload['source_refs'], list)
            or not all(isinstance(ref, str) for ref in payload['source_refs'])):
        return reject('INVALID_SCENE_PROPOSAL')
    templates = {'handover_opportunity': 'offer_handover', 'pause_opportunity': 'pause_handover'}
    if not isinstance(payload['event_type'], str) or payload['event_type'] not in templates:
        return reject('UNKNOWN_EVENT_TYPE')
    if payload['based_on_revision'] != world.revision:
        return reject('STALE_REVISION')
    if payload['audience'] != list(SCENE_AUDIENCE):
        return reject('INVALID_AUDIENCE')
    refs = payload['source_refs']
    if (not state['choice_ref'] or state['choice_ref'] not in refs
            or any(ref not in (state['choice_ref'], config_ref(world)) for ref in refs)
            or len(set(refs)) != len(refs)):
        return reject('SOURCE_UNAVAILABLE')
    choice = templates[payload['event_type']]
    if (state['choice'] != choice or state['owner_status'] != 'sender'
            or (choice == 'offer_handover' and world.actor_locations['lin_yan'] != world.actor_locations['other_npc'])):
        return reject('PRECONDITION_FAILED')
    business_key = f"{payload['event_type']}:{state['choice_ref']}"
    previous = next((e for e in world.events if e['kind'] == 'OpportunityEvent'
                     and e['business_key'] == business_key), None)
    if previous:
        return deepcopy(world), result(True, 'OPPORTUNITY_EXISTS', previous['text'],
                                       event_id=previous['event_id']), []
    if any(e['kind'] == 'OpportunityEvent' and e['turn_id'] == scene_turn_id for e in world.events):
        return reject('OPPORTUNITY_LIMIT')
    text = ('现在具备核对交接的机会；尚未发生给物。' if choice == 'offer_handover'
            else '可以说明暂缓原因并保留未解决的交接线。')
    return _append(world, 'OpportunityEvent', scene_turn_id, text,
                   event_type=payload['event_type'], proposal_id=payload['proposal_id'],
                   business_key=business_key, source_refs=refs,
                   preconditions={'choice': choice, 'owner_status': 'sender'}, effect_parameters={})


def propose_opportunity(view, *, proposal_id):
    if (not view['enabled'] or view['story_status'] == 'ended' or not view['choice']
            or view['owner_status'] != 'sender'):
        return None
    if view['choice'] == 'offer_handover' and view['locations']['lin_yan'] != view['locations']['other_npc']:
        return None
    return {'proposal_id': proposal_id,
            'event_type': 'handover_opportunity' if view['choice'] == 'offer_handover' else 'pause_opportunity',
            'based_on_revision': view['revision'], 'source_refs': [view['choice_ref']],
            'preconditions': {}, 'effect_parameters': {}, 'audience': list(SCENE_AUDIENCE)}


def select_responders(view, *, focus_actor, candidate_ids, stimulated, mentioned=(), max_responders=1):
    if type(max_responders) is not int or max_responders not in (1, 2):
        raise ValueError('max_responders 必须为 1 或 2。')
    if focus_actor not in ACTOR_CONFIGS:
        raise ValueError('焦点角色未登记。')
    selected, skipped, seen = [], [], set()
    location = view['locations'][focus_actor]
    for actor in candidate_ids:
        reason = None
        if actor not in ACTOR_CONFIGS:
            reason = 'UNKNOWN_ACTOR'
        elif actor in seen:
            reason = 'DUPLICATE_ACTOR'
        elif view['locations'][actor] != location:
            reason = 'NOT_COLOCATED'
        elif actor not in stimulated:
            reason = 'NO_AUTHORIZED_STIMULUS'
        elif view['story_status'] == 'ended':
            reason = 'STORY_ENDED'
        seen.add(actor)
        if reason:
            skipped.append({'actor_id': actor, 'reason': reason})
        else:
            selected.append(actor)
    order = list(ACTOR_CONFIGS)
    selected.sort(key=lambda actor: (actor != focus_actor, actor not in mentioned,
                                    actor not in SCENE_AUDIENCE, order.index(actor)))
    skipped.extend({'actor_id': actor, 'reason': 'RESPONDER_LIMIT'} for actor in selected[max_responders:])
    return selected[:max_responders], skipped


class Director:
    """近一步计划仅作安排与审计；不执行 give，也不作为完成证据。"""
    def __init__(self):
        self._plans = []

    @property
    def plans(self):
        return deepcopy(self._plans)

    def invalidate(self, view):
        if self._plans and self._plans[-1]['status'] == 'active':
            old = self._plans[-1]
            if old['based_on_revision'] != view['revision']:
                old['status'] = 'invalidated'
                old['invalidated_reason'] = 'choice_or_world_revision_changed'

    def refresh(self, view):
        self.invalidate(view)
        if self._plans and self._plans[-1]['status'] == 'active':
            return 0
        if view['story_status'] == 'ended' or view['thread_status'] == 'resolved':
            steps, status = [], 'completed'
        elif view['choice'] == 'pause_handover':
            steps, status = ['wait', 'check_deferred_conditions'], 'active'
        elif view['choice'] == 'offer_handover' and view['thread_status'] == 'open':
            steps, status = ['offer_handover', 'verify_transfer_receipt'], 'active'
        else:
            steps, status = ['clarify_or_wait'], 'active'
        plan_id = f'plan-{len(self._plans) + 1}'
        if self._plans and self._plans[-1]['status'] == 'invalidated':
            self._plans[-1]['superseded_by'] = plan_id
        self._plans.append({'plan_id': plan_id, 'based_on_revision': view['revision'],
                            'source_refs': [view['choice_ref']] if view['choice_ref'] else
                                           [view['threads'][0]['origin_ref']],
                            'steps': steps, 'status': status,
                            'invalidated_reason': None, 'superseded_by': None})
        return 1
