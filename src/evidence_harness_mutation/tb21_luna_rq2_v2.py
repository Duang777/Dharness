from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from pathlib import Path

from evidence_harness_mutation._tb21_luna_analysis_v2 import (
    ArtifactBundle,
    build_artifact_bundle,
    validate_artifact_prefix,
)
from evidence_harness_mutation._tb21_luna_harbor_v2 import (
    CollectionSummary,
    ExecutablePreflight,
    collect_luna_harbor,
    preflight_luna_harbor,
)
from evidence_harness_mutation._tb21_luna_manifest_v2 import (
    BoundExecutable,
    ExecutableManifest,
    first_matching_commit,
    freeze_manifest,
    load_manifest,
    path_has_git_history,
    project_root_path,
    require_git_ancestor,
    require_preoutcome_state,
)
from evidence_harness_mutation.tb21_luna_rq2_protocol_v2 import LUNA_OUTCOME_PATHS

__all__ = ("build", "check", "collect", "freeze", "load", "preflight")


def freeze(project_root: Path) -> ExecutableManifest:
    return freeze_manifest(project_root)


def load(project_root: Path) -> BoundExecutable:
    return load_manifest(project_root)


def preflight(project_root: Path, *, tb21_checkout: Path) -> ExecutablePreflight:
    root = project_root_path(project_root)
    require_preoutcome_state(root, include_executable=False)
    executable = load_manifest(root)
    return preflight_luna_harbor(executable, tb21_checkout=tb21_checkout)


def collect(
    project_root: Path,
    *,
    tb21_checkout: Path,
    environment: Mapping[str, str] | None = None,
) -> CollectionSummary:
    root = project_root_path(project_root)
    _require_derived_outputs_absent(root)
    executable = load_manifest(root)
    decision = preflight_luna_harbor(executable, tb21_checkout=tb21_checkout)
    if not decision.ready_for_provider_execution:
        reasons = "; ".join(reason.message for reason in decision.blockers)
        raise ValueError(f"Luna Provider execution is blocked: {reasons}")
    return collect_luna_harbor(
        root,
        executable,
        decision,
        environment=os.environ if environment is None else environment,
    )


def build(project_root: Path) -> ArtifactBundle:
    root = project_root_path(project_root)
    executable = load_manifest(root)
    return build_artifact_bundle(root, executable)


def check(project_root: Path) -> tuple[str, ...]:
    try:
        root = project_root_path(project_root)
        executable = load_manifest(root)
    except (OSError, ValueError) as exc:
        return (f"invalid Luna RQ2 executable: {exc}",)
    raw_root = root / LUNA_OUTCOME_PATHS[0]
    if not os.path.lexists(raw_root):
        unexpected = tuple(path for path in LUNA_OUTCOME_PATHS[1:] if os.path.lexists(root / path))
        if unexpected:
            return (
                "Luna derived artifacts exist without the fixed raw root: " + ", ".join(unexpected),
            )
        return ()
    try:
        bundle = build_artifact_bundle(root, executable)
    except (OSError, ValueError) as exc:
        return (f"invalid Luna RQ2 collection: {exc}",)
    errors = list(validate_artifact_prefix(root, bundle))
    outcome_commits: set[str] = set()
    for entry in bundle.entries:
        if not path_has_git_history(root, entry.path):
            continue
        try:
            commit = first_matching_commit(root, Path(entry.path), entry.data)
            require_git_ancestor(root, executable.executable_commit, commit)
            outcome_commits.add(commit)
        except ValueError as exc:
            errors.append(f"invalid Luna outcome chronology for {entry.path}: {exc}")
    if len(outcome_commits) > 1:
        errors.append("Luna derived outcomes do not share one first commit")
    return tuple(errors)


def _require_derived_outputs_absent(project_root: Path) -> None:
    existing = tuple(
        path for path in LUNA_OUTCOME_PATHS[1:] if os.path.lexists(project_root / path)
    )
    if existing:
        raise ValueError("Luna collection must precede derived artifacts: " + ", ".join(existing))
    historical = tuple(
        path for path in LUNA_OUTCOME_PATHS[1:] if path_has_git_history(project_root, path)
    )
    if historical:
        raise ValueError(
            "Luna derived artifact already exists in Git history: " + ", ".join(historical)
        )


def _parse_installed_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the installed TB2.1 Luna RQ2 executable.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("preflight", "collect"):
        action = subparsers.add_parser(command)
        action.add_argument("--tb21-checkout", required=True, type=Path)
    return parser.parse_args(argv)


def _installed_main(argv: list[str] | None = None) -> int:
    args = _parse_installed_args(argv)
    project_root = Path.cwd()
    try:
        if args.command == "preflight":
            result = preflight(project_root, tb21_checkout=args.tb21_checkout)
            print(
                "ready_for_provider_execution="
                f"{str(result.ready_for_provider_execution).lower()} "
                f"executable_commit={result.executable_commit}"
            )
            for blocker in result.blockers:
                print(f"blocker={blocker.code}")
                print(f"reason={blocker.message}")
            return 0 if result.ready_for_provider_execution else 1

        summary = collect(project_root, tb21_checkout=args.tb21_checkout)
        print(
            f"status={summary.status} tasks={summary.tasks} "
            f"terminal={summary.terminal} pending={summary.pending} "
            f"interrupted={summary.interrupted}"
        )
        return 0 if summary.status == "complete" else 1
    except (OSError, ValueError) as exc:
        print(f"TB2.1 Luna RQ2 execution failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(_installed_main())
