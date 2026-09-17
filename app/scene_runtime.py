"""A06 顺序场景轮：共享预算、受众隔离、可重放的部分完成记录。"""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace

from app.async_runtime import validate_completion
from app.character import ACTOR_CONFIGS
from app.director import (CHOICES, Director, director_view, eligible_ending, ending_material,
                          project_threads, propose_opportunity, select_responders)
from app.engine import TurnConflict, request_digest
from app.execution import RunBudget, RunLimits, RunStopped, SharedBudget
from app.memory import visible_records
from app.memory_runtime import MemoryStory
from app.runtime import AgentTurnError
from app.trace import RunTrace


class SceneStory:
    def __init__(self, world=None, *, max_story_requests=24, lore=None):
        if type(max_story_requests) is not int or max_story_requests < 0:
            raise ValueError('故事总请求额度必须是非负整数。')
        self.memory = MemoryStory(world)
        self.engine = self.memory.engine
        self.engine.submit_scene('start', {}, request_id='scene:start', scene_turn_id='scene:start')
        self.director = Director()
        self.director.refresh(director_view(self.engine.world))
        self._lock = asyncio.Lock()
        self._records = {}
        self._audits = {}
        self.max_story_requests = max_story_requests
        self.model_requests = 0
        self._lore = lore

    @property
    def lore(self):
        return self._lore

    @property
    def records(self):
        return deepcopy(self._records)

    @property
    def audit_records(self):
        """开发者离线审计入口；不送给导演、其他角色或普通 CLI 输出。"""
        return deepcopy(self._audits)

    def view(self, viewer='player'):
        return {**ending_material(self.engine.world, viewer),
                'threads': project_threads(self.engine.world, viewer)}

    async def turn(self, text, model, *, scene_turn_id, choice=None, focus_actor='lin_yan',
                   private_to=None, candidate_ids=None, mentioned=(), max_responders=1,
                   limits=None, narrate_ending=False, trace_path=None):
        if not isinstance(scene_turn_id, str) or not scene_turn_id.strip():
            raise ValueError('scene_turn_id 必须非空。')
        if not isinstance(text, str) or focus_actor not in ACTOR_CONFIGS:
            raise ValueError('场景输入或焦点角色无效。')
        if choice is not None and choice not in CHOICES:
            raise ValueError('玩家选择无效。')
        if type(max_responders) is not int or max_responders not in (1, 2):
            raise ValueError('max_responders 必须为 1 或 2。')
        private_to = focus_actor if private_to is None else private_to
        if private_to not in ACTOR_CONFIGS:
            raise ValueError('私语接收者未登记。')
        candidates = list(ACTOR_CONFIGS) if candidate_ids is None else list(candidate_ids)
        if (not all(isinstance(actor, str) for actor in candidates)
                or not all(isinstance(actor, str) for actor in mentioned)):
            raise ValueError('候选与点名必须是角色 ID。')
        digest = request_digest('scene_turn', {'text': text, 'choice': choice, 'focus_actor': focus_actor,
            'private_to': private_to, 'candidate_ids': candidates, 'mentioned': list(mentioned),
            'max_responders': max_responders, 'narrate_ending': narrate_ending})
        limits = limits or RunLimits(max_input_chars=8000)
        if limits.max_input_chars is None:
            limits = replace(limits, max_input_chars=8000)
        shared = SharedBudget(limits)  # 等场景锁的时间也计入这一次 deadline。
        try:
            async with asyncio.timeout_at(shared.deadline):
                await self._lock.acquire()
        except TimeoutError:
            raise AgentTurnError('TURN_TIMEOUT', {'model_requests': 0}) from None
        try:
            previous = self._records.get(scene_turn_id)
            if previous is not None:
                if previous['request_digest'] != digest:
                    raise TurnConflict('同一 scene_turn_id 已用于不同参数。')
                return {**deepcopy(previous['result']), 'replayed': True, 'model_requests': 0,
                        'chat_requests': 0, 'embedding_requests': 0}
            before = self.engine.world
            if text.strip() and before.actor_locations[private_to] != before.actor_locations[focus_actor]:
                raise ValueError('私语接收者不在本场景。')
            output = {'scene_turn_id': scene_turn_id, 'status': 'completed', 'error_code': None,
                      'selected': [], 'responses': [], 'skipped': [], 'replayed': False,
                      'model_requests': 0, 'before_revision': before.revision, 'replans': 0,
                      'ending_text': None, 'ending_narration': 'not_requested'}
            audit = {'selection_rounds': [], 'actors': [], 'plans_before': self.director.plans}
            self._audits[scene_turn_id] = audit
            shared.max_model_requests = min(shared.max_model_requests,
                                           self.max_story_requests - self.model_requests)

            def submit(operation, payload, phase):
                return self.engine.submit_scene(operation, payload, request_id=f'{scene_turn_id}:{phase}',
                    scene_turn_id=scene_turn_id, expected_revision=self.engine.world.revision,
                    before_accept=shared.check)

            try:
                if self.engine.story_ended:
                    output.update(status='stopped', error_code='STORY_ENDED')
                    return output
                shared.check_capacity()
                if choice is not None:
                    submit('choice', {'choice': choice}, 'choice')
                self.director.invalidate(director_view(self.engine.world))
                opportunity = propose_opportunity(director_view(self.engine.world),
                                                   proposal_id=f'{scene_turn_id}:opportunity')
                if opportunity:
                    record = submit('opportunity', opportunity, 'opportunity')
                    if not record['receipt']['ok']:
                        raise RunStopped(record['receipt']['code'])

                responded = set()
                for _ in range(max_responders):
                    world = self.engine.world
                    stimuli = {}
                    for actor in ACTOR_CONFIGS:
                        if actor in responded:
                            continue
                        pieces = [text] if actor == private_to and text.strip() else []
                        fresh = [record for record in visible_records(world, actor)
                                 if record['event_revision'] > before.revision]
                        if fresh:
                            pieces.append('本轮你已收到以下授权事件，请根据自己的目标决定下一步：\n'
                                          + json.dumps([{'event_id': item['event_id'], 'text': item['text']}
                                                        for item in fresh], ensure_ascii=False))
                        if pieces:
                            previous_turns = [record for key, record in self.engine.turns.items() if key[1] == actor]
                            if previous_turns and not previous_turns[-1]['receipt']['ok']:
                                receipt = previous_turns[-1]['receipt']
                                pieces.append('你上次行动的程序回执：' + json.dumps(receipt, ensure_ascii=False))
                            stimuli[actor] = '\n'.join(pieces)
                    chosen, skipped = select_responders(director_view(world), focus_actor=focus_actor,
                        candidate_ids=[actor for actor in candidates if actor not in responded],
                        stimulated=set(stimuli), mentioned=mentioned, max_responders=max_responders - len(responded))
                    audit['selection_rounds'].append({'selected': chosen, 'skipped': skipped})
                    output['skipped'] = skipped
                    if not chosen:
                        break
                    shared.check_capacity()
                    actor = chosen[0]
                    output['selected'].append(actor)
                    actor_turn_id = f'{scene_turn_id}:actor:{actor}'
                    try:
                        actor_result = await self.memory.turn(actor, stimuli[actor], model, turn_id=actor_turn_id,
                            limits=limits, shared_budget=shared, scene_mode=True, trace_path=trace_path,
                            record_player_input=actor == private_to and bool(text.strip()), player_text=text,
                            lore=self.lore)
                    except (AgentTurnError, asyncio.CancelledError) as error:
                        audit['actors'].append({'actor_id': actor, 'trace': deepcopy(getattr(error, 'trace', {}))})
                        # 提交后的取消已由 A05 保存回执；记录事实，不重新执行该子步骤。
                        record = self.engine.turns.get((world.session_id, actor, actor_turn_id))
                        if record is not None:
                            output['responses'].append({'actor_id': actor, 'code': record['receipt']['code'],
                                                        'reply': record['reply']})
                        raise
                    audit['actors'].append({'actor_id': actor, 'trace': actor_result.trace})
                    output['responses'].append({'actor_id': actor, 'code': actor_result.receipt['code'],
                                                'reply': actor_result.reply})
                    responded.add(actor)
                    if not actor_result.receipt['ok']:
                        raise RunStopped('ACTION_REJECTED')

                if eligible_ending(self.engine.world):
                    record = submit('ending', {}, 'ending')
                    if not record['receipt']['ok']:
                        raise RunStopped(record['receipt']['code'])
                    output['ending_text'] = record['receipt']['message']
                    output['ending_narration'] = 'deterministic'
                    if narrate_ending:
                        await self._narrate(model, limits, shared, output, audit, trace_path)
            except asyncio.CancelledError:
                output.update(status='partial' if self.engine.world.revision != before.revision else 'stopped',
                              error_code='CANCELLED')
                raise
            except (AgentTurnError, RunStopped, TurnConflict) as error:
                code = 'TURN_CONFLICT' if isinstance(error, TurnConflict) else error.code
                output.update(status='partial' if self.engine.world.revision != before.revision else 'stopped',
                              error_code=code)
            except Exception:
                output.update(status='partial' if self.engine.world.revision != before.revision else 'stopped',
                              error_code='INTERNAL_ERROR')
                raise
            finally:
                output['model_requests'] = shared.model_requests
                output['chat_requests'] = shared.chat_requests
                output['embedding_requests'] = shared.embedding_requests
                self.model_requests += shared.model_requests
                output['after_revision'] = self.engine.world.revision
                output['state'] = self.view()
                if output['error_code'] != 'STORY_ENDED':
                    output['replans'] = self.director.refresh(director_view(self.engine.world))
                audit['plans_after'] = self.director.plans
                # finally 中无 await，已提交事实和停止记录在下一次取消点前保存。
                self._records[scene_turn_id] = {'request_digest': digest, 'result': deepcopy(output)}
            return deepcopy(output)
        finally:
            self._lock.release()

    async def _narrate(self, model, limits, shared, output, audit, trace_path):
        trace = RunTrace(self.engine.world.session_id, output['scene_turn_id'] + ':ending',
                         mode=model.mode, path=trace_path)
        budget = RunBudget(limits, trace, shared=shared)
        try:
            completion = await budget.call_model(model, [
                {'role': 'system', 'content': '仅依据已接纳的结局材料写一句结尾；保留未解决状态，不增添事实。'},
                {'role': 'user', 'content': json.dumps(ending_material(self.engine.world), ensure_ascii=False)},
            ], options={'tools': [], 'tool_choice': 'none'}, kind='narration', step_id=1)
            message = validate_completion(completion)
            if message.get('tool_calls') or not isinstance(message.get('content'), str) or not message['content'].strip():
                raise RunStopped('INVALID_NARRATION')
            output['ending_text'] += '\n' + message['content'].strip()
            output['ending_narration'] = 'generated'
        except (RunStopped, TimeoutError):
            output['ending_narration'] = 'fallback'
        finally:
            trace.finish(output['ending_narration'], model_requests=budget.model_requests,
                         committed_revision=self.engine.world.revision)
            audit['ending_trace'] = trace.records
