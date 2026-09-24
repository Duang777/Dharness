import pytest

from evidence_harness.policy import PolicyViolation, find_repeated_cycle, validate_check
from evidence_harness.protocol import (
    CheckKind,
    CommandMode,
    CommandReceipt,
    FailureKind,
    OutputExcerpt,
    VerificationCheck,
)


def _receipt(sequence: int, command_hash: str, observation_hash: str) -> CommandReceipt:
    empty = OutputExcerpt(
        head="",
        tail="",
        total_bytes=0,
        omitted_bytes=0,
        sha256="0" * 64,
    )
    return CommandReceipt(
        sequence=sequence,
        command_id=f"command-{sequence}",
        script="status",
        purpose="inspect",
        cwd="/app",
        mode=CommandMode.OBSERVE,
        work_epoch=0,
        return_code=0,
        failure=None,
        duration_sec=0,
        stdout=empty,
        stderr=empty,
        command_fingerprint=command_hash,
        observation_fingerprint=observation_hash,
    )


def test_verification_blocks_benchmark_verifier_path() -> None:
    check = VerificationCheck(
        id="hidden",
        kind=CheckKind.BEHAVIOR,
        script="pytest -q /tests/test_task.py",
        proves="hidden verifier passes",
    )

    with pytest.raises(PolicyViolation, match="benchmark"):
        validate_check(check, 300)


def test_verification_blocks_benchmark_verifier_cwd() -> None:
    check = VerificationCheck(
        id="hidden-cwd",
        kind=CheckKind.BEHAVIOR,
        script="pytest -q test_task.py",
        cwd="/tests",
        proves="hidden verifier passes",
    )

    with pytest.raises(PolicyViolation, match="benchmark"):
        validate_check(check, 300)


def test_verification_allows_project_owned_tests() -> None:
    check = VerificationCheck(
        id="project-tests",
        kind=CheckKind.BEHAVIOR,
        script="pytest -q /app/tests/test_feature.py",
        proves="project behavior passes",
    )

    validate_check(check, 300)


@pytest.mark.parametrize("script", ["true", "echo success", "ls -la", "cat result.txt"])
def test_verification_rejects_display_only_checks(script: str) -> None:
    check = VerificationCheck(
        id="weak",
        kind=CheckKind.ARTIFACT,
        script=script,
        proves="claims completion",
    )

    with pytest.raises(PolicyViolation, match=r"display-only|no-op"):
        validate_check(check, 300)


@pytest.mark.parametrize(
    "script",
    [
        "git merge-base --is-ancestor deadbeef master",
        "if grep -nE '^(<<<<<<<|=======|>>>>>>>)' a.txt; then exit 1; fi",
    ],
)
def test_verification_allows_read_only_tokens_that_resemble_mutations(
    script: str,
) -> None:
    check = VerificationCheck(
        id="read-only",
        kind=CheckKind.BEHAVIOR,
        script=script,
        proves="repository state is correct",
    )

    validate_check(check, 300)


@pytest.mark.parametrize(
    "script",
    [
        "rm -f output.txt",
        "printf result > output.txt",
        "sed -i 's/old/new/' file.txt",
        "git merge recovered-change",
        "grep result source.txt | tee output.txt",
        "bash -c -- 'true; rm -f output.txt'",
    ],
)
def test_verification_rejects_mutating_shell_commands(script: str) -> None:
    check = VerificationCheck(
        id="mutating",
        kind=CheckKind.BEHAVIOR,
        script=script,
        proves="repository state is correct",
    )

    with pytest.raises(PolicyViolation, match="modify task state"):
        validate_check(check, 300)


def test_cycle_detector_finds_repeated_two_command_cycle() -> None:
    receipts = [
        _receipt(index + 1, *pair)
        for index, pair in enumerate(
            [
                ("a", "1"),
                ("b", "2"),
                ("a", "1"),
                ("b", "2"),
                ("a", "1"),
                ("b", "2"),
            ]
        )
    ]

    assert find_repeated_cycle(receipts) == ("a:1", "b:2")


def test_nonzero_failure_is_not_success() -> None:
    receipt = _receipt(1, "a", "1").model_copy(
        update={"return_code": 2, "failure": FailureKind.NONZERO}
    )

    assert receipt.succeeded is False
