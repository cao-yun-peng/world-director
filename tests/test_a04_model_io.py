"""每次实际发送的输入/输出关联落盘；真实 SDK + MockTransport，不联网。"""

import asyncio
import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI

from app.async_runtime import run_agent_turn
from app.engine import WorldEngine
from app.execution import RunLimits, TransientFailure
from app.model import AsyncRealModelAdapter
from app.runtime import AgentTurnError
from app.session import create_session
from app.world import create_world
from scripts.a04_demo import ScriptedModel, call, terminal, response


class ModelIOTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "trace.jsonl"
        self.session = create_session(actor_id="lin_yan", goal_id="clarify")
        self.engine = WorldEngine(create_world(self.session["session_id"]))
        self.limits = RunLimits(retry_delay_s=0)

    async def run_turn(self, model, *, limits=None):
        return await run_agent_turn(self.session, "用户原文", model, expected_actor_id="lin_yan",
                                    engine=self.engine, turn_id="T1", trace_path=self.path,
                                    limits=limits or self.limits)

    def exchanges(self, trace):
        result = []
        for row in trace["records"]:
            if row["kind"] not in ("model", "narration"):
                continue
            path = self.path.parent / row["io_ref"]
            self.assertTrue(path.resolve().is_relative_to(self.path.parent.resolve()))
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            for record in records:
                for key in ("run_id", "turn_id", "session_id", "step_id", "span_id", "attempt"):
                    self.assertEqual(record[key], row[key])
            self.assertEqual(records[0]["phase"], "input")
            self.assertEqual(records[-1]["phase"], "end")
            result.append(records)
        return result

    def adapter(self, transport):
        native = AsyncOpenAI(api_key="AUTH-MUST-NOT-APPEAR", base_url="https://example.invalid/v1",
                             max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
        with patch("app.model.AsyncOpenAI", return_value=native):
            model = AsyncRealModelAdapter("AUTH-MUST-NOT-APPEAR", "test-model", "https://example.invalid/v1")
        self.addAsyncCleanup(model.aclose)
        return model

    async def test_sdk_body_matches_saved_input_and_invalid_output_is_preserved(self):
        sent = []
        malformed = '{"reply":"未闭合的原文'
        valid = json.dumps({"reply": "你好"}, ensure_ascii=False)

        async def transport(request):
            # 请求进入传输层时，input 必须已经落盘。
            inputs = list(self.path.parent.glob("trace_io/*/*.jsonl"))
            self.assertEqual(len(inputs), len(sent) + 1)
            sent.append(json.loads(request.content))
            return httpx.Response(200, headers={"x-request-id": f"provider-{len(sent)}", "x-private": "HEADER-SECRET"},
                json={"id": f"completion-{len(sent)}", "object": "chat.completion", "created": 0,
                      "model": "test-model", "choices": [{"index": 0, "finish_reason": "tool_calls",
                      "message": {"role": "assistant", "content": None, "tool_calls": [
                          call("end_turn", call_id=f"end-{len(sent)}", arguments=malformed if len(sent) == 1 else valid)]}}]})

        result = await self.run_turn(self.adapter(transport))
        exchanges = self.exchanges(result.trace)
        self.assertEqual(len(exchanges), 2)
        for index, records in enumerate(exchanges):
            body = deepcopy(records[0]["payload"])
            # SDK 的 extra_body 合并为 HTTP JSON 顶层；其余参数必须逐项一致。
            body.update(body.pop("extra_body"))
            self.assertEqual(body, sent[index])
            output = records[1]["payload"]
            self.assertEqual(output["format"], "provider_completion")
            self.assertEqual(output["provider_request_id"], f"provider-{index + 1}")
            self.assertEqual(output["response"]["id"], f"completion-{index + 1}")
            self.assertEqual(output["response"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"], malformed if index == 0 else valid)
        feedback = exchanges[1][0]["payload"]["messages"][-1]
        self.assertEqual(feedback["tool_call_id"], "end-1")
        self.assertEqual(json.loads(feedback["content"])["error"]["code"], "INVALID_ARGUMENTS")
        text = json.dumps(exchanges, ensure_ascii=False)
        self.assertIn("用户原文", text)
        self.assertNotIn("AUTH-MUST-NOT-APPEAR", text)
        self.assertNotIn("HEADER-SECRET", text)
        self.assertNotIn("用户原文", json.dumps(result.trace, ensure_ascii=False))

    async def test_sdk_terminal_round_trip_requires_tools_and_keeps_narration_text(self):
        sent = []
        completions = [response(calls=[call()]), terminal("give", object_id="envelope_01", recipient_id="other_npc"), response("信封给你。")]

        async def transport(request):
            sent.append(json.loads(request.content))
            completion = completions[len(sent) - 1]
            return httpx.Response(200, json={
                "id": f"schema-{len(sent)}", "model": "test-model", "object": "chat.completion", "created": 1,
                "choices": [{"index": 0, "finish_reason": completion["finish_reason"],
                             "message": completion["message"]}]})

        result = await self.run_turn(self.adapter(transport))
        self.assertEqual(result.trace["termination_reason"], "completed")
        self.assertEqual(result.trace["model_requests"], 3)
        self.assertEqual(len(self.engine.world.events), 2)
        self.assertEqual(self.engine.world.events[0]["kind"], "DiscoveryEvent")
        for body in sent[:2]:
            self.assertNotIn("response_format", body)
            self.assertEqual(body["tool_choice"], "required")
            self.assertEqual({tool["function"]["name"] for tool in body["tools"]},
                             {"get_visible_scene", "inspect_object", "end_turn", "move", "give"})
            self.assertFalse(body["enable_thinking"])
        self.assertEqual(sent[1]["messages"][-1]["tool_call_id"], "q1")
        self.assertEqual(json.loads(sent[1]["messages"][-1]["content"])["data"]["id"], "lamp_01")
        self.assertNotIn("response_format", sent[2])
        self.assertEqual(sent[2]["tool_choice"], "none")
        for body, records in zip(sent, self.exchanges(result.trace), strict=True):
            saved = deepcopy(records[0]["payload"])
            saved.update(saved.pop("extra_body"))
            self.assertEqual(saved, body)

    async def test_tools_results_and_narration_have_their_actual_context(self):
        model = ScriptedModel([response(calls=[call()]), terminal("give", object_id="envelope_01", recipient_id="other_npc"), response("信封给你。")])
        result = await self.run_turn(model)
        exchanges = self.exchanges(result.trace)
        self.assertEqual(len(exchanges), 3)
        for records, request in zip(exchanges, model.requests, strict=True):
            self.assertEqual(records[0]["payload"], request)
        first_output = exchanges[0][1]["payload"]["response"]
        self.assertEqual(first_output["message"]["tool_calls"][0]["id"], "q1")
        second_input = exchanges[1][0]["payload"]["messages"]
        self.assertEqual(second_input[-1]["tool_call_id"], "q1")
        self.assertEqual(json.loads(second_input[-1]["content"])["data"]["id"], "lamp_01")
        self.assertEqual(exchanges[2][0]["kind"], "narration")
        self.assertEqual(len(exchanges[2][0]["payload"]["messages"]), 2)
        self.assertNotIn("response_format", exchanges[2][0]["payload"])

    async def test_transport_retries_have_separate_files_and_failure_has_no_output(self):
        model = ScriptedModel([TransientFailure(), terminal(reply="你好")])
        result = await self.run_turn(model)
        exchanges = self.exchanges(result.trace)
        self.assertEqual([record["phase"] for record in exchanges[0]], ["input", "end"])
        self.assertEqual(exchanges[0][-1]["payload"]["error_code"], "TRANSIENT_FAILURE")
        self.assertEqual([record["phase"] for record in exchanges[1]], ["input", "output", "end"])
        models = [row for row in result.trace["records"] if row["kind"] == "model"]
        self.assertEqual(models[0]["span_id"], models[1]["span_id"])
        self.assertNotEqual(models[0]["io_ref"], models[1]["io_ref"])
        self.assertEqual([row["attempt"] for row in models], [1, 2])

    async def test_cancelled_inflight_call_keeps_input_and_no_world_commit(self):
        entered = asyncio.Event()

        async def blocked(messages):
            entered.set()
            await asyncio.Event().wait()

        model = ScriptedModel([blocked])
        task = asyncio.create_task(self.run_turn(model))
        await asyncio.wait_for(entered.wait(), 2)
        # 模型还没返回时，主 trace 已经能找到对应输入。
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        started = next(row for row in rows if row["kind"] == "model_request")
        self.assertTrue((self.path.parent / started["io_ref"]).exists())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError) as caught:
            await task
        exchanges = self.exchanges(caught.exception.trace)
        self.assertEqual([record["phase"] for record in exchanges[0]], ["input", "end"])
        self.assertEqual(exchanges[0][-1]["payload"]["status"], "cancelled")
        self.assertEqual(self.engine.turns, {})

    async def test_timeout_keeps_input_and_explicit_end_reason(self):
        async def blocked(messages):
            await asyncio.Event().wait()
        with self.assertRaises(AgentTurnError) as caught:
            await self.run_turn(ScriptedModel([blocked]), limits=replace(self.limits, turn_timeout_s=0.05))
        exchange = self.exchanges(caught.exception.trace)[0]
        self.assertEqual([record["phase"] for record in exchange], ["input", "end"])
        self.assertEqual(exchange[-1]["payload"]["error_code"], "TURN_TIMEOUT")

    async def test_io_write_failure_does_not_retry_or_duplicate_world_action(self):
        # 把正文目录占成文件，主 trace 仍能正常写入。
        (self.path.parent / "trace_io").write_text("blocked", encoding="utf-8")
        result = await self.run_turn(ScriptedModel([
            terminal("give", object_id="envelope_01", recipient_id="other_npc"), response("给你。")]))
        self.assertTrue(result.trace["trace_write_failed"])
        self.assertEqual(result.trace["model_requests"], 2)
        self.assertEqual(len(self.engine.world.events), 1)
        rows = [row for row in result.trace["records"] if row["kind"] in ("model", "narration")]
        self.assertTrue(all(row["io_write_failed"] for row in rows))
        replay = await self.run_turn(ScriptedModel([]))
        self.assertEqual(replay.trace["model_requests"], 0)
        self.assertEqual(len(self.engine.world.events), 1)

    async def test_replayed_turn_creates_no_new_model_io(self):
        await self.run_turn(ScriptedModel([terminal(reply="你好")]))
        before = set(self.path.parent.glob("trace_io/*/*.jsonl"))
        result = await self.run_turn(ScriptedModel([]), limits=replace(self.limits, max_model_requests=0))
        self.assertEqual(result.trace["model_requests"], 0)
        self.assertEqual(set(self.path.parent.glob("trace_io/*/*.jsonl")), before)
        self.assertFalse(any("io_ref" in row for row in result.trace["records"]))

    async def test_empty_choices_response_is_saved_before_protocol_rejection(self):
        async def transport(request):
            return httpx.Response(200, json={"id": "empty", "object": "chat.completion", "created": 0,
                                             "model": "test-model", "choices": []})
        with self.assertRaises(AgentTurnError) as caught:
            await self.run_turn(self.adapter(transport))
        self.assertEqual(caught.exception.code, "MODEL_PROTOCOL_ERROR")
        exchange = self.exchanges(caught.exception.trace)[0]
        self.assertEqual(exchange[1]["payload"]["response"]["choices"], [])
        self.assertEqual(self.engine.turns, {})


if __name__ == "__main__":
    unittest.main()
