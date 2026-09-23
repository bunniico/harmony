"""Thin wrapper over the Anthropic SDK: retries, caching, usage stats."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import anthropic

log = logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 6


class AIUnavailable(Exception):
    """The model call failed; the caller replies with an in-character fallback."""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def add(self, u) -> None:
        self.calls += 1
        self.input_tokens += u.input_tokens or 0
        self.output_tokens += u.output_tokens or 0
        self.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0

    @property
    def cache_hit_rate(self) -> float:
        total = self.input_tokens + self.cache_read + self.cache_write
        return self.cache_read / total if total else 0.0


class AIClient:
    def __init__(self, api_key: str, model: str, max_output_tokens: int):
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2)
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.usage = Usage()

    async def _create(self, **kwargs) -> str:
        resp = await self._send(**kwargs)
        return "".join(b.text for b in resp.content if b.type == "text")

    async def _send(self, **kwargs):
        try:
            resp = await self._client.messages.create(model=self.model, **kwargs)
        except anthropic.RateLimitError as e:
            log.warning("Anthropic rate limited: %s", e)
            raise AIUnavailable from e
        except anthropic.APIConnectionError as e:
            log.warning("Anthropic connection error: %s", e)
            raise AIUnavailable from e
        except anthropic.APIStatusError as e:
            log.error("Anthropic API error %s: %s", e.status_code, e)
            raise AIUnavailable from e
        self.usage.add(resp.usage)
        return resp

    async def chat(self, stable_prompt: str, volatile_context: str, messages: list[dict]) -> str:
        return await self._create(
            max_tokens=self.max_output_tokens,
            system=[
                {"type": "text", "text": stable_prompt, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": volatile_context},
            ],
            messages=messages,
        )

    async def complete(self, system: str, prompt: str, max_tokens: int) -> str:
        """Single-turn helper for background summary and fact-extraction calls."""
        return await self._create(
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )

    async def chat_with_tools(
        self,
        stable_prompt: str,
        volatile_context: str,
        messages: list[dict],
        tools: list[dict],
        run_tool: Callable[[str, dict], Awaitable[tuple[str, bool]]],
    ) -> str:
        """`chat`, but the model may call `tools`. `run_tool(name, input)` returns
        (result text, is_error). After MAX_TOOL_ROUNDS the model must answer in text."""
        system = [
            {"type": "text", "text": stable_prompt, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": volatile_context},
        ]
        messages = list(messages)
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            last = round_no == MAX_TOOL_ROUNDS
            resp = await self._send(
                max_tokens=self.max_output_tokens,
                system=system,
                messages=messages,
                tools=tools,
                tool_choice={"type": "none"} if last else {"type": "auto"},
            )
            calls = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or not calls:
                return "".join(b.text for b in resp.content if b.type == "text")
            messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in resp.content]})
            results = []
            for call in calls:
                text, is_error = await run_tool(call.name, call.input)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": text, "is_error": is_error})
            messages.append({"role": "user", "content": results})
        return ""
