"""一轮共用的预算和外部尝试；重试不增加逻辑 step，但增加真实请求数。"""

import asyncio
import math
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

    def __post_init__(self):
        for name in ("max_steps", "max_parallel_tools", "max_attempts", "max_tool_calls_per_batch"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} 必须是正整数。")
        if type(self.max_model_requests) is not int or self.max_model_requests < 0:
            raise ValueError("max_model_requests 必须是非负整数（0 仍允许重放）。")
        if self.max_tool_calls_per_batch > 2:
            raise ValueError("模型每批最多两个查询；执行器压力实验另走执行器接口。")
        for name in ("turn_timeout_s", "model_attempt_timeout_s", "query_attempt_timeout_s", "retry_delay_s"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0 or (name != "retry_delay_s" and value == 0):
                raise ValueError(f"{name} 必须是有限的有效时间。")


class RunBudget:
    def __init__(self, limits: RunLimits, trace: RunTrace):
        self.limits, self.trace = limits, trace
        self.deadline = asyncio.get_running_loop().time() + limits.turn_timeout_s
        self.model_requests = 0

    def check(self) -> None:
        if asyncio.get_running_loop().time() >= self.deadline:
            raise RunStopped("TURN_TIMEOUT")

    async def call(self, operation, *, kind: str, step_id: int, semaphore=None, **metadata):
        """operation 是创建新协程的函数；每个 attempt 才创建，避免泄漏未等待协程。"""
        logical_span = str(uuid4())
        is_model = kind in ("model", "narration")
        timeout_s = (self.limits.model_attempt_timeout_s if is_model
                     else self.limits.query_attempt_timeout_s)
        for attempt in range(1, self.limits.max_attempts + 1):
            delay_s = self.limits.retry_delay_s
            self.check()
            if is_model and self.model_requests >= self.limits.max_model_requests:
                raise RunStopped("MODEL_REQUEST_LIMIT")
            started = monotonic()
            fields = dict(step_id=step_id, span_id=logical_span, parent_span_id=self.trace.run_id,
                          attempt=attempt, started_at=datetime.now(timezone.utc).isoformat(), **metadata)
            status, error_code, usage = "ok", None, None
            acquired = False
            try:
                # 排队受整轮 deadline 限制，单次调用计时从拿到名额后开始。
                async with asyncio.timeout_at(self.deadline):
                    if semaphore is not None:
                        await semaphore.acquire()
                        acquired = True
                    self.check()
                    async with asyncio.timeout(timeout_s):
                        if is_model:
                            self.model_requests += 1
                        value = await operation()
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
                if asyncio.get_running_loop().time() >= self.deadline:
                    error_code = "TURN_TIMEOUT"
                    raise RunStopped(error_code) from None
                if attempt == self.limits.max_attempts:
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
                self.trace.emit(kind, status, **fields, error_code=error_code, usage=usage,
                                model_requests=self.model_requests,
                                duration_ms=round((monotonic() - started) * 1000, 3))
            # 退避期间已释放名额；总 deadline 不会因重试而重置。
            if is_model and self.model_requests >= self.limits.max_model_requests:
                raise RunStopped("MODEL_REQUEST_LIMIT")
            self.check()
            if asyncio.get_running_loop().time() + delay_s >= self.deadline:
                raise RunStopped("RETRY_BUDGET_EXHAUSTED")
            self.trace.emit("retry_wait", "waiting", **fields)
            async with asyncio.timeout_at(self.deadline):
                await asyncio.sleep(delay_s)
