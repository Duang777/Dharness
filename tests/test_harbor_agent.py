from conftest import FakeCompletionIsolation, FakeEnvironment, ScriptedModel
from harbor.models.agent.context import AgentContext

from evidence_harness.harbor_agent import EvidenceHarnessAgent
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CheckKind,
    RequirementCoverage,
    ReviewDecision,
    VerificationCheck,
)


async def test_harbor_adapter_populates_context(tmp_path, monkeypatch) -> None:
    finish = AgentDecision(
        action=ActionKind.FINISH,
        rationale="the requested state already exists",
        summary="verified answer.txt",
        checks=(
            VerificationCheck(
                id="check-answer",
                kind=CheckKind.ARTIFACT,
                script="test -s answer.txt",
                proves="answer.txt is non-empty",
            ),
        ),
        coverage=(
            RequirementCoverage(
                requirement="answer.txt is non-empty",
                check_ids=("check-answer",),
            ),
        ),
    )
    model = ScriptedModel(
        [finish],
        [ReviewDecision(verdict="accept", rationale="the check is sufficient")],
    )
    monkeypatch.setattr(
        "evidence_harness.harbor_agent.LiteLLMModelGateway",
        lambda **_: model,
    )
    agent = EvidenceHarnessAgent(
        logs_dir=tmp_path,
        model_name="test/model",
        max_environment_calls=8,
    )
    environment = FakeEnvironment()
    isolation = FakeCompletionIsolation(environment)
    monkeypatch.setattr(
        "evidence_harness.harbor_agent.completion_isolation_for_harbor",
        lambda **_: isolation,
    )
    context = AgentContext()

    await agent.setup(environment)
    await agent.run("Ensure answer.txt is non-empty", environment, context)

    assert context.n_input_tokens == 0
    assert context.model_usage is not None
    assert context.metadata is not None
    assert context.metadata["evidence_harness"]["stop_reason"] == "verified"
    assert (tmp_path / "evidence-harness" / "events.jsonl").is_file()
