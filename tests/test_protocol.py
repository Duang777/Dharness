import pytest
from pydantic import ValidationError

from evidence_harness.model_client import _strict_json
from evidence_harness.protocol import AgentDecision


def test_execute_requires_a_command() -> None:
    with pytest.raises(ValidationError, match="execute requires at least one command"):
        AgentDecision.model_validate(
            {
                "action": "execute",
                "rationale": "inspect first",
                "plan": ["inspect"],
            }
        )


def test_finish_coverage_must_reference_known_check() -> None:
    with pytest.raises(ValidationError, match="coverage references unknown checks"):
        AgentDecision.model_validate(
            {
                "action": "finish",
                "rationale": "done",
                "checks": [
                    {
                        "id": "actual",
                        "kind": "artifact",
                        "script": "test -s result.txt",
                        "proves": "result exists",
                    }
                ],
                "coverage": [
                    {
                        "requirement": "write the result",
                        "check_ids": ["missing"],
                    }
                ],
            }
        )


def test_strict_json_rejects_markdown_fences() -> None:
    with pytest.raises(ValueError, match="no surrounding prose"):
        _strict_json('```json\n{"action":"stop"}\n```')
