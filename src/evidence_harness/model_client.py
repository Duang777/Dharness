from __future__ import annotations

import asyncio
import json
from typing import TypeVar

import litellm
from litellm.exceptions import AuthenticationError, BadRequestError, NotFoundError
from pydantic import BaseModel, ValidationError

from evidence_harness.journal import RunJournal
from evidence_harness.protocol import AgentDecision, ReviewDecision, UsageTotals

ResponseT = TypeVar("ResponseT", bound=BaseModel)


class ModelProtocolError(RuntimeError):
    pass


class ModelServiceError(RuntimeError):
    pass


class LiteLLMModelGateway:
    def __init__(
        self,
        *,
        model_name: str,
        journal: RunJournal,
        api_key: str | None = None,
        api_base: str | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        max_output_tokens: int = 8_192,
        transport_attempts: int = 3,
    ) -> None:
        self._model_name = model_name
        self._journal = journal
        self._api_key = api_key
        self._api_base = api_base
        self._temperature = temperature
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens
        self._transport_attempts = transport_attempts
        self._usage = UsageTotals()

    @property
    def usage(self) -> UsageTotals:
        return self._usage.model_copy()

    async def decide(self, prompt: str) -> AgentDecision:
        return await self._call(prompt, AgentDecision, role="executor")

    async def review(self, prompt: str) -> ReviewDecision:
        return await self._call(prompt, ReviewDecision, role="reviewer")

    async def _call(
        self,
        prompt: str,
        response_type: type[ResponseT],
        *,
        role: str,
    ) -> ResponseT:
        repair_prompt: str | None = None
        for schema_attempt in range(2):
            raw = ""
            try:
                raw = await self._request(repair_prompt or prompt)
                parsed = response_type.model_validate_json(_strict_json(raw))
            except (ModelProtocolError, ValueError, ValidationError) as exc:
                if schema_attempt:
                    raise ModelProtocolError(
                        f"{role} returned invalid structured output after repair: {exc}"
                    ) from exc
                repair_prompt = (
                    f"{prompt}\n\n"
                    "Your previous response did not validate. Return only one corrected JSON "
                    "object. Do not add Markdown or explanation outside the object.\n"
                    f"VALIDATION ERROR\n{str(exc)[:2_000]}\n"
                    f"INVALID RESPONSE\n{raw[:6_000] or '<empty>'}"
                )
                continue

            self._journal.append(
                "model_decision",
                {
                    "role": role,
                    "schema_repair": bool(schema_attempt),
                    "decision": parsed.model_dump(mode="json"),
                    "usage": self._usage.model_dump(mode="json"),
                },
            )
            return parsed
        raise AssertionError("schema repair loop exited unexpectedly")

    async def _request(self, prompt: str) -> str:
        kwargs: dict[str, object] = {
            "model": self._model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self._max_output_tokens,
        }
        if self._api_base is not None:
            kwargs["api_base"] = self._api_base
        if self._api_key is not None:
            kwargs["api_key"] = self._api_key
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        if self._reasoning_effort is not None:
            kwargs["reasoning_effort"] = self._reasoning_effort

        try:
            supported = litellm.get_supported_openai_params(model=self._model_name) or []
        except Exception:
            supported = []
        if "response_format" in supported:
            kwargs["response_format"] = {"type": "json_object"}

        for attempt in range(self._transport_attempts):
            try:
                response = await litellm.acompletion(**kwargs)
                self._accumulate_usage(response)
                content = response.choices[0].message.content
                if not isinstance(content, str) or not content.strip():
                    raise ModelProtocolError("model returned empty content")
                return content
            except (AuthenticationError, BadRequestError, NotFoundError):
                raise
            except ModelProtocolError:
                raise
            except Exception as exc:
                if attempt + 1 >= self._transport_attempts:
                    raise ModelServiceError(
                        f"model request failed after {self._transport_attempts} attempts: {exc}"
                    ) from exc
                await asyncio.sleep(min(2**attempt, 4))
        raise AssertionError("transport retry loop exited unexpectedly")

    def _accumulate_usage(self, response: object) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            self._usage.model_calls += 1
            return

        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        details = getattr(usage, "prompt_tokens_details", None)
        cache_tokens = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
        try:
            cost = float(litellm.completion_cost(completion_response=response) or 0)
        except Exception:
            cost = 0.0

        self._usage.input_tokens += prompt_tokens
        self._usage.cache_tokens += cache_tokens
        self._usage.output_tokens += completion_tokens
        self._usage.cost_usd += cost
        self._usage.model_calls += 1


def _strict_json(raw: str) -> str:
    stripped = raw.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        raise ValueError("response must contain one JSON object and no surrounding prose")
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("response must be a JSON object")
    return stripped
