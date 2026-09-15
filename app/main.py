"""D002：一次玩家输入 → 一次角色回复 → 一条运行记录。"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import APIError

from app.character import BASE_CARD, GOALS, PROMPT_VERSION, build_prompt
from app.model import FakeModelAdapter, ModelAdapter, RealModelAdapter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
RUN_PATH = PROJECT_ROOT / "runs" / "d002.jsonl"


def main() -> int:
    # 加载项目配置；终端里已有的环境变量优先。
    load_dotenv(ENV_PATH, override=False, encoding="utf-8-sig")
    print("AI互动世界导演 · D002")
    goal_id = os.getenv("CHARACTER_GOAL", "clarify").strip()
    if goal_id not in GOALS:
        print(f"未知目标：{goal_id}。可选值：{', '.join(GOALS)}")
        return 1
    # 新建角色卡，避免切换目标时改写共享配置。
    card = {**BASE_CARD, "goal": GOALS[goal_id]}
    print(f"当前目标：{goal_id} — {card['goal']}")

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

    user_input = input("你：").strip()
    if not user_input:
        print("请输入一句话后重新运行。")
        return 1

    messages = [
        {"role": "system", "content": build_prompt(card)},
        {"role": "user", "content": user_input},
    ]
    try:
        reply = model.generate(messages)
    except APIError:
        # SDK 异常可能包含服务端请求信息，不直接打印或保存。
        print("模型请求失败，请检查密钥、模型名、服务地址和网络。")
        return 1
    except ValueError as error:
        print(str(error))
        return 1

    print(f"\n{card['name']}：{reply}")
    record = {
        "day": "D002",
        "goal_id": goal_id,
        "goal": card["goal"],
        "prompt_version": PROMPT_VERSION,
        "mode": mode,
        "model": model_name,
        "input": user_input,
        "output": reply,
    }
    RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUN_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"\n记录已追加：{RUN_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
