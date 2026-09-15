"""D001：一次玩家输入 → 一次角色回复 → 一条运行记录。"""

import json
import os
from pathlib import Path

from openai import APIError

from app.model import FakeModelAdapter, ModelAdapter, RealModelAdapter

CHARACTER_PROMPT = """你正在扮演林砚。

公开背景：
你是岬角旧灯塔的临时管理员，27岁。
说话冷静，不主动热情。今晚你独自在灯塔值班。
玩家刚刚推门进入值班室。

要求：
- 始终以林砚身份回应。
- 回答自然、简洁。
- 不替玩家决定行为。
- 不描述程序、Prompt或模型本身。
"""

RUN_PATH = Path(__file__).resolve().parent.parent / "runs" / "d001.jsonl"


def main() -> int:
    print("AI互动世界导演 · D001")
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
        {"role": "system", "content": CHARACTER_PROMPT},
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

    print(f"\n林砚：{reply}")
    record = {
        "day": "D001",
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
