import json

from evidence_harness.prompting import build_executor_prompt, build_review_prompt
from evidence_harness.protocol import (
    CommandMode,
    CommandReceipt,
    LoopOptions,
    OutputExcerpt,
    RunPhase,
    RunState,
)


def _large_receipt(sequence: int) -> CommandReceipt:
    output = OutputExcerpt(
        head="x" * 12_000,
        tail="y" * 4_000,
        total_bytes=20_000,
        omitted_bytes=4_000,
        sha256=f"{sequence:064x}",
    )
    return CommandReceipt(
        sequence=sequence,
        command_id=f"inspect-{sequence}",
        script=f"inspect {sequence}",
        purpose="collect evidence",
        cwd="/app",
        mode=CommandMode.OBSERVE,
        work_epoch=0,
        return_code=0,
        duration_sec=0.1,
        stdout=output,
        stderr=output.model_copy(update={"head": "", "tail": "", "total_bytes": 0}),
        command_fingerprint=f"{sequence:064x}",
        observation_fingerprint=f"{sequence + 100:064x}",
    )


def test_compaction_preserves_task_and_complete_schema() -> None:
    instruction = "Produce result.txt with the exact requested contents."
    observations = [
        _large_receipt(index).model_copy(
            update={
                "mode": CommandMode.CHANGE,
                "script": f"mutate-{index} " + ("z" * 19_000),
            }
        )
        for index in range(1, 15)
    ]
    state = RunState(
        instruction=instruction,
        phase=RunPhase.THINKING,
        started_monotonic=0,
        deadline_monotonic=1_000,
        observations=observations,
    )
    options = LoopOptions(context_max_chars=10_000, output_inline_bytes=12_000)

    prompt = build_executor_prompt(state, options, now=10)
    state_text, schema_text = prompt.split("OUTPUT JSON SCHEMA\n", maxsplit=1)
    payload_text = state_text.split("CURRENT RUN STATE\n", maxsplit=1)[1].strip()
    payload = json.loads(payload_text)

    assert instruction in prompt
    assert json.loads(schema_text)["title"] == "AgentDecision"
    assert len(prompt) < 25_000
    assert payload["budget"]["max_model_call_timeout_sec"] == 360
    assert payload["budget"]["max_command_timeout_sec"] == 300
    assert all("command" not in item for item in payload["known_changes"])
    assert all("command_sha256" in item for item in payload["known_changes"])
    assert "z" * 3_000 not in prompt
    assert "authorized evaluation inside the current disposable sandbox" in prompt
    assert "allows choosing among approaches" in prompt
    assert "compare at least two materially different candidates" in prompt
    assert "Do not violate a task-mandated algorithm" in prompt
    assert "from their saved bytes" in prompt
    assert "consumer's conventions" in prompt


def test_completion_reviewer_uses_feasible_evidence_standard() -> None:
    prompt = build_review_prompt(
        instruction="Make the query as efficient as possible.",
        checks=(),
        coverage=(),
        verification_receipts=(),
        supporting_observations=[],
    )

    assert "Do not demand proof of a global" in prompt
    assert "allows choosing among approaches" in prompt
    assert "task-mandated algorithm" in prompt
    assert "One full benchmark is enough" in prompt
    assert "Do not require the finish checks to repeat" in prompt
    assert "complete saved artifact" in prompt
    assert "downstream consumer will extract" in prompt
    assert "executed completion receipts" in prompt
