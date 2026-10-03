from __future__ import annotations

import os
from pathlib import Path

from evidence_harness_mutation._tb21_analysis import (
    ArtifactBundle,
    build_artifact_bundle,
    validate_artifact_prefix,
)
from evidence_harness_mutation._tb21_harbor import (
    CollectionSummary,
    ExecutablePreflight,
    collect_harbor,
    preflight_harbor,
)
from evidence_harness_mutation._tb21_manifest import (
    OUTCOME_PATHS,
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

__all__ = ("build", "check", "collect", "freeze", "load", "preflight")


def freeze(project_root: Path) -> ExecutableManifest:
    return freeze_manifest(project_root)


def load(project_root: Path) -> BoundExecutable:
    return load_manifest(project_root)


def preflight(project_root: Path, *, tb21_checkout: Path) -> ExecutablePreflight:
    root = project_root_path(project_root)
    require_preoutcome_state(root, include_executable=False)
    executable = load_manifest(root)
    return preflight_harbor(executable, tb21_checkout=tb21_checkout)


def collect(
    project_root: Path,
    *,
    tb21_checkout: Path,
    env_file: Path,
) -> CollectionSummary:
    root = project_root_path(project_root)
    _require_derived_outputs_absent(root)
    executable = load_manifest(root)
    decision = preflight_harbor(executable, tb21_checkout=tb21_checkout)
    if not decision.ready_for_provider_execution:
        reasons = "; ".join(reason.message for reason in decision.blockers)
        raise ValueError(f"TB2.1 Provider execution is blocked: {reasons}")
    return collect_harbor(
        root,
        executable,
        decision,
        env_file=env_file,
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
        return (f"invalid TB2.1 sensitivity executable: {exc}",)
    raw_root = root / OUTCOME_PATHS[0]
    if not os.path.lexists(raw_root):
        unexpected = tuple(path for path in OUTCOME_PATHS[1:] if os.path.lexists(root / path))
        if unexpected:
            return (
                "TB2.1 derived artifacts exist without the fixed raw root: "
                + ", ".join(unexpected),
            )
        return ()
    try:
        bundle = build_artifact_bundle(root, executable)
    except (OSError, ValueError) as exc:
        return (f"invalid TB2.1 sensitivity collection: {exc}",)
    errors = list(validate_artifact_prefix(root, bundle))
    for entry in bundle.entries:
        if not path_has_git_history(root, entry.path):
            continue
        try:
            commit = first_matching_commit(root, Path(entry.path), entry.data)
            require_git_ancestor(root, executable.executable_commit, commit)
        except ValueError as exc:
            errors.append(f"invalid TB2.1 outcome chronology for {entry.path}: {exc}")
    return tuple(errors)


def _require_derived_outputs_absent(project_root: Path) -> None:
    existing = tuple(path for path in OUTCOME_PATHS[1:] if os.path.lexists(project_root / path))
    if existing:
        raise ValueError("TB2.1 collection must precede derived artifacts: " + ", ".join(existing))
    historical = tuple(
        path for path in OUTCOME_PATHS[1:] if path_has_git_history(project_root, path)
    )
    if historical:
        raise ValueError(
            "TB2.1 derived artifact already exists in Git history: " + ", ".join(historical)
        )
