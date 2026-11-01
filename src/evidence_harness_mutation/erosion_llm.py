from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from evidence_harness_mutation.model import FrozenModel


class LLMEndpointError(RuntimeError):
    pass


class LLMUsage(FrozenModel):
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class LLMReply:
    model: str
    text: str
    usage: LLMUsage
    temperature: float | None


@dataclass(frozen=True, slots=True)
class LLMEndpoint:
    """Anthropic models use the Messages API; every other model uses chat completions."""

    base_url: str
    api_key: str
    timeout_sec: float = 900.0
    attempts: int = 4

    @classmethod
    def from_environment(cls) -> LLMEndpoint:
        try:
            return cls(
                base_url=os.environ["ANTHROPIC_BASE_URL"].rstrip("/"),
                api_key=os.environ["ANTHROPIC_API_KEY"],
            )
        except KeyError as exc:
            raise LLMEndpointError(f"missing environment variable {exc}") from exc

    def complete(
        self,
        model: str,
        prompt: str,
        *,
        max_output_tokens: int,
        temperature: float | None,
        system: str | None = None,
    ) -> LLMReply:
        last_error = ""
        for attempt in range(self.attempts):
            try:
                if model.startswith("claude-"):
                    return self._messages(model, prompt, max_output_tokens, temperature, system)
                return self._chat(model, prompt, max_output_tokens, temperature, system)
            except (httpx.HTTPError, LLMEndpointError, KeyError, IndexError, TypeError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(10 * (attempt + 1))
        raise LLMEndpointError(f"{model}: {last_error}")

    def _messages(
        self,
        model: str,
        prompt: str,
        max_output_tokens: int,
        temperature: float | None,
        system: str | None,
    ) -> LLMReply:
        body: dict[str, object] = {
            "model": model,
            "max_tokens": max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if temperature is not None:
            body["temperature"] = temperature
        if system is not None:
            body["system"] = system
        response = httpx.post(
            f"{self.base_url}/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=self.timeout_sec,
        )
        data = _json_or_raise(response)
        text = "".join(
            block.get("text", "") for block in data["content"] if block.get("type") == "text"
        )
        usage = data["usage"]
        return LLMReply(
            model=str(data.get("model") or model),
            text=text,
            usage=LLMUsage(
                input_tokens=int(usage["input_tokens"]),
                output_tokens=int(usage["output_tokens"]),
            ),
            temperature=temperature,
        )

    def _chat(
        self,
        model: str,
        prompt: str,
        max_output_tokens: int,
        temperature: float | None,
        system: str | None,
    ) -> LLMReply:
        messages = [{"role": "user", "content": prompt}]
        if system is not None:
            messages.insert(0, {"role": "system", "content": system})
        body: dict[str, object] = {
            "model": model,
            "max_completion_tokens": max_output_tokens,
            "messages": messages,
        }
        if temperature is not None:
            body["temperature"] = temperature
        response = httpx.post(
            f"{self.base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
            timeout=self.timeout_sec,
        )
        if (
            response.status_code == 400
            and temperature is not None
            and "temperature" in response.text
        ):
            return self._chat(model, prompt, max_output_tokens, None, system)
        data = _json_or_raise(response)
        text = data["choices"][0]["message"]["content"] or ""
        usage = data["usage"]
        return LLMReply(
            model=str(data.get("model") or model),
            text=text,
            usage=LLMUsage(
                input_tokens=int(usage["prompt_tokens"]),
                output_tokens=int(usage["completion_tokens"]),
            ),
            temperature=temperature,
        )


def _json_or_raise(response: httpx.Response) -> dict[str, Any]:
    if response.status_code != 200:
        raise LLMEndpointError(f"HTTP {response.status_code}: {response.text[:300]}")
    data = response.json()
    if not isinstance(data, dict) or data.get("error"):
        raise LLMEndpointError(f"error body: {str(data)[:300]}")
    return data
