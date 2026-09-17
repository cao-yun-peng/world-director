"""A06 场景工程反例；Fake 验证控制流，不代替真实模型策略评价。"""

import asyncio
import json
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock

from app.actions import ActionProposal
from app.director import (SCENE_AUDIENCE, director_view, eligible_ending, project_threads,
                          propose_opportunity, select_responders)
from app.engine import TurnConflict
from app.execution import RunLimits, TransientFailure
from app.memory import input_chars
from app.runtime import AgentTurnError
from app.scene_runtime import SceneStory
from app.scene_tools import parse_scene_terminal
from app.world import append_statement, create_world
from scripts.a04_demo import ScriptedModel, call, response
from scripts.a06_demo import branch, decision, replan_case


class DirectorTests(unittest.TestCase):
    def setUp(self):
        self.story = SceneStory(create_world('a06-rules'))
        self.engine = self.story.engine

    def submit(self, operation, payload, key='op', scene_turn_id='round'):
        return self.engine.submit_scene(operation, payload, request_id=key, scene_turn_id=scene_turn_id)

    def offer(self):
        self.submit('choice', {'choice': 'offer_handover'}, 'choice')
        return propose_opportunity(director_view(self.engine.world), proposal_id='proposal')

    def test_opportunity_does_not_transfer_and_changed_id_cannot_duplicate(self):
        proposal = self.offer()
        first = self.submit('opportunity', proposal)
        before = self.engine.world
        again = {**proposal, 'proposal_id': 'another', 'based_on_revision': before.revision}
        duplicate = self.submit('opportunity', again, 'another')
        self.assertEqual(duplicate['receipt']['code'], 'OPPORTUNITY_EXISTS')
        self.assertEqual(duplicate['event_ids'], [])
        self.assertEqual(self.engine.world, before)
        self.assertEqual(before.owners['envelope_01'], 'actor:lin_yan')
        self.assertEqual(first['after_revision'], first['before_revision'] + 1)

    def test_illegal_opportunities_leave_world_unchanged(self):
        proposal = self.offer()
        mutations = [
            {'event_type': 'steal_envelope'}, {'event_type': []},
            {'source_refs': ['another-story:E0001']}, {'source_refs': ['missing']},
            {'based_on_revision': 0}, {'based_on_revision': True},
            {'effect_parameters': {'owners': {'envelope_01': 'actor:other_npc'}}},
            {'effect_parameters': {'delete_events': True}},
            {'audience': list(SCENE_AUDIENCE) + ['archive_keeper']},
            {'owners': {}}, {'source_refs': []},
        ]
        before = self.engine.world
        for index, fields in enumerate(mutations):
            with self.subTest(fields=fields):
                rejected = self.submit('opportunity', {**proposal, **fields}, f'bad-{index}')
                self.assertFalse(rejected['receipt']['ok'])
                self.assertEqual(self.engine.world, before)

    def test_preconditions_are_recomputed_and_same_round_has_one_opportunity(self):
        proposal = self.offer()
        self.engine.submit_action(ActionProposal('move', destination_id='storage_room'),
                                  actor_id='other_npc', turn_id='move')
        proposal.update(based_on_revision=self.engine.world.revision, preconditions={'together': True})
        before = self.engine.world
        self.assertEqual(self.submit('opportunity', proposal)['receipt']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(self.engine.world, before)
        self.engine.submit_action(ActionProposal('move', destination_id='duty_room'),
                                  actor_id='other_npc', turn_id='return')
        proposal['based_on_revision'] = self.engine.world.revision
        self.assertTrue(self.submit('opportunity', proposal, 'valid')['receipt']['ok'])
        self.submit('choice', {'choice': 'pause_handover'}, 'pause')
        second = propose_opportunity(director_view(self.engine.world), proposal_id='pause')
        self.assertEqual(self.submit('opportunity', second, 'second')['receipt']['code'], 'OPPORTUNITY_LIMIT')

    def test_scene_subrequest_replay_and_conflict(self):
        first = self.submit('choice', {'choice': 'offer_handover'})
        before = self.engine.world
        self.assertEqual(self.submit('choice', {'choice': 'offer_handover'}), first)
        self.assertEqual(self.engine.world, before)
        with self.assertRaises(TurnConflict):
            self.submit('choice', {'choice': 'pause_handover'})

    def test_threads_and_endings_need_real_transfer_and_current_owner(self):
        self.offer()
        self.engine.submit_action(ActionProposal('talk', reply='交接已经解决。'), actor_id='lin_yan', turn_id='claim')
        self.assertEqual(project_threads(self.engine.world, 'player')[0]['status'], 'open')
        self.assertIsNone(eligible_ending(self.engine.world))
        self.assertEqual(self.submit('ending', {}, 'too-early')['receipt']['code'], 'ENDING_NOT_READY')
        self.engine.submit_action(ActionProposal('give', object_id='envelope_01', recipient_id='other_npc'),
                                  actor_id='lin_yan', turn_id='give')
        thread = project_threads(self.engine.world, 'player')[0]
        self.assertEqual(thread['status'], 'resolved')
        self.assertTrue(thread['resolved_by_event_ids'])
        self.assertEqual(eligible_ending(self.engine.world), 'handed_over')
        self.engine.submit_action(ActionProposal('give', object_id='envelope_01', recipient_id='lin_yan'),
                                  actor_id='other_npc', turn_id='give-back')
        self.assertIsNone(eligible_ending(self.engine.world))

    def test_thread_metadata_and_reason_sources_are_authorized(self):
        self.assertEqual(project_threads(self.engine.world, 'archive_keeper'), [])
        self.assertEqual(project_threads(self.engine.world, 'unknown'), [])
        self.assertEqual(self.story.view('archive_keeper'), {'story_status': 'unavailable', 'threads': []})
        for source in ('secret-event', 'another-story:E0001'):
            tool = call('wait', arguments=json.dumps({'reply': '等待', 'reason': '基于隐藏内容', 'source_refs': [source]}))
            with self.assertRaises(ValueError):
                parse_scene_terminal(tool, set())

    def test_pause_cannot_be_overridden_by_a_give_proposal(self):
        self.submit('choice', {'choice': 'pause_handover'})
        before = self.engine.world
        result = self.engine.submit_action(ActionProposal('give', object_id='envelope_01', recipient_id='other_npc'),
                                           actor_id='lin_yan', turn_id='force-give')
        self.assertEqual(result['receipt']['code'], 'PLAYER_CHOICE_REQUIRED')
        self.assertEqual(self.engine.world, before)

    def test_invalid_choice_and_ending_patch_are_rejected(self):
        before = self.engine.world
        for index, payload in enumerate(({'choice': 'already_done'}, {'choice': []}, {'choice': 'offer_handover', 'owner': 'x'})):
            self.assertFalse(self.submit('choice', payload, f'bad{index}')['receipt']['ok'])
        self.assertFalse(self.submit('ending', {'ending': 'handed_over'}, 'fake')['receipt']['ok'])
        self.assertEqual(self.engine.world, before)

    def test_selection_filters_candidates_before_ranking(self):
        view = director_view(self.engine.world)
        selected, skipped = select_responders(view, focus_actor='lin_yan',
            candidate_ids=['unknown', 'archive_keeper', 'other_npc', 'lin_yan', 'lin_yan'],
            stimulated={'lin_yan', 'other_npc', 'archive_keeper'}, max_responders=1)
        self.assertEqual(selected, ['lin_yan'])
        self.assertEqual({row['reason'] for row in skipped},
                         {'UNKNOWN_ACTOR', 'NOT_COLOCATED', 'DUPLICATE_ACTOR', 'RESPONDER_LIMIT'})
        self.assertEqual(select_responders(view, focus_actor='lin_yan', candidate_ids=list(SCENE_AUDIENCE),
                                           stimulated=set())[0], [])
        with self.assertRaises(ValueError):
            select_responders(view, focus_actor='lin_yan', candidate_ids=[], stimulated=set(), max_responders=3)


class SceneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.story = SceneStory(create_world('a06-scene-tests'))
        self.limits = RunLimits(retry_delay_s=0, max_input_chars=8000)

    async def turn(self, replies, *, text='请处理交接。', scene_turn_id='T1', **options):
        model = ScriptedModel(replies)
        result = await self.story.turn(text, model, scene_turn_id=scene_turn_id,
                                       limits=options.pop('limits', self.limits), **options)
        return result, model

    def wait(self):
        return decision(self.story, reply='我先核对，暂时保留信封。')

    def give(self):
        return decision(self.story, 'give', object_id='envelope_01', recipient_id='other_npc')

    async def test_two_branches_same_initial_business_state_different_endings(self):
        offered, a, evidence_a = await branch('offer_handover')
        paused, b, evidence_b = await branch('pause_handover')
        self.assertEqual(evidence_a['initial'], evidence_b['initial'])
        self.assertNotEqual(offered.engine.world.session_id, paused.engine.world.session_id)
        self.assertEqual(a['state']['ending'], 'handed_over')
        self.assertEqual(b['state']['ending'], 'deferred')
        self.assertEqual(a['state']['thread_status'], 'resolved')
        self.assertEqual(b['state']['thread_status'], 'open')
        self.assertFalse(any(e['kind'] == 'TransferEvent' for e in paused.engine.world.events))

    async def test_player_change_invalidates_previous_plan(self):
        evidence = await replan_case()
        previous = evidence['plans_before_change'][-1]['plan_id']
        old = next(p for p in evidence['plans_after_change'] if p['plan_id'] == previous)
        self.assertEqual(old['status'], 'invalidated')
        self.assertIsNotNone(old['superseded_by'])
        self.assertEqual(evidence['second']['replans'], 1)
        self.assertTrue(all(len(p['steps']) <= 2 for p in evidence['plans_after_change']))

    async def test_movement_invalidates_plan_without_teleport_or_forced_transfer(self):
        await self.turn([self.wait()], choice='offer_handover')
        previous = self.story.director.plans[-1]['plan_id']
        self.story.engine.submit_action(ActionProposal('move', destination_id='storage_room'),
                                        actor_id='other_npc', turn_id='move')
        before = self.story.engine.world
        result, _ = await self.turn([self.give()], scene_turn_id='T2')
        self.assertEqual(result['responses'][0]['code'], 'NOT_COLOCATED')
        self.assertEqual(self.story.engine.world, before)
        old = next(p for p in self.story.director.plans if p['plan_id'] == previous)
        self.assertEqual(old['status'], 'invalidated')
        result, model = await self.turn([self.wait()], scene_turn_id='T3')
        self.assertIn('NOT_COLOCATED', json.dumps(model.requests, ensure_ascii=False))
        self.assertIsNone(result['state']['ending'])

    async def test_owner_change_rechecks_plan_and_cannot_report_false_success(self):
        await self.turn([self.wait()], choice='offer_handover')
        old = self.story.director.plans[-1]['plan_id']
        self.story.engine.submit_action(ActionProposal('give', object_id='envelope_01', recipient_id='other_npc'),
                                        actor_id='lin_yan', turn_id='outside-give')
        self.story.engine.submit_action(ActionProposal('move', destination_id='storage_room'),
                                        actor_id='other_npc', turn_id='move')
        self.story.engine.submit_action(ActionProposal('give', object_id='envelope_01', recipient_id='archive_keeper'),
                                        actor_id='other_npc', turn_id='hidden-give')
        result, _ = await self.turn([self.wait()], scene_turn_id='T2')
        self.assertIsNone(result['state']['ending'])
        self.assertEqual(next(p for p in self.story.director.plans if p['plan_id'] == old)['status'], 'invalidated')

    async def test_second_actor_reads_new_state_without_first_actors_private_input(self):
        secret = '仅给林砚的未公开附言-92719'
        def second(messages):
            wire = json.dumps(messages, ensure_ascii=False)
            self.assertNotIn(secret, wire)
            self.assertIn('可信 actor_id=other_npc', wire)
            self.assertIn('"inventory":["envelope_01"]', messages[0]['content'])
            return self.wait()
        result, model = await self.turn([self.give(), response('已经交接。'), second],
                                        text=secret, choice='offer_handover', max_responders=2)
        self.assertEqual(result['selected'], ['lin_yan', 'other_npc'])
        self.assertEqual(result['model_requests'], 3)
        for request in model.requests:
            self.assertLessEqual(input_chars(request['messages'], request['tools']), 8000)
        incoming = [e for e in self.story.engine.world.events if e['kind'] == 'StatementEvent'
                    and e['speaker_id'] == 'player']
        self.assertEqual([e['text'] for e in incoming], [secret])

    async def test_private_input_does_not_wake_other_actors_and_empty_is_allowed(self):
        result, _ = await self.turn([self.wait()], max_responders=2)
        self.assertEqual(result['selected'], ['lin_yan'])
        empty, model = await self.turn([], text='', scene_turn_id='empty', candidate_ids=[])
        self.assertEqual(empty['selected'], [])
        self.assertEqual(model.requests, [])

    async def test_hidden_text_cannot_change_director_or_unauthorized_requests(self):
        results = []
        for secret in ('甲的私密内容-A', '甲的私密内容-B'):
            world, _, _ = append_statement(create_world('a06-mirror'), speaker_id='player',
                recipient_id='lin_yan', text=secret, turn_id='secret', channel='player_dialogue')
            story = SceneStory(world)
            view = director_view(story.engine.world)
            model = ScriptedModel([decision(story, reply='先核对。')])
            result = await story.turn('请核对。', model, scene_turn_id='T1', choice='offer_handover',
                                      focus_actor='other_npc', candidate_ids=['other_npc'], limits=self.limits)
            self.assertNotIn(secret, json.dumps(model.requests, ensure_ascii=False))
            results.append((view, result['selected'], model.requests))
        self.assertEqual(results[0], results[1])

    async def test_outsider_prompt_has_no_thread_metadata(self):
        source = []  # 沈岚没有本章主线的已提交来源，不伪造引用。
        model = ScriptedModel([response(calls=[call('wait', arguments=json.dumps({
            'reply': '目前没有收到新线索。', 'reason': '没有已核实的信息。', 'source_refs': source}))])])
        result = await self.story.turn('先留在这里。', model, scene_turn_id='outsider',
                                       focus_actor='archive_keeper', limits=self.limits)
        self.assertEqual(result['responses'][0]['code'], 'WAITED')
        wire = json.dumps(model.requests, ensure_ascii=False)
        for hidden in ('信封交接', 'envelope_handover', 'handover-v1', 'a06-scene-tests:E0001'):
            self.assertNotIn(hidden, wire)

    async def test_scene_replay_conflict_and_end_guard_do_not_call_models(self):
        first, _ = await self.turn([self.give(), response('交接完成。')], choice='offer_handover')
        world, plans = self.story.engine.world, self.story.director.plans
        replay, model = await self.turn([], choice='offer_handover')
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['state'], first['state'])
        self.assertEqual(model.requests, [])
        with self.assertRaises(TurnConflict):
            await self.turn([], choice='pause_handover')
        ended, model = await self.turn([], scene_turn_id='new')
        self.assertEqual(ended['error_code'], 'STORY_ENDED')
        self.assertEqual(model.requests, [])
        direct = self.story.engine.submit_action(ActionProposal('move', destination_id='storage_room'),
                                                 actor_id='lin_yan', turn_id='after-ending')
        self.assertEqual(direct['receipt']['code'], 'STORY_ENDED')
        with self.assertRaises(AgentTurnError) as caught:
            await self.story.memory.turn('lin_yan', '继续', ScriptedModel([]), turn_id='raw-memory')
        self.assertEqual(caught.exception.code, 'STORY_ENDED')
        from app.world_runtime import run_world_turn
        sync_model = Mock()
        with self.assertRaises(AgentTurnError) as caught:
            run_world_turn(self.story.memory.conversations['lin_yan'], '继续', sync_model,
                           expected_actor_id='lin_yan', engine=self.story.engine, turn_id='old-entry')
        self.assertEqual(caught.exception.code, 'STORY_ENDED')
        sync_model.complete.assert_not_called()
        self.assertEqual(self.story.engine.world, world)
        self.assertEqual(self.story.director.plans, plans)
        self.assertEqual(sum(e['kind'] == 'EndingEvent' for e in world.events), 1)

    async def test_partial_failure_replays_without_repeating_first_action(self):
        result, model = await self.turn([self.give(), response('交接完成。'), RuntimeError('第二角色故障')],
                                        choice='offer_handover', max_responders=2)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['error_code'], 'MODEL_ERROR')
        self.assertEqual(self.story.engine.world.owners['envelope_01'], 'actor:other_npc')
        self.assertIsNone(result['state']['ending'])
        before = self.story.engine.world
        replay, model = await self.turn([], choice='offer_handover', max_responders=2)
        self.assertEqual(model.requests, [])
        self.assertEqual(replay['status'], 'partial')
        self.assertEqual(self.story.engine.world, before)

    async def test_shared_budget_counts_retries_both_actors_and_ending(self):
        result, model = await self.turn([TransientFailure(), self.wait(), self.wait()],
            choice='offer_handover', max_responders=2, limits=replace(self.limits, max_model_requests=2))
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(result['error_code'], 'MODEL_REQUEST_LIMIT')
        self.assertEqual(len(result['responses']), 1)
        other = SceneStory(create_world('a06-no-free-ending'))
        model = ScriptedModel([decision(other, reply='本次暂缓。')])
        result = await other.turn('暂缓', model, scene_turn_id='pause', choice='pause_handover',
                                  narrate_ending=True, limits=replace(self.limits, max_model_requests=1))
        self.assertEqual(result['state']['ending'], 'deferred')
        self.assertEqual(result['ending_narration'], 'fallback')
        self.assertEqual(len(model.requests), 1)

    async def test_story_total_limit_and_necessary_input_overflow(self):
        self.story = SceneStory(create_world('a06-story-budget'), max_story_requests=1)
        await self.turn([self.wait()])
        result, model = await self.turn([], scene_turn_id='T2')
        self.assertEqual(result['error_code'], 'MODEL_REQUEST_LIMIT')
        self.assertEqual(model.requests, [])
        self.story = SceneStory(create_world('a06-input-budget'))
        before = self.story.engine.world
        result, model = await self.turn([], limits=replace(self.limits, max_input_chars=100))
        self.assertEqual(result['error_code'], 'CONTEXT_BUDGET_EXCEEDED')
        self.assertEqual(model.requests, [])
        self.assertEqual(self.story.engine.world, before)

    async def test_ending_failure_keeps_authoritative_result(self):
        result, model = await self.turn([self.wait(), RuntimeError('结尾故障')],
                                        choice='pause_handover', narrate_ending=True)
        self.assertEqual(result['state']['ending'], 'deferred')
        self.assertEqual(result['ending_narration'], 'fallback')
        self.assertIn('尚未解决', result['ending_text'])
        self.assertEqual(len(model.requests), 2)

    async def test_one_deadline_spans_both_actors(self):
        async def slow(messages):
            await asyncio.sleep(0.20)
            return self.wait()
        result, model = await self.turn([slow, slow], choice='offer_handover', max_responders=2,
            limits=replace(self.limits, turn_timeout_s=0.35, max_attempts=1))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['error_code'], 'TURN_TIMEOUT')
        self.assertEqual(len(result['responses']), 1)
        self.assertLessEqual(len(model.requests), 2)

    async def test_queue_time_is_in_deadline_and_lock_is_released(self):
        await self.story._lock.acquire()
        model = ScriptedModel([])
        try:
            with self.assertRaises(AgentTurnError) as caught:
                await self.story.turn('排队', model, scene_turn_id='queue',
                                      limits=replace(self.limits, turn_timeout_s=0.01))
            self.assertEqual(caught.exception.code, 'TURN_TIMEOUT')
            self.assertEqual(model.requests, [])
        finally:
            self.story._lock.release()
        result, _ = await self.turn([self.wait()], scene_turn_id='after-queue')
        self.assertEqual(result['status'], 'completed')

    async def test_cancel_before_commit_saves_stop_and_releases_locks(self):
        started = asyncio.Event()
        async def blocked(messages):
            started.set()
            await asyncio.Event().wait()
        before = self.story.engine.world
        model = ScriptedModel([blocked])
        task = asyncio.create_task(self.story.turn('等待', model, scene_turn_id='cancel', limits=self.limits))
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.story.engine.world, before)
        replay = await self.story.turn('等待', ScriptedModel([]), scene_turn_id='cancel', limits=self.limits)
        self.assertEqual(replay['error_code'], 'CANCELLED')
        self.assertEqual(replay['model_requests'], 0)
        result, _ = await self.turn([self.wait()], scene_turn_id='after-cancel')
        self.assertEqual(result['status'], 'completed')

    async def test_cancel_after_commit_preserves_transfer_and_replay(self):
        started = asyncio.Event()
        async def blocked(messages):
            started.set()
            await asyncio.Event().wait()
        model = ScriptedModel([self.give(), blocked])
        task = asyncio.create_task(self.story.turn('交接', model, scene_turn_id='cancel',
                                                   choice='offer_handover', limits=self.limits))
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        world = self.story.engine.world
        self.assertEqual(world.owners['envelope_01'], 'actor:other_npc')
        replay = await self.story.turn('交接', ScriptedModel([]), scene_turn_id='cancel',
                                      choice='offer_handover', limits=self.limits)
        self.assertEqual(replay['status'], 'partial')
        self.assertEqual(replay['responses'][0]['code'], 'GIVEN')
        self.assertEqual(replay['model_requests'], 0)
        self.assertEqual(self.story.engine.world, world)


if __name__ == '__main__':
    unittest.main()
