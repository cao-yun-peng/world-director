"""A06 分步验收：当前仅覆盖 D036 受控等待，全部使用 Fake。"""

import json
import unittest
from dataclasses import replace

from app.actions import ActionProposal
from app.engine import TurnConflict, WorldEngine
from app.execution import RunLimits
from app.memory import input_chars, visible_records
from app.memory_runtime import MemoryStory
from app.runtime import AgentTurnError
from app.turn_tools import parse_memory_terminal
from app.world import create_world
from scripts.a04_demo import ScriptedModel, call, response, terminal


class WaitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.story = MemoryStory(create_world('a06-wait-test'))
        self.limits = RunLimits(retry_delay_s=0, max_input_chars=8000)

    async def run_turn(self, responses, *, turn_id='T1', text='请决定下一步。', limits=None):
        model = ScriptedModel(responses)
        result = await self.story.turn('lin_yan', text, model, turn_id=turn_id,
                                       limits=limits or self.limits)
        return result, model

    async def test_action_type_not_reply_keyword_controls_wait(self):
        for name, reply, code in (
            ('end_turn', '别等了，现在就交接。', 'TALK'),
            ('wait', '先留在这里。', 'WAITED'),
        ):
            with self.subTest(name=name):
                self.story = MemoryStory(create_world('a06-wait-test'))
                before = self.story.engine.world
                conversations = self.story.conversations
                result, model = await self.run_turn([terminal(name, reply=reply)])
                after = self.story.engine.world
                self.assertEqual(result.receipt['code'], code)
                self.assertEqual(result.reply, reply)
                self.assertEqual(result.trace['model_requests'], 1)
                self.assertEqual(len(model.requests), 1)
                self.assertEqual(after.owners, before.owners)
                self.assertEqual(after.actor_locations, before.actor_locations)
                self.assertEqual([item['evidence_type'] for item in after.knowledge['lin_yan']],
                                 ['reported', 'said'])
                self.assertEqual([event['kind'] for event in after.events], ['StatementEvent'] * 2)
                self.assertEqual(after.events[-1]['text'], reply)
                for actor in ('other_npc', 'archive_keeper'):
                    self.assertEqual(self.story.conversations[actor], conversations[actor])
                    self.assertEqual(visible_records(after, actor), [])
                self.assertFalse(any(row['kind'] == 'narration' for row in result.trace['records']))
                decision = next(row for row in result.trace['records']
                                if row['kind'] == 'terminal_tool' and row['status'] == 'validated')
                self.assertEqual(decision['decision_kind'], 'wait' if name == 'wait' else 'talk')
                request = model.requests[0]
                self.assertLessEqual(input_chars(request['messages'], request['tools']), 8000)
                self.assertIn('可信 actor_id=lin_yan', request['messages'][0]['content'])
                wait_schema = next(tool['function'] for tool in request['tools']
                                   if tool['function']['name'] == 'wait')
                self.assertFalse(wait_schema['parameters']['additionalProperties'])
                self.assertEqual(wait_schema['parameters']['required'], ['reply'])

    async def test_wait_replay_and_conflict_do_not_call_model(self):
        first, _ = await self.run_turn([terminal('wait', reply='先留在这里。')])
        world, conversations = self.story.engine.world, self.story.conversations
        replay, model = await self.run_turn([])
        self.assertEqual(replay.receipt, first.receipt)
        self.assertTrue(replay.trace['replayed'])
        self.assertEqual(model.requests, [])
        conflicting_model = ScriptedModel([])
        with self.assertRaises(TurnConflict):
            await self.story.turn('lin_yan', '不同的请求', conflicting_model, turn_id='T1', limits=self.limits)
        self.assertEqual(conflicting_model.requests, [])
        self.assertEqual(self.story.engine.world, world)
        self.assertEqual(self.story.conversations, conversations)

    async def test_wait_cannot_share_batch_with_query_or_give(self):
        wait_call = terminal('wait', reply='先留在这里。')['message']['tool_calls'][0]
        give_call = call('give', call_id='give', arguments=json.dumps({
            'object_id': 'envelope_01', 'recipient_id': 'other_npc'}))
        for other_call in (call(), give_call):
            with self.subTest(other=other_call['function']['name']):
                before = self.story.engine.world
                with self.assertRaises(AgentTurnError) as caught:
                    await self.run_turn([response(calls=[wait_call, other_call])])
                self.assertEqual(caught.exception.code, 'TERMINAL_TOOL_CONFLICT')
                self.assertEqual(self.story.engine.world, before)
                self.assertEqual(self.story.engine.turns, {})

    async def test_invalid_wait_uses_request_budget_without_commit(self):
        before = self.story.engine.world
        model = ScriptedModel([terminal('wait', reply='等待', actor_id='other_npc')])
        with self.assertRaises(AgentTurnError) as caught:
            await self.story.turn('lin_yan', '先停一下', model, turn_id='bad',
                                  limits=replace(self.limits, max_model_requests=1))
        self.assertEqual(caught.exception.code, 'MODEL_REQUEST_LIMIT')
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(self.story.engine.world, before)
        self.assertEqual(self.story.engine.turns, {})

    async def test_invalid_wait_can_be_corrected_with_paired_tool_feedback(self):
        result, model = await self.run_turn([
            terminal('wait', reply=' '), terminal('wait', reply='先留在这里。')])
        wire = model.requests[1]['messages']
        self.assertEqual(wire[-2]['tool_calls'][0]['id'], wire[-1]['tool_call_id'])
        self.assertEqual(json.loads(wire[-1]['content'])['error']['code'], 'INVALID_ARGUMENTS')
        self.assertEqual(result.receipt['code'], 'WAITED')
        self.assertEqual(len(model.requests), 2)

    async def test_inspect_then_wait_keeps_only_actual_discovery(self):
        result, model = await self.run_turn([
            response(calls=[call(object_id='envelope_01')]),
            terminal('wait', reply='外观已经核对，暂时保留信封。')])
        world = self.story.engine.world
        self.assertEqual(result.receipt['code'], 'WAITED')
        self.assertEqual(len(model.requests), 2)
        self.assertEqual([event['kind'] for event in world.events],
                         ['StatementEvent', 'DiscoveryEvent', 'StatementEvent'])
        self.assertEqual(world.owners['envelope_01'], 'actor:lin_yan')
        discoveries = [item for item in world.knowledge['lin_yan'] if 'object_id' in item]
        self.assertEqual(len(discoveries), 1)
        self.assertEqual(discoveries[0]['object_id'], 'envelope_01')


