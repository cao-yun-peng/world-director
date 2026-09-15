"""模型调用集中在这里；上层只依赖 generate(messages)。"""

from typing import Protocol

from openai import OpenAI


class ModelAdapter(Protocol):
    def generate(self, messages: list[dict[str, str]]) -> str:
        ...


class RealModelAdapter:
    """调用支持 OpenAI Chat Completions 格式的模型服务。"""

    def __init__(self, api_key: str, model: str, base_url: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=30.0)
        self.model = model

    def generate(self, messages: list[dict[str, str]]) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
        )
        text = response.choices[0].message.content
        if not text or not text.strip():
            raise ValueError("模型没有返回文本回答。")
        return text.strip()


class FakeModelAdapter:
    def generate(self, messages: list[dict[str, str]]) -> str:
        return "【离线演示】林砚看了你一眼，没有回答。"
