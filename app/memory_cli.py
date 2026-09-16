"""A05 显式真实入口；三角色复用同一模型客户端，各自请求独立。"""

import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from app.character import ACTOR_CONFIGS
from app.engine import TurnConflict
from app.execution import RunLimits
from app.loop_cli import load_model
from app.memory_runtime import MemoryStory
from app.runtime import AgentTurnError

ROOT = Path(__file__).resolve().parent.parent


async def chat(model, *, limits, max_model_requests, trace_path=None):
    story = MemoryStory()
    actor_id, remaining, last_turn = "lin_yan", max_model_requests, None
    trace_path = trace_path or ROOT / "runs" / "a05.jsonl"
    print(f"A05 三角色记忆 [{model.mode}]，故事 {story.engine.world.session_id}")
    print("/actor lin_yan|other_npc|archive_keeper 切换对话入口；/retry 重发；/exit 退出。")
    print("切换不触发角色响应。世界与记忆只在内存中；无世界存档。")
    try:
        while True:
            try:
                text = input(f"你 → {ACTOR_CONFIGS[actor_id]['card']['name']}：").strip()
            except EOFError:
                return 0
            if text == "/exit":
                return 0
            if not text:
                continue
            if text.startswith("/actor "):
                target = text.partition(" ")[2].strip()
                if target not in ACTOR_CONFIGS:
                    print("未登记角色。")
                else:
                    actor_id = target
                continue
            if text == "/retry":
                if last_turn is None:
                    print("暂无可重发回合。")
                    continue
                target, turn_id, text = last_turn
            elif text.startswith("/"):
                print("可用 /actor、/retry、/exit；本课无世界存档。")
                continue
            else:
                target, turn_id = actor_id, str(uuid4())
                last_turn = target, turn_id, text
            try:
                result = await story.turn(target, text, model, turn_id=turn_id, trace_path=trace_path,
                    limits=replace(limits, max_model_requests=min(limits.max_model_requests, remaining)))
                trace = result.trace
                print(f"{ACTOR_CONFIGS[target]['card']['name']}：{result.reply}")
            except AgentTurnError as error:
                trace = error.trace
                print(f"本轮未提交：{error.code}；可 /retry。")
            except TurnConflict:
                print("回合编号冲突。")
                continue
            remaining -= trace["model_requests"]
            print(f"{trace['termination_reason']}；剩余请求 {remaining}；世界版本 {story.engine.world.revision}")
    finally:
        await model.aclose()


async def configured_chat(args, limits):
    model = load_model()
    return await chat(model, limits=limits, max_model_requests=args.max_model_requests)


def main(args):
    try:
        if args.load:
            raise ValueError("A05 没有世界存档，不能加载对话 JSON。")
        if args.max_model_requests < 1:
            raise ValueError("模型请求预算必须大于 0。")
        limits = RunLimits(max_steps=args.max_steps, turn_timeout_s=args.turn_timeout,
                           max_parallel_tools=args.max_parallel_tools, max_input_chars=args.max_input_chars)
        return asyncio.run(configured_chat(args, limits))
    except KeyboardInterrupt:
        return 130
    except ValueError as error:
        print(str(error))
        return 1