class WaitBoundaryTests(unittest.TestCase):
    def test_wait_rejects_unexpected_or_invalid_arguments_at_both_entries(self):
        for arguments in ('{', 'null', '[]', '{}', '{"reply":null}', '{"reply":3}',
                          '{"reply":" "}', '{"reply":"等待","actor_id":"other_npc"}',
                          '{"reply":"等待","object_id":"envelope_01"}'):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                parse_memory_terminal(call('wait', arguments=arguments))
        engine = WorldEngine(create_world('a06-direct-wait'))
        before = engine.world
        for proposal in (ActionProposal('wait', reply=' '),
                         ActionProposal('wait', reply='等待', recipient_id='other_npc'),
                         ActionProposal('wait', reply='等待', destination_id='storage_room')):
            with self.subTest(proposal=proposal), self.assertRaises(ValueError):
                engine.submit_action(proposal, actor_id='lin_yan', turn_id='bad')
        self.assertEqual(engine.world, before)
        self.assertEqual(engine.turns, {})
        record = engine.submit_action(ActionProposal('wait', reply='先留在这里。'),
                                      actor_id='lin_yan', turn_id='wait')
        self.assertEqual(record['receipt']['code'], 'WAITED')
        self.assertEqual(engine.world, before)


if __name__ == '__main__':
    unittest.main()
