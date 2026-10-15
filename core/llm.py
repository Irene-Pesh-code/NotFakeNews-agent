"""Клиент языковой модели через OpenAI-совместимый API (OrcaRouter, DeepSeek и др.)."""
from __future__ import annotations

import json
import re

from openai import AsyncOpenAI, OpenAIError


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key,
                                  timeout=timeout, max_retries=2)
        self.model = model

    async def chat(self, messages: list[dict], tools: list[dict] | None = None,
                   temperature: float = 0.1):
        kwargs = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            resp = await self.client.chat.completions.create(**kwargs)
        except OpenAIError as e:
            raise LLMError(f"{e.__class__.__name__}: {e}") from e
        if not resp.choices:
            raise LLMError("пустой ответ модели")
        return resp.choices[0].message


def parse_json(text: str | None) -> dict:
    """Достаёт JSON-объект из ответа модели (даже если он обёрнут в ```json)."""
    if not text:
        raise ValueError("пустой ответ")
    t = re.sub(r"```(?:json)?", "", text).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        raise ValueError("в ответе нет JSON")
    return json.loads(t[i:j + 1])
