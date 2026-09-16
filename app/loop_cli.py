"""A04 顺序 CLI：一个 asyncio.run 管理模型客户端与整局生命周期。"""

import asyncio
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from app.async_runtime import RUNTIME_VERSION, run_agent_turn
from app.engine import TurnConflict, WorldEngine, request_digest
from app.execution import RunLimits
from app.model import AsyncRealModelAdapter
from app.query_executor import ReadonlyExecutor
from app.runtime import AgentTurnError
from app.session import create_session
from app.world import create_world

ROOT = Path(__file__).resolve().parent.parent
TRACE_PATH = ROOT / "runs" / "a04.jsonl"


def load_model() -> AsyncRealModelAdapter:
    load_dotenv(ROOT / ".env", override=False, encoding="utf-8-sig")
    api_key = os.getenv("LLM_API_KEY", "").strip()
    model_name = os.getenv("LLM_MODEL", "qwen3.7-plus").strip()
    base_url = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
    if not api_key:
        raise ValueError("A04 互动需要 LLM_API_KEY；离线演示：python -m scripts.a04_demo")
    if not model_name or not base_url or model_name == "qwen-plus-character":
        raise ValueError("请配置支持 Function Calling 及 tool_choice=required 的 LLM_MODEL（例如 qwen3.7-plus）及 LLM_BASE_URL。")
    return AsyncRealModelAdapter(api_key, model_name, base_url)


async def chat(model, *, session: dict, limits: RunLimits, max_model_requests: int,
               trace_path: Path = TRACE_PATH) -> int:
    engine = WorldEngine(create_world(session["session_id"]))
    executor = ReadonlyExecutor(limits.max_parallel_tools)
    remaining, last_turn = max_model_requests, None
    print(f"AI互动世界导演 · A04 [{model.mode}]\n会话：{session['session_id']}")
    print("/retry 重发上一请求；/exit 退出。世界保存在内存中，Ctrl+C 取消并退出。")
    print(f"轨迹：{trace_path.resolve()}（运行协议 {RUNTIME_VERSION}）")
    print(f"模型输入输出：{trace_path.with_name(trace_path.stem + '_io').resolve()}（通过 io_ref 关联）")
    try:
        while True:
            # input 只在上一轮所有任务都回收后执行；运行中的模型请求始终是异步的。
            try:
                text = input("你：").strip()
            except EOFError:
                return 0
            if text == "/exit":
                return 0
            if not text:
                continue
            if text == "/retry":
                if last_turn is None:
                    print("暂无可重发回合。")
                    continue
                turn_id, text = last_turn
            elif text.startswith("/"):
                print("可用 /retry 或 /exit；本课没有世界存档。")
                continue
            else:
                turn_id = str(uuid4())
                last_turn = turn_id, text
            try:
                result = await run_agent_turn(
                    session, text, model, expected_actor_id=session["actor_id"], engine=engine,
                    turn_id=turn_id, executor=executor, trace_path=trace_path,
                    limits=replace(limits, max_model_requests=min(limits.max_model_requests, remaining)))
                session = result.session
                trace = result.trace
                print(f"\n林砚：{result.reply}")
            except AgentTurnError as error:
                trace = error.trace
                print(f"本轮未提交（{error.code}）。")
                if error.code == "TOOL_CALL_REQUIRED":
                    print("模型未调用工具；本轮必须通过终结工具提交结果。请通过 io_ref 查看原始响应。")
                elif error.code == "TERMINAL_TOOL_CONFLICT":
                    print("终结工具必须单独调用；本批工具均未执行。")
                print("可用 /retry 重试。")
            except TurnConflict:
                print("同一 turn_id 的请求冲突，未再次执行。")
                continue
            remaining -= trace["model_requests"]
            if trace["trace_write_failed"]:
                print("轨迹写入失败；本轮业务结果仍有效，请检查 runs 目录。")
            print(f"run_id={trace['run_id']} turn_id={turn_id}")
            print(f"{trace['termination_reason']}；请求 {trace['model_requests']}；剩余 {remaining}；"
                  f"世界版本 {engine.world.revision}；事件 {len(engine.world.events)}")
    except asyncio.CancelledError as error:
        if getattr(error, "trace", {}).get("trace_write_failed"):
            print("轨迹写入失败；取消证据不完整。")
        print("\n本次运行已取消。")
        if last_turn is not None:
            turn_id, text = last_turn
            saved = engine.lookup(actor_id=session["actor_id"], turn_id=turn_id,
                                  digest=request_digest("text", text))
            if saved is not None:
                print("本轮已提交，确定性回执：" + saved["receipt"]["message"])
                if saved["trace"] and saved["trace"].get("trace_write_failed"):
                    print("轨迹写入失败；已提交事实仍保留。")
            else:
                print("本轮未提交；世界保持取消前的已提交版本。")
        raise
    finally:
        await model.aclose()


async def configured_chat(args, limits: RunLimits) -> int:
    model = load_model()
    print(f"模型：{model.model}")
    try:
        session = create_session(actor_id="lin_yan", goal_id=os.getenv("CHARACTER_GOAL", "clarify").strip())
    except ValueError:
        await model.aclose()
        raise
    return await chat(model, session=session, limits=limits, max_model_requests=args.max_model_requests)


def main(args) -> int:
    try:
        if args.load:
            raise ValueError("A04 世界只在内存中，不能加载 A01/A02 对话存档。")
        if args.max_model_requests < 1:
            raise ValueError("进程模型请求预算必须大于 0。")
        limits = RunLimits(max_steps=args.max_steps, turn_timeout_s=args.turn_timeout,
                           max_parallel_tools=args.max_parallel_tools)
        return asyncio.run(configured_chat(args, limits))
    except KeyboardInterrupt:
        return 130
    except ValueError as error:
        print(str(error))
        return 1
