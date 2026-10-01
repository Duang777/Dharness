import json

from conftest import FakeCompletionIsolation, FakeEnvironment, ScriptedModel
from harbor.models.agent.context import AgentContext

from evidence_harness.collection_profile import PREFIXBENCH_V1
from evidence_harness.harbor_agent import EvidenceHarnessAgent
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CheckKind,
    ProducerAttestation,
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


async def test_profiled_harbor_run_rechecks_and_records_producer(
    tmp_path,
    monkeypatch,
) -> None:
    finish = AgentDecision(
        action=ActionKind.FINISH,
        rationale="the requested state already exists",
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
    producer = ProducerAttestation(
        commit="a" * 40,
        tree="b" * 40,
        source_sha256="c" * 64,
    )
    verified: list[ProducerAttestation] = []

    def verify_producer(_root, expected):
        verified.append(expected)
        return expected

    monkeypatch.setattr(
        "evidence_harness.harbor_agent.LiteLLMModelGateway",
        lambda **_: model,
    )
    monkeypatch.setattr(
        "evidence_harness.harbor_agent.verify_git_runtime_source",
        verify_producer,
    )
    agent = EvidenceHarnessAgent(
        logs_dir=tmp_path,
        model_name="test/model",
        **dict(PREFIXBENCH_V1.controlled_agent_options),
        prefixbench_profile="prefixbench-v1",
        producer_commit=producer.commit,
        producer_tree=producer.tree,
        producer_source_sha256=producer.source_sha256,
    )
    environment = FakeEnvironment()
    monkeypatch.setattr(
        "evidence_harness.harbor_agent.completion_isolation_for_harbor",
        lambda **_: FakeCompletionIsolation(environment),
    )

    await agent.run("Ensure answer.txt is non-empty", environment, AgentContext())

    events = [
        json.loads(line)
        for line in (tmp_path / "evidence-harness" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    started = events[0]["payload"]
    assert verified == [producer]
    assert started["prefixbench_profile"] == "prefixbench-v1"
    assert started["producer"] == producer.model_dump(mode="json")
    assert started["options"] == dict(PREFIXBENCH_V1.controlled_agent_options)
