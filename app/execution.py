"""一轮共用的预算和外部尝试；重试不增加逻辑 step，但增加真实请求数。"""

import asyncio
import math
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from app.trace import RunTrace, safe_response_details, safe_usage


class RunStopped(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class TransientFailure(Exception):
    """适配器确认的临时传输失败；只有这个类型和单次超时可以重试。"""


    def __init__(self, *, retry_after_s: float = 0.0):
        super().__init__("TRANSIENT_FAILURE")
        self.retry_after_s = retry_after_s if math.isfinite(retry_after_s) else 0.0


class CallFailure(RunStopped):
    """一次逻辑外部调用失败；查询可转换为业务反馈，模型请求终止本轮。"""


@dataclass(frozen=True)
class RunLimits:
    max_steps: int = 4
    max_model_requests: int = 8
    turn_timeout_s: float = 30.0
    model_attempt_timeout_s: float = 10.0
    query_attempt_timeout_s: float = 5.0
    max_tool_calls_per_batch: int = 2
    max_parallel_tools: int = 2
    max_attempts: int = 2
    retry_delay_s: float = 0.1
    max_input_chars: int | None = None
    max_embedding_requests: int = 2
    max_rerank_requests: int = 2
    rerank_timeout_s: float = 5.0

    def __post_init__(self):
        if self.max_input_chars is not None and (type(self.max_input_chars) is not int or self.max_input_chars < 1):
            raise ValueError("max_input_chars 必须是正整数或 None。")
        for name in ("max_steps", "max_parallel_tools", "max_attempts", "max_tool_calls_per_batch"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} 必须是正整数。")
        if type(self.max_model_requests) is not int or self.max_model_requests < 0:
            raise ValueError("max_model_requests 必须是非负整数（0 仍允许重放）。")
        if type(self.max_embedding_requests) is not int or self.max_embedding_requests < 0:
            raise ValueError('max_embedding_requests 必须是非负整数。')
        if self.max_tool_calls_per_batch > 2:
            raise ValueError("模型每批最多两个查询；执行器压力实验另走执行器接口。")
        if type(self.max_rerank_requests) is not int or self.max_rerank_requests < 0:
            raise ValueError('max_rerank_requests 必须是非负整数。')
        for name in ("rerank_timeout_s", "turn_timeout_s", "model_attempt_timeout_s", "query_attempt_timeout_s", "retry_delay_s"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0 or (name != "retry_delay_s" and value == 0):
                raise ValueError(f"{name} 必须是有限的有效时间。")


class SharedBudget:
    """排队前创建的场景额度；每次模型尝试扣一次，角色之间不重置。"""
    def __init__(self, limits: RunLimits, *, batch=None):
        self.batch = batch
        self.max_model_requests = limits.max_model_requests
        self.deadline = asyncio.get_running_loop().time() + limits.turn_timeout_s
        self.model_requests = 0
        self.chat_requests = 0
        self.embedding_requests = 0
        self.rerank_requests = 0
        self.max_rerank_requests = limits.max_rerank_requests
        self.max_embedding_requests = limits.max_embedding_requests

    def check(self):
        if asyncio.get_running_loop().time() >= self.deadline:
            raise RunStopped('TURN_TIMEOUT')

    def check_capacity(self):
        self.check()
        if (self.model_requests >= self.max_model_requests
                or (self.batch is not None and self.batch.remaining <= 0)):
            raise RunStopped('MODEL_REQUEST_LIMIT')

    def claim(self, kind='model'):
        self.check_capacity()
        if kind == 'embedding' and self.embedding_requests >= self.max_embedding_requests:
            raise RunStopped('EMBEDDING_REQUEST_LIMIT')
        if kind == 'rerank' and self.rerank_requests >= self.max_rerank_requests:
            raise RunStopped('RERANK_REQUEST_LIMIT')
        if self.batch is not None:
            self.batch.used += 1
        self.model_requests += 1
        if kind == 'embedding':
            self.embedding_requests += 1
        elif kind == 'rerank':
            self.rerank_requests += 1
        else:
            self.chat_requests += 1


class RunBudget:
    def __init__(self, limits: RunLimits, trace: RunTrace, *, shared: SharedBudget | None = None):
        self.limits, self.trace = limits, trace
        self.deadline = asyncio.get_running_loop().time() + limits.turn_timeout_s
        self.shared = shared
        if shared is not None:
            self.deadline = min(self.deadline, shared.deadline)
        self.model_requests = 0
        self.chat_requests = 0
        self.embedding_requests = 0
        self.rerank_requests = 0
        self.max_rerank_requests = limits.max_rerank_requests
        self.max_embedding_requests = limits.max_embedding_requests
        self.context = None
        self.lore_mode = False
        self.delivered_lore = {}

    def check(self) -> None:
        if asyncio.get_running_loop().time() >= self.deadline:
            raise RunStopped("TURN_TIMEOUT")

    async def call_model(self, model, messages, *, options: dict, kind: str, step_id: int, **metadata):
        """冻结这次调用的输入；真实适配器与日志共用同一份默认模型参数。"""
        messages, options = deepcopy(messages), deepcopy(options)
        if self.lore_mode and kind == 'model':
            from app.lore_tools import pack_lore
            messages, self.delivered_lore = pack_lore(
                messages, options.get('tools', []), self.limits.max_input_chars)
            self.trace.emit('lore_delivery', 'selected', step_id=step_id,
                            lore_refs=list(self.delivered_lore.values()))
        if self.limits.max_input_chars is not None:
            from app.memory import input_chars
            if self.context is not None and kind == "model":
                messages, report = self.context.pack(messages, options.get("tools", []), self.limits.max_input_chars)
            else:
                count = input_chars(messages, options.get("tools", []))
                report = {"input_chars": count, "max_input_chars": self.limits.max_input_chars,
                          "unit": "unicode_characters", "counted_fields": ["messages", "tools"]}
                if count > self.limits.max_input_chars:
                    raise RunStopped("CONTEXT_BUDGET_EXCEEDED")
            self.trace.emit("context", "validated", step_id=step_id, **report)
        model_input = {**deepcopy(getattr(model, "request_defaults", {})), "messages": messages, **options}
        return await self.call(
            lambda: model.complete(deepcopy(messages), **deepcopy(options)),
            kind=kind, step_id=step_id, model_input=model_input,
            request_options={"tool_choice": options.get("tool_choice"),
                             "response_format": options.get("response_format", {"type": "text"}).get("type"),
                             "tool_schema_count": len(options.get("tools", []))}, **metadata)

    async def call(self, operation, *, kind: str, step_id: int, semaphore=None,
                   model_input: dict | None = None, **metadata):
        """operation 是创建新协程的函数；每个 attempt 才创建，避免泄漏未等待协程。"""
        logical_span = str(uuid4())
        is_model = kind in ("model", "narration")
        is_external = is_model or kind in ('embedding', 'rerank')
        timeout_s = (self.limits.model_attempt_timeout_s if is_model
                     else self.limits.query_attempt_timeout_s)
        if kind in ('rerank', 'local_rerank'):
            timeout_s = self.limits.rerank_timeout_s
        attempts = 1 if kind in ('rerank', 'local_rerank') else self.limits.max_attempts
        for attempt in range(1, attempts + 1):
            delay_s = self.limits.retry_delay_s
            self.check()
            if is_external and self.model_requests >= self.limits.max_model_requests:
                raise RunStopped("MODEL_REQUEST_LIMIT")
            if kind == 'embedding' and self.embedding_requests >= self.max_embedding_requests:
                raise RunStopped('EMBEDDING_REQUEST_LIMIT')
            if kind == 'rerank' and self.rerank_requests >= self.max_rerank_requests:
                raise RunStopped('RERANK_REQUEST_LIMIT')
            if is_external and self.shared is not None:
                self.shared.check_capacity()
            started = monotonic()
            fields = dict(step_id=step_id, span_id=logical_span, parent_span_id=self.trace.run_id,
                          attempt=attempt, started_at=datetime.now(timezone.utc).isoformat(), **metadata)
            status, error_code, usage = "ok", None, None
            acquired = False
            turn_timeout = None
            io_ref = self.trace.io_reference(logical_span, attempt) if is_model and model_input is not None else None
            io_ok = True
            try:
                # 排队受整轮 deadline 限制，单次调用计时从拿到名额后开始。
                async with asyncio.timeout_at(self.deadline) as turn_timeout:
                    if semaphore is not None:
                        await semaphore.acquire()
                        acquired = True
                    self.check()
                    async with asyncio.timeout(timeout_s):
                        if io_ref is not None:
                            fields["io_ref"] = io_ref
                            io_ok = self.trace.write_io(io_ref, "input", model_input, kind=kind, **fields)
                            self.trace.emit(kind + "_request", "prepared", **fields,
                                            io_write_failed=not io_ok, model_requests=self.model_requests)
                        self.check()  # 本地写日志也计入原 deadline。
                        if is_external:
                            if self.model_requests >= self.limits.max_model_requests:
                                raise RunStopped('MODEL_REQUEST_LIMIT')
                            if kind == 'embedding' and self.embedding_requests >= self.max_embedding_requests:
                                raise RunStopped('EMBEDDING_REQUEST_LIMIT')
                            if kind == 'rerank' and self.rerank_requests >= self.max_rerank_requests:
                                raise RunStopped('RERANK_REQUEST_LIMIT')
                            if self.shared is not None:
                                self.shared.claim(kind)
                            self.model_requests += 1
                            if kind == 'embedding':
                                self.embedding_requests += 1
                            elif kind == 'rerank':
                                self.rerank_requests += 1
                            else:
                                self.chat_requests += 1
                        value = await operation()
                        # 在预算检查、协议校验及 JSON 解析前保留原文，包括格式不合法的回答。
                        if io_ref is not None:
                            output = ({"format": "provider_completion" if "raw_response" in value else "adapter_completion",
                                       "response": value.get("raw_response", value),
                                       "provider_request_id": value.get("provider_request_id")}
                                      if isinstance(value, dict) else {"response": value})
                            saved = self.trace.write_io(io_ref, "output", output, kind=kind, **fields)
                            io_ok = io_ok and saved
                    self.check()  # 同步代码也可能花时间；返回后再次检查。
                if is_model:
                    fields["response_details"] = safe_response_details(value)
                    usage = safe_usage(value.get("usage")) if isinstance(value, dict) else None
                return value
            except asyncio.CancelledError:
                status, error_code = "cancelled", "CANCELLED"
                raise
            except (TimeoutError, TransientFailure) as error:
                if isinstance(error, TransientFailure):
                    delay_s = max(delay_s, error.retry_after_s)
                error_code = "ATTEMPT_TIMEOUT" if isinstance(error, TimeoutError) else "TRANSIENT_FAILURE"
                status = "error"
                # 事件循环可按时钟精度略提前触发定时器；以 timeout 的实际状态为准。
                if (turn_timeout is not None and turn_timeout.expired()) or asyncio.get_running_loop().time() >= self.deadline:
                    error_code = "TURN_TIMEOUT"
                    raise RunStopped(error_code) from None
                if attempt == attempts:
                    raise CallFailure(error_code) from None
            except RunStopped as error:
                status, error_code = "error", error.code
                raise
            except Exception:
                status, error_code = "error", "MODEL_ERROR" if is_model else "QUERY_ERROR"
                raise CallFailure(error_code) from None
            finally:
                if acquired:
                    semaphore.release()
                if io_ref is not None and "io_ref" in fields:
                    saved = self.trace.write_io(io_ref, "end", {"status": status, "error_code": error_code},
                                                kind=kind, **fields)
                    fields["io_write_failed"] = not (io_ok and saved)
                self.trace.emit(kind, status, **fields, error_code=error_code, usage=usage,
                                model_requests=self.model_requests,
                                duration_ms=round((monotonic() - started) * 1000, 3))
            # 退避期间已释放名额；总 deadline 不会因重试而重置。
            if is_external and self.model_requests >= self.limits.max_model_requests:
                raise RunStopped("MODEL_REQUEST_LIMIT")
            self.check()
            if asyncio.get_running_loop().time() + delay_s >= self.deadline:
                raise RunStopped("RETRY_BUDGET_EXHAUSTED")
            self.trace.emit("retry_wait", "waiting", **fields)
            async with asyncio.timeout_at(self.deadline):
                await asyncio.sleep(delay_s)
