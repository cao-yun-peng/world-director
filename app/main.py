"""会话入口：默认 A01 对话，可选 A02 原生只读工具。"""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import APIError

from app.character import GOALS
from app.model import FakeModelAdapter, ModelAdapter, RealModelAdapter
from app.runtime import AgentTurnError, run_agent_turn
from app.scene_data import FACTS
from app.session import create_session, run_turn
from app.storage import load_session, save_session
from app.view import build_view

ACTOR_ID = "lin_yan"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
RUN_PATH = PROJECT_ROOT / "runs" / "a01.jsonl"
A02_RUN_PATH = PROJECT_ROOT / "runs" / "a02.jsonl"


def append_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_agent_trace(session: dict, trace: dict, model_name: str) -> None:
    record = {"day": "A02", "session_id": session["session_id"],
              "turn_index": len(session["history"]) // 2 + 1,
              "actor_id": ACTOR_ID, "goal_id": session["goal_id"],
              "prompt_version": session["prompt_version"], "mode": "real", "model": model_name,
              **trace}
    try:
        append_record(A02_RUN_PATH, record)
    except OSError:
        print("工具轨迹写入失败；请检查 runs 目录权限。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="林砚的会话：/save 路径 保存，/exit 退出")
    parser.add_argument("--load", type=Path, help="加载JSON会话")
    parser.add_argument("--engine", choices=("dialogue", "tools"), default="dialogue")
    parser.add_argument("--max-model-requests", type=int, default=12,
                        help="工具模式本进程的模型请求上限（默认 12，失败请求也计数）")
    args = parser.parse_args(argv)
    if args.max_model_requests < 1:
        parser.error("--max-model-requests 必须大于 0")
    remaining_requests = args.max_model_requests
    load_dotenv(ENV_PATH, override=False, encoding="utf-8-sig")
    try:
        if args.load:
            session = load_session(args.load, expected_actor_id=ACTOR_ID)
        else:
            session = create_session(actor_id=ACTOR_ID, goal_id=os.getenv("CHARACTER_GOAL", "clarify").strip())
    except (ValueError, OSError) as error:
        print(f"无法打开会话：{error}")
        return 1
    stage = "A02" if args.engine == "tools" else "A01"
    print(f"AI互动世界导演 · {stage}\n会话：{session['session_id']}\n目标：{session['goal_id']}")
    api_key = os.getenv("LLM_API_KEY", "").strip()
    model: ModelAdapter

    if api_key:
        model_name = os.getenv("LLM_MODEL", "qwen-plus-character").strip()
        base_url = os.getenv(
            "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).strip()
        if not model_name or not base_url:
            print("请配置 LLM_MODEL 和 LLM_BASE_URL 后重试。")
            return 1
        if args.engine == "tools" and model_name == "qwen-plus-character":
            print('工具模式需要支持 Function Calling 的模型；请设置 $env:LLM_MODEL = "qwen-plus"。')
            return 1
        model = RealModelAdapter(api_key, model_name, base_url)
        mode = "real"
        print(f"[real mode] 模型：{model_name}")
    else:
        if args.engine == "tools":
            print("工具模式需要 LLM_API_KEY；离线协议验证请运行 tests/test_a02.py。")
            return 1
        model = FakeModelAdapter()
        model_name = "FakeLLM"
        mode = "fake"
        print("[offline mode] 未设置 LLM_API_KEY，使用离线演示。")

    print("输入 /save 路径 保存；/exit 退出（不会自动保存）。")
    while True:
        try:
            user_input = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n会话结束，未自动保存。")
            return 0
        if user_input == "/exit":
            return 0
        if not user_input:
            print("请输入一句话。")
            continue
        command, _, argument = user_input.partition(" ")
        if command == "/save":
            if not argument.strip():
                print("用法：/save saves/a01.json")
                continue
            try:
                save_session(session, Path(argument.strip()), expected_actor_id=ACTOR_ID)
                print("会话已保存。")
            except (ValueError, OSError) as error:
                print(f"保存失败：{error}")
            continue
        if user_input.startswith("/"):
            print("未知命令，可用 /save 路径 或 /exit。")
            continue
        try:
            if args.engine == "tools":
                updated, reply, trace = run_agent_turn(
                    session, user_input, model, expected_actor_id=ACTOR_ID,
                    max_model_requests=remaining_requests,
                )
            else:
                updated, reply = run_turn(session, user_input, model, expected_actor_id=ACTOR_ID)
        except AgentTurnError as error:
            write_agent_trace(session, error.trace, model_name)
            print(str(error))
            return 1
        except APIError:
            print("模型请求失败，请检查密钥、模型名、服务地址和网络。本轮未写入历史。")
            return 1
        except ValueError as error:
            print(str(error))
            return 1
        if args.engine == "tools":
            remaining_requests -= trace["model_requests"]
            write_agent_trace(session, trace, model_name)
            session = updated
            print(f"\n林砚：{reply}")
            print(f"[tools] {trace['intent']}；本轮请求 {trace['model_requests']}；剩余 {remaining_requests}")
            continue
        session = updated
        print(f"\n林砚：{reply}")
        record = {
            "day": "A01", "session_id": session["session_id"],
            "turn_index": len(session["history"]) // 2,
            "actor_id": session["actor_id"], "goal_id": session["goal_id"],
            "goal": GOALS[session["goal_id"]], "prompt_version": session["prompt_version"],
            "visible_fact_ids": [fact["id"] for fact in build_view(FACTS, ACTOR_ID)],
            "mode": mode, "model": model_name, "input": user_input, "output": reply,
        }
        try:
            append_record(RUN_PATH, record)
        except OSError:
            print("运行日志写入失败；当前历史仍在内存中，请尝试 /save 保存。")


if __name__ == "__main__":
    raise SystemExit(main())
