"""A01：多轮对话、显式保存与跨进程恢复。"""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import APIError

from app.character import GOALS
from app.model import FakeModelAdapter, ModelAdapter, RealModelAdapter
from app.scene_data import FACTS
from app.session import create_session, run_turn
from app.storage import load_session, save_session
from app.view import build_view

ACTOR_ID = "lin_yan"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
RUN_PATH = PROJECT_ROOT / "runs" / "a01.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="林砚的会话：/save 路径 保存，/exit 退出")
    parser.add_argument("--load", type=Path, help="加载JSON会话")
    args = parser.parse_args(argv)
    load_dotenv(ENV_PATH, override=False, encoding="utf-8-sig")
    try:
        if args.load:
            session = load_session(args.load, expected_actor_id=ACTOR_ID)
        else:
            session = create_session(actor_id=ACTOR_ID, goal_id=os.getenv("CHARACTER_GOAL", "clarify").strip())
    except (ValueError, OSError) as error:
        print(f"无法打开会话：{error}")
        return 1
    print(f"AI互动世界导演 · A01\n会话：{session['session_id']}\n目标：{session['goal_id']}")
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
        model = RealModelAdapter(api_key, model_name, base_url)
        mode = "real"
        print(f"[real mode] 模型：{model_name}")
    else:
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
                save_session(session, Path(argument.strip()))
                print("会话已保存。")
            except (ValueError, OSError) as error:
                print(f"保存失败：{error}")
            continue
        if user_input.startswith("/"):
            print("未知命令，可用 /save 路径 或 /exit。")
            continue
        try:
            updated, reply = run_turn(session, user_input, model)
        except APIError:
            print("模型请求失败，请检查密钥、模型名、服务地址和网络。本轮未写入历史。")
            return 1
        except ValueError as error:
            print(str(error))
            return 1
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
            RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
            with RUN_PATH.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            print("运行日志写入失败；当前历史仍在内存中，请尝试 /save 保存。")


if __name__ == "__main__":
    raise SystemExit(main())
