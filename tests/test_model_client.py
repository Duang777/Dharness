from types import SimpleNamespace
from unittest.mock import AsyncMock

from evidence_harness.journal import RunJournal
from evidence_harness.model_client import LiteLLMModelGateway


def _response(content: str):
    usage = SimpleNamespace(
        prompt_tokens=120,
        completion_tokens=30,
        prompt_tokens_details=SimpleNamespace(cached_tokens=20),
    )
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


async def test_gateway_parses_decision_and_tracks_usage(tmp_path, monkeypatch) -> None:
    completion = AsyncMock(
        return_value=_response(
            """
            {
              "action": "replan",
              "rationale": "the first assumption was wrong",
              "plan": ["inspect the actual format"]
            }
            """
        )
    )
    monkeypatch.setattr("litellm.acompletion", completion)
    monkeypatch.setattr("litellm.get_supported_openai_params", lambda **_: [])
    monkeypatch.setattr("litellm.completion_cost", lambda **_: 0.125)
    gateway = LiteLLMModelGateway(
        model_name="test/model",
        journal=RunJournal(tmp_path, inline_bytes=256),
    )

    decision = await gateway.decide("prompt")

    assert decision.action == "replan"
    assert gateway.usage.input_tokens == 120
    assert gateway.usage.cache_tokens == 20
    assert gateway.usage.output_tokens == 30
    assert gateway.usage.cost_usd == 0.125
    assert gateway.usage.model_calls == 1


async def test_gateway_uses_one_schema_repair_call(tmp_path, monkeypatch) -> None:
    completion = AsyncMock(
        side_effect=[
            _response("not json"),
            _response(
                """
                {
                  "action": "stop",
                  "rationale": "required input is unavailable",
                  "summary": "blocked",
                  "stop_category": "blocked"
                }
                """
            ),
        ]
    )
    monkeypatch.setattr("litellm.acompletion", completion)
    monkeypatch.setattr("litellm.get_supported_openai_params", lambda **_: [])
    monkeypatch.setattr("litellm.completion_cost", lambda **_: 0)
    gateway = LiteLLMModelGateway(
        model_name="test/model",
        journal=RunJournal(tmp_path, inline_bytes=256),
    )

    decision = await gateway.decide("prompt")

    assert decision.action == "stop"
    assert completion.await_count == 2
    assert "VALIDATION ERROR" in completion.await_args_list[1].kwargs["messages"][0]["content"]


async def test_gateway_repairs_empty_content_once(tmp_path, monkeypatch) -> None:
    completion = AsyncMock(
        side_effect=[
            _response(""),
            _response(
                """
                {
                  "action": "stop",
                  "rationale": "required input is unavailable",
                  "summary": "blocked",
                  "stop_category": "blocked"
                }
                """
            ),
        ]
    )
    monkeypatch.setattr("litellm.acompletion", completion)
    monkeypatch.setattr("litellm.get_supported_openai_params", lambda **_: [])
    monkeypatch.setattr("litellm.completion_cost", lambda **_: 0)
    gateway = LiteLLMModelGateway(
        model_name="test/model",
        journal=RunJournal(tmp_path, inline_bytes=256),
    )

    decision = await gateway.decide("prompt")

    assert decision.action == "stop"
    assert completion.await_count == 2
    repair_prompt = completion.await_args_list[1].kwargs["messages"][0]["content"]
    assert "model returned empty content" in repair_prompt
