"""模型调用集中在这里；文本模式使用 generate，工具模式使用 complete。"""

from typing import Any, Protocol
from urllib.parse import urlsplit

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, OpenAI

from app.execution import TransientFailure


class ModelAdapter(Protocol):
    def generate(self, messages: list[dict[str, str]]) -> str:
        ...


class ToolModelAdapter(Protocol):
    def complete(self, messages: list[dict[str, Any]], *,
                 tools: list[dict[str, Any]] | None = None,
                 tool_choice: str | None = None,
                 response_format: dict[str, Any] | None = None) -> dict[str, Any]:
        ...


class RealModelAdapter:
    """调用支持 OpenAI Chat Completions 格式的模型服务。"""

    def __init__(self, api_key: str, model: str, base_url: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=30.0, max_retries=0)
        self.model = model

    def generate(self, messages: list[dict[str, str]]) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=256,
        )
        text = response.choices[0].message.content
        if not text or not text.strip():
            raise ValueError("模型没有返回文本回答。")
        return text.strip()


    def complete(self, messages: list[dict[str, Any]], *,
                 tools: list[dict[str, Any]] | None = None,
                 tool_choice: str | None = None,
                 response_format: dict[str, Any] | None = None) -> dict[str, Any]:
        options = {}
        if tools is not None:
            options["tools"] = tools
        if tool_choice is not None:
            options["tool_choice"] = tool_choice
        if response_format is not None:
            options["response_format"] = response_format
        response = self.client.chat.completions.create(
            model=self.model, messages=messages, max_tokens=512, stream=False,
            extra_body={"enable_thinking": False}, **options,
        )
        if not response.choices:
            raise ValueError("模型没有返回消息。")
        choice = response.choices[0]
        message = {"role": choice.message.role, "content": choice.message.content}
        if choice.message.tool_calls:
            message["tool_calls"] = [call.model_dump(include={"id", "type", "function"})
                                     for call in choice.message.tool_calls]
        return {"message": message, "finish_reason": choice.finish_reason,
                "usage": response.usage.model_dump() if response.usage else None}


class FakeModelAdapter:
    def generate(self, messages: list[dict[str, str]]) -> str:
        return "【离线演示】林砚看了你一眼，没有回答。"


class AsyncToolModelAdapter(Protocol):
    mode: str

    async def complete(self, messages: list[dict[str, Any]], **options) -> dict[str, Any]:
        ...


class AsyncRealModelAdapter:
    """原生异步 I/O；SDK 重试关闭，外层统一计数与截止时间。"""

    mode = "real"

    def __init__(self, api_key: str, model: str, base_url: str):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=10.0, max_retries=0)
        self.model = model
        self.request_count = 0
        self.provider_host = urlsplit(base_url).hostname

    async def complete(self, messages: list[dict[str, Any]], **options) -> dict[str, Any]:
        self.request_count += 1
        try:
            response = await self.client.chat.completions.create(
                model=self.model, messages=messages, max_tokens=512, stream=False,
                extra_body={"enable_thinking": False}, **options,
            )
        except APIConnectionError:
            raise TransientFailure() from None
        except APIStatusError as error:
            if error.status_code in (429, 500, 502, 503, 504):
                from email.utils import parsedate_to_datetime
                from datetime import datetime, timezone

                header = error.response.headers.get("retry-after", "0")
                try:
                    delay = float(header)
                except ValueError:
                    try:
                        delay = (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        delay = 0.0
                raise TransientFailure(retry_after_s=max(0.0, delay)) from None
            raise
        if not response.choices:
            raise ValueError("模型没有返回消息。")
        choice = response.choices[0]
        message = {"role": choice.message.role, "content": choice.message.content}
        if choice.message.tool_calls:
            message["tool_calls"] = [call.model_dump(include={"id", "type", "function"})
                                     for call in choice.message.tool_calls]
        return {"message": message, "finish_reason": choice.finish_reason,
                "usage": response.usage.model_dump() if response.usage else None}

    async def aclose(self) -> None:
        await self.client.close()
