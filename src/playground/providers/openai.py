"""OpenAI provider implementation.

Supports GPT-5.4 (and any other OpenAI chat model) with tool calling.
Registers itself as 'openai' in the provider registry at import time.
"""

from __future__ import annotations

import json
from typing import Iterator

from tenacity import retry, stop_after_attempt, wait_exponential

from playground.core.exceptions import ProviderError
from playground.providers.base import LLMProvider, LLMResponse, ToolCall
from playground.providers import registry


class OpenAIProvider:
    """OpenAI chat completions with tool calling support."""

    def __init__(self, model: str, api_key: str) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError("openai package not installed. Run: pip install openai") from exc

        self._model = model
        self._client = OpenAI(api_key=api_key)

    @property
    def model_id(self) -> str:
        return self._model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def complete(self, messages: list[dict]) -> LLMResponse:
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
            choice = resp.choices[0]
            return LLMResponse(
                content=choice.message.content or "",
                finish_reason=choice.finish_reason,
                raw=resp,
            )
        except Exception as exc:
            raise ProviderError(f"OpenAI complete failed: {exc}") from exc

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def complete_with_tools(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )
            choice = resp.choices[0]
            msg = choice.message

            tool_calls: list[ToolCall] = []
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls.append(
                        ToolCall(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=json.loads(tc.function.arguments),
                        )
                    )

            return LLMResponse(
                content=msg.content or "",
                finish_reason=choice.finish_reason,
                tool_calls=tool_calls,
                raw=resp,
            )
        except Exception as exc:
            raise ProviderError(f"OpenAI complete_with_tools failed: {exc}") from exc

    def stream_complete(self, messages: list[dict]) -> Iterator[str]:
        try:
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        except Exception as exc:
            raise ProviderError(f"OpenAI stream_complete failed: {exc}") from exc


# Self-register
def _factory(model: str, api_key: str, **_: object) -> LLMProvider:
    return OpenAIProvider(model=model, api_key=api_key)


registry.register("openai", _factory)
