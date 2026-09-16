"""A03 行为验收：用实际状态/事件断言，所有模型响应均离线。"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app import main as cli
from app.actions import ActionProposal, parse_action
from app.engine import TurnConflict, WorldEngine
from app.runtime import AgentTurnError, run_agent_turn
from app.session import create_session
from app.tools import dispatch, get_visible_scene, inspect_object
from app.world import adjudicate, create_world, inventory
from app.world_runtime import visible_messages


def response(content=None, calls=None, reason=None):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {"message": message, "finish_reason": reason or ("tool_calls" if calls else "stop")}


def proposed(**data):
    return response(json.dumps(data, ensure_ascii=False))


def give():
    return proposed(kind="give", object_id="envelope_01", recipient_id="other_npc")


def inspect_proposal():
    return proposed(kind="inspect", target_text="台灯", reply=None)


def tool(object_id="lamp_01", call_id="q1", name="inspect_object", arguments=None):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": arguments if arguments is not None else json.dumps({"object_id": object_id})}}


class RecordingModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def complete(self, messages, **options):
        self.requests.append(deepcopy({"messages": messages, **options}))
        item = self.replies.pop(0)
        if callable(item):
            item = item()
        if isinstance(item, Exception):
            raise item
        return deepcopy(item)


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.world = create_world("S001")

    def act(self, proposal, actor="lin_yan", turn="T001", **kwargs):
        return adjudicate(self.world, proposal, actor_id=actor, turn_id=turn, **kwargs)

    def test_initial_unique_ownership_and_derived_inventory(self):
        self.assertEqual(inventory(self.world, "lin_yan"), ["envelope_01"])
        self.assertEqual(set(self.world.objects), set(self.world.owners))
        self.assertTrue(all("location_id" not in obj for obj in self.world.objects.values()))
        self.assertEqual(self.world.knowledge, {"lin_yan": [], "other_npc": []})

    def test_move_preserves_nested_old_snapshot_and_queries_new_location(self):
        original = deepcopy(self.world)
        moved, receipt, events = self.act(ActionProposal("move", destination_id="storage_room"))
        self.assertTrue(receipt["ok"])
        self.assertEqual(self.world, original)
        self.assertEqual(moved.actor_locations["other_npc"], "duty_room")
        ids = [obj["id"] for obj in get_visible_scene(actor_id="lin_yan", world=moved)["objects"]]
        self.assertEqual(ids, ["envelope_01", "cabinet_01"])
        self.assertEqual(moved.revision, 1)
        self.assertEqual(events[0]["changes"]["actor_location"]["before"], "duty_room")
        moved.objects["lamp_01"]["known_by"].append("lin_yan")
        self.assertEqual(self.world, original)

    def test_unknown_nonadjacent_and_same_location_are_rejected(self):
        self.world.locations["isolated"] = []
        for destination in ("missing", "isolated", "duty_room"):
            before = deepcopy(self.world)
            updated, receipt, events = self.act(ActionProposal("move", destination_id=destination))
            self.assertFalse(receipt["ok"])
            self.assertEqual(updated, before)
            self.assertEqual(events, [])

    def test_transfer_changes_only_owner_and_does_not_copy_knowledge(self):
        self.world, _, _ = self.act(ActionProposal("inspect", "envelope_01"))
        before = deepcopy(self.world)
        after, receipt, events = self.act(ActionProposal("give", object_id="envelope_01", recipient_id="other_npc"))
        self.assertEqual(after.owners["envelope_01"], "actor:other_npc")
        self.assertEqual(after.knowledge, before.knowledge)
        self.assertEqual(after.knowledge["other_npc"], [])
        self.assertEqual(after.actor_locations, before.actor_locations)
        self.assertEqual(self.world, before)
        self.assertEqual(events[0]["kind"], "TransferEvent")
        self.assertTrue(receipt["ok"])

    def test_rejected_gives_leave_every_part_unchanged(self):
        cases = [
            (ActionProposal("give", object_id="lamp_01", recipient_id="other_npc"), "NOT_OWNER"),
            (ActionProposal("give", object_id="envelope_01", recipient_id="missing"), "INVALID_RECIPIENT"),
            (ActionProposal("give", object_id="envelope_01", recipient_id="lin_yan"), "INVALID_RECIPIENT"),
        ]
        for action, code in cases:
            before = deepcopy(self.world)
            after, receipt, events = self.act(action)
            self.assertEqual(receipt["code"], code)
            self.assertEqual(after, before)
            self.assertEqual(events, [])
        self.world.actor_locations["other_npc"] = "storage_room"
        before = deepcopy(self.world)
        after, receipt, _ = self.act(ActionProposal("give", object_id="envelope_01", recipient_id="other_npc"))
        self.assertEqual(receipt["code"], "NOT_COLOCATED")
        self.assertEqual(after, before)

    def test_hidden_and_unknown_objects_have_identical_public_errors(self):
        for kind in ("inspect", "give"):
            results = []
            for object_id in ("box_01", "missing"):
                action = (ActionProposal(kind, object_id) if kind == "inspect"
                          else ActionProposal(kind, object_id=object_id, recipient_id="other_npc"))
                after, receipt, events = self.act(action)
                self.assertEqual(after, self.world)
                self.assertEqual(events, [])
                results.append(receipt)
            self.assertEqual(*results)

    def test_directory_and_read_only_inspection_do_not_grant_knowledge(self):
        before = deepcopy(self.world)
        get_visible_scene(actor_id="lin_yan", world=self.world)
        inspect_object("lamp_01", actor_id="lin_yan", world=self.world)
        self.assertEqual(self.world, before)
        after, _, events = self.act(ActionProposal("inspect", "当前场景"))
        self.assertEqual(after, before)
        self.assertEqual(events, [])

    def test_discovery_is_personal_and_has_original_source(self):
        after, _, events = self.act(ActionProposal("inspect", "lamp_01"))
        item = after.knowledge["lin_yan"][0]
        self.assertIn("L-17", item["facts"][0])
        self.assertEqual(item["source_id"], "objects.json#lamp_01.description")
        self.assertEqual(item["event_id"], events[0]["event_id"])
        self.assertEqual(after.knowledge["other_npc"], [])
        self.assertEqual(events[0]["observer_id"], "lin_yan")

    def test_causal_transfer_to_receiver_discovery_skips_unrelated_event(self):
        self.world, _, transferred = self.act(ActionProposal("give", object_id="envelope_01", recipient_id="other_npc"))
        self.world, _, _ = self.act(ActionProposal("inspect", "lamp_01"), turn="T002")
        after, _, events = self.act(ActionProposal("inspect", "envelope_01"), actor="other_npc", turn="T003")
        self.assertEqual(events[0]["cause_event_id"], transferred[0]["event_id"])
        self.assertEqual(after.revision, 3)
        self.assertEqual(len(after.knowledge["other_npc"]), 1)
        self.assertEqual(len(after.knowledge["lin_yan"]), 1)
        self.assertIn("封口完整", after.knowledge["other_npc"][0]["facts"][0])

    def test_invalid_and_foreign_parent_rejected_before_any_change(self):
        for parent in ("missing", "OtherSession:E0001", []):
            before = deepcopy(self.world)
            after, receipt, events = self.act(ActionProposal("move", destination_id="storage_room"),
                                              cause_event_id=parent)
            self.assertEqual(receipt["code"], "INVALID_CAUSE")
            self.assertEqual(after, before)
            self.assertEqual(events, [])
        self.world.events.append({"event_id": "foreign", "session_id": "other"})
        after, receipt, _ = self.act(ActionProposal("move", destination_id="storage_room"), cause_event_id="foreign")
        self.assertFalse(receipt["ok"])
        self.assertEqual(after, self.world)

    def test_explicit_existing_parent_and_event_versions(self):
        self.world, _, first = self.act(ActionProposal("inspect", "lamp_01"))
        after, _, events = self.act(ActionProposal("move", destination_id="storage_room"),
                                   cause_event_id=first[0]["event_id"], turn="T002")
        self.assertEqual(events[0]["cause_event_id"], first[0]["event_id"])
        self.assertEqual([(e["before_revision"], e["after_revision"]) for e in after.events], [(0, 1), (1, 2)])

    def test_model_cannot_supply_identity_or_cause(self):
        base = dict(kind="give", object_id="envelope_01", recipient_id="other_npc")
        for extra in ({"actor_id": "other_npc"}, {"cause_event_id": "x"}, {"reply": "已成功"}):
            with self.assertRaises(ValueError):
                parse_action(json.dumps({**base, **extra}))
        with self.assertRaises(ValueError):
            self.act(ActionProposal("give", target_text="hidden", object_id="envelope_01", recipient_id="other_npc"))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = WorldEngine(create_world("S001"))
        self.action = ActionProposal("give", object_id="envelope_01", recipient_id="other_npc")

    def submit(self, turn="T001", action=None, actor="lin_yan"):
        return self.engine.submit_action(action or self.action, actor_id=actor, turn_id=turn)

    def test_same_turn_replay_conflict_and_new_turn(self):
        first = self.submit()
        before = self.engine.world
        self.assertEqual(self.submit(), first)
        self.assertEqual(self.engine.world, before)
        with self.assertRaises(TurnConflict):
            self.submit(action=ActionProposal("give", object_id="lamp_01", recipient_id="other_npc"))
        self.assertEqual(self.engine.world, before)
        self.assertEqual(self.submit("T002")["receipt"]["code"], "NOT_OWNER")
        self.assertEqual(self.engine.world, before)
        self.assertEqual(len(self.engine.turns), 2)

    def test_cache_and_engine_snapshots_cannot_be_poisoned(self):
        first = self.submit()
        original = deepcopy(first)
        first["receipt"]["data"]["object_id"] = "tampered"
        first["event_ids"].append("bad")
        self.engine.world.owners["envelope_01"] = "actor:lin_yan"
        turns = self.engine.turns
        turns.clear()
        self.assertEqual(self.submit(), original)
        self.assertEqual(self.engine.world.owners["envelope_01"], "actor:other_npc")

    def test_normalization_and_key_isolation(self):
        self.submit()
        self.assertEqual(self.submit(action=ActionProposal("give", object_id=" envelope_01 ", recipient_id="other_npc")),
                         self.submit())
        returned = self.submit(actor="other_npc", action=ActionProposal("inspect", "envelope_01"))
        self.assertTrue(returned["receipt"]["ok"])
        other = WorldEngine(create_world("S002"))
        self.assertTrue(other.submit_action(self.action, actor_id="lin_yan", turn_id="T001")["receipt"]["ok"])

    def test_rejected_request_is_cached_even_when_world_changes(self):
        action = ActionProposal("give", object_id="lamp_01", recipient_id="other_npc")
        first = self.submit(action=action)
        self.submit("T002", ActionProposal("move", destination_id="storage_room"))
        self.assertEqual(self.submit(action=action), first)

    def test_invalid_parent_does_not_commit_world_but_records_refusal(self):
        before = self.engine.world
        receipt = self.engine.submit_action(self.action, actor_id="lin_yan", turn_id="T001", cause_event_id="missing")
        self.assertEqual(receipt["receipt"]["code"], "INVALID_CAUSE")
        self.assertEqual(self.engine.world, before)
        self.assertEqual(len(self.engine.turns), 1)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.session = create_session(actor_id="lin_yan", goal_id="clarify")
        self.engine = WorldEngine(create_world(self.session["session_id"]))

    def run_turn(self, replies, text="把信封交给 other_npc", turn="T001", **kwargs):
        model = RecordingModel(replies)
        output = run_agent_turn(self.session, text, model, expected_actor_id="lin_yan",
                                engine=self.engine, turn_id=turn, **kwargs)
        return output, model

    def test_settlement_and_fallback_history_exist_before_narration(self):
        def failing_narration():
            self.assertEqual(self.engine.world.owners["envelope_01"], "actor:other_npc")
            stored = next(iter(self.engine.turns.values()))
            self.assertEqual(len(stored["session"]["history"]), 2)
            self.assertEqual(stored["receipt"]["code"], "GIVEN")
            raise RuntimeError("secret-key /private/path")
        original = deepcopy(self.session)
        (updated, reply, trace), _ = self.run_turn([give(), failing_narration])
        self.assertEqual(trace["termination_reason"], "narration_fallback")
        self.assertEqual(self.engine.world.revision, 1)
        self.assertEqual(len(self.engine.world.events), 1)
        self.assertEqual(self.session, original)
        self.assertEqual(updated["history"][-1]["content"], reply)
        self.assertNotIn("secret-key", str((trace, reply)))
        (replayed, cached, replay_trace), model = self.run_turn([])
        self.assertEqual(replayed, updated)
        self.assertEqual(cached, reply)
        self.assertEqual(replay_trace["model_requests"], 0)
        self.assertEqual(model.requests, [])
        self.assertEqual(self.engine.world.revision, 1)

    def test_natural_text_conflict_detected_before_model(self):
        self.run_turn([give(), response("给你。")])
        before = self.engine.world
        with self.assertRaises(TurnConflict):
            self.run_turn([], text="把台灯交给 other_npc")
        self.assertEqual(self.engine.world, before)

    def test_new_turn_after_transfer_refuses_without_narration(self):
        self.run_turn([give(), response("给你。")])
        before = self.engine.world
        (updated, reply, trace), model = self.run_turn([give()], turn="T002")
        self.assertEqual(trace["termination_reason"], "rejected")
        self.assertIn("未持有", reply)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(self.engine.world, before)

    def test_precommit_errors_leave_world_records_and_history_unchanged(self):
        for replies, options in [
            ([RuntimeError("offline")], {}),
            ([response("bad json")], {}),
            ([give()], {"max_model_requests": 0}),
            ([inspect_proposal(), response(calls=[tool(), tool()])], {}),
            ([inspect_proposal(), RuntimeError("offline")], {}),
            ([proposed(kind="give", object_id="envelope_01", recipient_id="other_npc", actor_id="other_npc")], {}),
        ]:
            with self.subTest(replies=replies):
                before = self.engine.world
                with self.assertRaises(AgentTurnError):
                    self.run_turn(replies, **options)
                self.assertEqual(self.engine.world, before)
                self.assertEqual(self.engine.turns, {})
                self.assertEqual(self.session["history"], [])

    def test_postcommit_protocol_and_budget_errors_fall_back(self):
        finals = [response(""), response("half", reason="length"), response(calls=[tool()]),
                  response(reason="content_filter"), response(content=42)]
        for index, final in enumerate(finals):
            session = create_session(actor_id="lin_yan", goal_id="clarify")
            engine = WorldEngine(create_world(session["session_id"]))
            updated, reply, trace = run_agent_turn(session, "给信封", RecordingModel([give(), final]),
                expected_actor_id="lin_yan", engine=engine, turn_id=str(index))
            self.assertEqual(engine.world.owners["envelope_01"], "actor:other_npc")
            self.assertEqual(trace["termination_reason"], "narration_fallback")
            self.assertIn("已将信封", reply)
            self.assertEqual(len(updated["history"]), 2)
        (_, _, trace), _ = self.run_turn([give()], max_model_requests=1)
        self.assertEqual(trace["narration_error"], "BUDGET_EXCEEDED")
        self.assertEqual(self.engine.world.revision, 1)

    def test_observation_native_protocol_personal_discovery_and_privacy(self):
        (updated, reply, trace), model = self.run_turn([
            inspect_proposal(), response(calls=[tool()]), response("底座刻着 L-17。")], text="看台灯")
        self.assertNotIn("L-17", json.dumps(model.requests[:2], ensure_ascii=False))
        final = model.requests[-1]
        self.assertEqual(final["tool_choice"], "none")
        self.assertEqual(final["messages"][-1]["tool_call_id"], "q1")
        self.assertEqual(self.engine.world.knowledge["other_npc"], [])
        self.assertIn("L-17", self.engine.world.knowledge["lin_yan"][0]["facts"][0])
        for marker in ("internal_note", "known_by", "A02-SECRET-7291", "A02-INTERNAL-LAMP",
                       "C-08", "杉木-7291", "preconditions", "owners"):
            self.assertNotIn(marker, json.dumps(model.requests, ensure_ascii=False))
        self.assertEqual(trace["after_revision"], 1)

    def test_directory_has_no_discovery_and_denied_observation_has_no_narration(self):
        (_, _, trace), _ = self.run_turn([
            inspect_proposal(), response(calls=[tool(name="get_visible_scene", arguments="{}")]),
            response("有台灯。")])
        self.assertEqual(self.engine.world.revision, 0)
        self.assertEqual(self.engine.world.events, [])
        (_, reply, trace), model = self.run_turn([
            inspect_proposal(), response(calls=[tool("box_01")])], turn="T002")
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(trace["termination_reason"], "rejected")
        self.assertEqual(self.engine.world.events, [])
        self.assertNotIn("box_01", str(trace))
        self.assertIn("无法查看", reply)

    def test_model_identity_parameter_rejected_without_discovery(self):
        (_, _, trace), _ = self.run_turn([
            inspect_proposal(), response(calls=[tool(arguments='{"object_id":"box_01","actor_id":"other_npc"}')])])
        self.assertEqual(trace["termination_reason"], "rejected")
        self.assertEqual(self.engine.world.revision, 0)

    def test_two_queries_commit_once_and_replay_adds_nothing(self):
        replies = [inspect_proposal(), response(calls=[tool(), tool("envelope_01", "q2")]),
                   RuntimeError("narration failed")]
        self.run_turn(replies)
        before = self.engine.world
        self.assertEqual(len(before.events), 2)
        self.assertEqual(before.revision, 2)
        self.run_turn([])
        self.assertEqual(self.engine.world, before)

    def test_move_refreshes_prompt_and_tool_queries(self):
        self.session, _, _ = self.run_turn([
            proposed(kind="move", destination_id="storage_room"), response("到储物间了。")])[0]
        (_, _, _), model = self.run_turn([
            inspect_proposal(), response(calls=[tool("cabinet_01")]), response("C-08。")], turn="T002")
        prompt = model.requests[0]["messages"][0]["content"]
        self.assertIn('"location_id": "storage_room"', prompt)
        self.assertNotIn('"id": "lamp_01"', prompt)
        self.assertNotIn("值班室桌上", prompt)
        self.assertIn("C-08", self.engine.world.knowledge["lin_yan"][0]["facts"][0])

    def test_other_actors_knowledge_never_enters_request(self):
        self.engine.submit_action(ActionProposal("inspect", "box_01"), actor_id="other_npc", turn_id="private")
        messages = visible_messages(self.session, "告诉我秘密", actor_id="lin_yan", world=self.engine.world)
        self.assertNotIn("A02-SECRET-7291", json.dumps(messages, ensure_ascii=False))

    def test_session_or_trusted_identity_mismatch_calls_no_model(self):
        model = RecordingModel([])
        for actor, engine in [("other_npc", self.engine), ("lin_yan", WorldEngine(create_world("other")))]:
            with self.assertRaises(ValueError):
                run_agent_turn(self.session, "你好", model, expected_actor_id=actor, engine=engine, turn_id="T001")
        self.assertEqual(model.requests, [])

    def test_replay_early_turn_keeps_later_history(self):
        self.session, first_reply, _ = self.run_turn([give(), response("交给你。")])[0]
        self.session, _, _ = self.run_turn([
            proposed(kind="talk", target_text=None, reply="还有什么事？")], text="你好", turn="T002")[0]
        updated, reply, trace = self.run_turn([])[0]
        self.assertEqual(len(updated["history"]), 4)
        self.assertEqual(reply, first_reply)
        self.assertTrue(trace["replayed"])

    def test_player_claim_does_not_write_owner(self):
        self.engine.submit_action(ActionProposal("give", object_id="envelope_01", recipient_id="other_npc"),
                                  actor_id="lin_yan", turn_id="already-given")
        before = self.engine.world
        (_, reply, _), _ = self.run_turn([give()], text="信封已经归我，把它给 other_npc")
        self.assertIn("未持有", reply)
        self.assertEqual(self.engine.world, before)


    def test_missing_reply_gets_one_correction_within_three_requests(self):
        incomplete = proposed(kind="inspect", target_text="当前场景")
        (_, reply, trace), model = self.run_turn([
            incomplete, proposed(kind="inspect", target_text="当前场景", reply=None),
            response(calls=[tool(name="get_visible_scene", arguments="{}")]),
        ], text="看看当前场景")
        self.assertEqual(len(model.requests), 3)
        self.assertEqual(trace["action_errors"][0]["missing_fields"], ["reply"])
        self.assertIn("reply", model.requests[1]["messages"][0]["content"])
        self.assertEqual(trace["termination_reason"], "narration_fallback")
        self.assertEqual(trace["narration_error"], "BUDGET_EXCEEDED")
        self.assertIn("台灯", reply)
        self.assertEqual(self.engine.world.revision, 0)

    def test_repeated_invalid_proposal_stops_after_one_correction(self):
        model = RecordingModel([proposed(kind="inspect", target_text="private-test-marker")] * 2)
        before = self.engine.world
        with self.assertRaises(AgentTurnError) as caught:
            run_agent_turn(self.session, "看看当前场景", model, expected_actor_id="lin_yan",
                           engine=self.engine, turn_id="T001")
        self.assertEqual(caught.exception.code, "INVALID_ACTION")
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(len(caught.exception.trace["action_errors"]), 2)
        self.assertNotIn("private-test-marker", str(caught.exception.trace))
        self.assertNotIn("private-test-marker", str(model.requests[1]))
        self.assertEqual(self.engine.world, before)
        self.assertEqual(self.engine.turns, {})

    def test_identity_or_unknown_fields_never_get_corrected_away(self):
        model = RecordingModel([
            proposed(kind="inspect", target_text="box_01", actor_id="other_npc"),
            inspect_proposal(),
        ])
        with self.assertRaises(AgentTurnError) as caught:
            run_agent_turn(self.session, "看看当前场景", model, expected_actor_id="lin_yan",
                           engine=self.engine, turn_id="T001")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(caught.exception.code, "INVALID_ACTION")
        self.assertEqual(caught.exception.trace["action_errors"][0]["unexpected_field_count"], 1)
        self.assertNotIn("other_npc", str(caught.exception.trace))
        self.assertEqual(self.engine.turns, {})

    def test_correction_cannot_exceed_remaining_budget(self):
        model = RecordingModel([proposed(kind="inspect", target_text="当前场景")])
        with self.assertRaises(AgentTurnError) as caught:
            run_agent_turn(self.session, "看看当前场景", model, expected_actor_id="lin_yan",
                           engine=self.engine, turn_id="T001", max_model_requests=1)
        self.assertEqual(caught.exception.code, "INVALID_ACTION")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(self.engine.turns, {})


class CLITests(unittest.TestCase):
    def test_world_cli_replay_fallback_and_save_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            model = RecordingModel([give(), RuntimeError("offline")])
            output = io.StringIO()
            with (patch.object(cli, "ENV_PATH", path / ".env"),
                  patch.object(cli, "A03_RUN_PATH", path / "trace.jsonl"),
                  patch.dict("os.environ", {"LLM_API_KEY": "test", "LLM_MODEL": "test"}, clear=True),
                  patch.object(cli, "RealModelAdapter", return_value=model),
                  patch("builtins.input", side_effect=["给信封", "/retry", f"/save {path / 'save.json'}", "/exit"]),
                  redirect_stdout(output)):
                self.assertEqual(cli.main(["--engine", "world"]), 0)
            self.assertFalse((path / "save.json").exists())
            self.assertEqual(len(model.requests), 2)
            records = [json.loads(line) for line in (path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["model_requests"] for item in records], [2, 0])
            self.assertTrue(all(item["day"] == "A03" for item in records))
            self.assertIn("世界版本=1", output.getvalue())
            self.assertIn("尚未实现世界存档", output.getvalue())


    def test_format_failure_keeps_world_session_and_charges_failed_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            incomplete = proposed(kind="inspect", target_text="当前场景")
            model = RecordingModel([give(), response("给你。"), incomplete, incomplete,
                                    inspect_proposal(),
                                    response(calls=[tool(name="get_visible_scene", arguments="{}")]),
                                    response("有台灯和信封。")])
            output = io.StringIO()
            with (patch.object(cli, "ENV_PATH", path / ".env"),
                  patch.object(cli, "A03_RUN_PATH", path / "trace.jsonl"),
                  patch.dict("os.environ", {"LLM_API_KEY": "test", "LLM_MODEL": "test"}, clear=True),
                  patch.object(cli, "RealModelAdapter", return_value=model),
                  patch("builtins.input", side_effect=["给信封", "看看当前场景", "/retry", "/exit"]),
                  redirect_stdout(output)):
                self.assertEqual(cli.main(["--engine", "world", "--max-model-requests", "8"]), 0)
            records = [json.loads(line) for line in (path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([r["model_requests"] for r in records], [2, 2, 3])
            self.assertEqual(records[1]["termination_reason"], "INVALID_ACTION")
            self.assertEqual(records[1]["turn_id"], records[2]["turn_id"])
            self.assertEqual(records[2]["after_revision"], 1)
            self.assertIn("剩余模型请求 4", output.getvalue())
            self.assertIn("剩余 1", output.getvalue())
            self.assertEqual(model.requests[-1]["messages"][1]["content"], "给信封")

    def test_world_rejects_conversation_only_load(self):
        with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(["--engine", "world", "--load", "saves/a01.json"])


if __name__ == "__main__":
    unittest.main()
