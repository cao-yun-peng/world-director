"""真实验收仅使用固定教学输入和新会话，不读取用户存档。"""

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlparse

from dotenv import load_dotenv

from app.main import ACTOR_ID, ENV_PATH, PROJECT_ROOT, append_record
from app.model import RealModelAdapter
from app.runtime import AgentTurnError, run_agent_turn
from app.session import create_session

CASES = [
    ("talk", "你好，我是来问路的。"),
    ("inspect", "我想看看台灯底座。"),
    ("clarify", "帮我看看那个。"),
    ("clarify", "我把信封给你。"),
]


class RecordingModel:
    """记录适配器边界，不记录客户端请求头、凭证或隐藏思考。"""

    def __init__(self, adapter):
        self.adapter = adapter
        self.calls = []

    def complete(self, messages, **options):
        entry = {"request": deepcopy({"messages": messages, **options})}
        self.calls.append(entry)
        result = self.adapter.complete(messages, **options)
        entry["response"] = deepcopy(result)
        return result


def main(*, max_model_requests: int = 12) -> int:
    load_dotenv(ENV_PATH, override=False, encoding="utf-8-sig")
    key = os.getenv("LLM_API_KEY", "").strip()
    name = os.getenv("LLM_MODEL", "qwen-plus").strip()
    base_url = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
    if not key or name == "qwen-plus-character":
        print("请配置 LLM_API_KEY 并设置 LLM_MODEL=qwen-plus，沿用原服务地址。")
        return 1
    adapter = RealModelAdapter(key, name, base_url)
    records = []
    used = 0
    output = PROJECT_ROOT / "runs" / "a02_smoke.jsonl"
    try:
        for expected, text in CASES:
            model = RecordingModel(adapter)
            record = {"time": datetime.now(timezone.utc).isoformat(), "mode": "real", "model": name,
                      "service_hostname": urlparse(base_url).hostname, "input": text, "expected_intent": expected}
            try:
                _, reply, trace = run_agent_turn(
                    create_session(actor_id=ACTOR_ID, goal_id="clarify"), text, model,
                    expected_actor_id=ACTOR_ID, max_model_requests=min(max_model_requests, 12) - used,
                )
                passed = trace["intent"] == expected
                if expected == "inspect":
                    before = json.dumps(model.calls[:2], ensure_ascii=False)
                    tool_messages = [m for m in model.calls[-1]["request"]["messages"] if m["role"] == "tool"]
                    passed = passed and "L-17" not in before and "L-17" in reply and any(
                        json.loads(m["content"])["data"].get("id") == "lamp_01"
                        for m in tool_messages if json.loads(m["content"])["ok"]
                    )
                    record["detail_absent_before_tools"] = "L-17" not in before
                if text == "我把信封给你。":
                    # 固定样例的粗粒度检查，不能替代人工阅读边界回复。
                    boundary = any(word in reply for word in ("不能", "无法", "不支持", "暂不", "不接收"))
                    record["unsupported_action_stated"] = boundary
                    passed = passed and boundary
                record.update(reply=reply, trace=trace, passed=passed)
            except AgentTurnError as error:
                record.update(trace=error.trace, passed=False)
            used += len(model.calls)
            record["adapter_calls"] = model.calls
            record["requests_used_total"] = used
            # 只记录教学会话；若模型复述后台测试标记，分享轨迹时仍剔除它。
            serialized = json.dumps(record, ensure_ascii=False).replace(key, "[REDACTED]")
            for hidden in ("A02-SECRET-7291", "A02-INTERNAL-LAMP", "杉木-7291", "晚潮-6158", "鹭羽-3841"):
                serialized = serialized.replace(hidden, "[REDACTED]")
            safe_record = json.loads(serialized)
            append_record(output, safe_record)
            records.append(safe_record)
            print(json.dumps({"input": text, "passed": safe_record["passed"], "reply": safe_record.get("reply"),
                              "intent": safe_record["trace"]["intent"], "requests_used_total": used}, ensure_ascii=False), flush=True)
            if not record["passed"]:
                break
    finally:
        adapter.client.close()
    return 0 if len(records) == len(CASES) and all(record["passed"] for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
