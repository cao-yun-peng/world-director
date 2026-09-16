"""A04 元数据轨迹。业务事实仍以 WorldEngine 的正式事件为准。"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from uuid import uuid4


def call_reference(call_id: str) -> str:
    """原生 ID 留在协议里；日志使用稳定摘要，避免模型在 ID 中夹带正文。"""
    return hashlib.sha256(call_id.encode("utf-8")).hexdigest()[:24]


class RunTrace:
    FIELDS = {
        "step_id", "span_id", "parent_span_id", "attempt", "tool_name", "call_id",
        "snapshot_revision", "committed_revision", "started_at", "duration_ms",
        "error_code", "termination_reason", "model_requests", "usage",
        "replayed", "cleanup_ms", "trace_write_failed", "decision_details",
        "runtime_version", "model_name", "provider_host", "limits", "history_messages",
        "request_options", "response_details", "decision_kind", "repair_used", "repairable",
    }

    def __init__(self, session_id: str, turn_id: str, *, mode: str, path: Path | None = None):
        if mode not in ("fake", "real"):
            raise ValueError("mode 必须是 fake 或 real。")
        self.run_id = str(uuid4())
        self.context = dict(session_id=session_id, turn_id=turn_id, run_id=self.run_id, mode=mode,
                            trace_version=2)
        self.path = path
        self.records: list[dict] = []
        self.write_failed = False
        self.started = monotonic()

    def emit(self, kind: str, status: str, **fields) -> None:
        record = {**self.context, "record_seq": len(self.records) + 1,
                  "kind": kind, "status": status,
                  "span_id": self.run_id, "parent_span_id": None,
                  "started_at": datetime.now(timezone.utc).isoformat(),
                  **{key: value for key, value in fields.items() if key in self.FIELDS}}
        self.records.append(record)
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            except OSError:
                # 不能因记录失败重新执行业务。调用方通过结果/CLI 明确报告证据缺口。
                self.write_failed = True

    def finish(self, reason: str, *, model_requests: int, committed_revision: int | None) -> None:
        self.emit("run_finished", reason, termination_reason=reason,
                  model_requests=model_requests, committed_revision=committed_revision,
                  duration_ms=round((monotonic() - self.started) * 1000, 3),
                  trace_write_failed=self.write_failed)


def safe_usage(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {key: value[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if type(value.get(key)) is int and value[key] >= 0} or None


def safe_response_details(value) -> dict:
    """服务返回与业务校验分开；元数据不包含正文、工具参数或任意服务端字段。"""
    if not isinstance(value, dict):
        return {"shape": "invalid_completion"}
    reason = value.get("finish_reason")
    known_reasons = ("stop", "length", "tool_calls", "content_filter", "function_call")
    details = {"finish_reason": reason if isinstance(reason, str) and reason in known_reasons else "unknown"}
    message = value.get("message")
    if not isinstance(message, dict):
        return {**details, "shape": "invalid_message"}
    content, calls = message.get("content"), message.get("tool_calls")
    details.update(
        is_assistant=message.get("role") == "assistant",
        content_type="null" if content is None else "string" if isinstance(content, str) else "invalid",
        content_length=len(content) if isinstance(content, str) else None,
        tool_calls_type="null" if calls is None else "list" if isinstance(calls, list) else "invalid",
        tool_call_count=len(calls) if isinstance(calls, list) else 0 if calls is None else None,
    )
    return details
