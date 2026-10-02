from __future__ import annotations

import importlib
import json
import os
import shlex
import subprocess
import sys
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evidence_harness_mutation.main_analysis_transfer import (
    WorkspaceAttestation,
    adapt_miniswe_trajectory,
)

MINISWE_COMMIT: Literal["04d809ceab9df28f9adaed044884180159172930"] = (
    "04d809ceab9df28f9adaed044884180159172930"
)
MINISWE_TREE: Literal["05aef37afee93a52cea7d571b00d435f28c81bd6"] = (
    "05aef37afee93a52cea7d571b00d435f28c81bd6"
)
PROGRAMBENCH_COMMIT: Literal["963063c9271cc40fa179977356782ea4582e0b0c"] = (
    "963063c9271cc40fa179977356782ea4582e0b0c"
)
PROGRAMBENCH_TREE: Literal["0662e455d08e8c6e8d326a615d0cd4c6e448cf72"] = (
    "0662e455d08e8c6e8d326a615d0cd4c6e448cf72"
)
TRANSFER_MODEL: Literal["openai/modelhub/gpt-5.6-terra"] = "openai/modelhub/gpt-5.6-terra"
TRANSFER_RUN_ROOT = Path("runs/programbench/miniswe-transfer-v1")

_DIGEST_PREFIX = "TRANSFER_WORKSPACE_SHA256="
_WORKSPACE_DIGEST_PROGRAM = r"""
import hashlib
import os
import stat

root = os.fsencode("/workspace")
digest = hashlib.sha256()

def frame(value):
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)

for directory, names, files in os.walk(root, topdown=True, followlinks=False):
    entries = sorted((*names, *files))
    names[:] = sorted(names)
    for name in entries:
        path = os.path.join(directory, name)
        relative = os.path.relpath(path, root)
        metadata = os.lstat(path)
        frame(relative)
        frame(metadata.st_mode.to_bytes(8, "big"))
        if stat.S_ISLNK(metadata.st_mode):
            frame(b"symlink")
            frame(os.readlink(path))
        elif stat.S_ISREG(metadata.st_mode):
            frame(b"file")
            with open(path, "rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    frame(chunk)
        elif stat.S_ISDIR(metadata.st_mode):
            frame(b"directory")
        else:
            frame(b"other")

print("TRANSFER_WORKSPACE_SHA256=" + digest.hexdigest())
""".strip()
_WORKSPACE_DIGEST_COMMAND = f"python3 -c {shlex.quote(_WORKSPACE_DIGEST_PROGRAM)}"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CheckoutIdentity(_FrozenModel):
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    tree: str = Field(pattern=r"^[0-9a-f]{40}$")


class TransferRuntimePreflight(_FrozenModel):
    schema_version: Literal[1] = 1
    mini_swe: CheckoutIdentity
    programbench: CheckoutIdentity
    model: Literal["openai/modelhub/gpt-5.6-terra"] = TRANSFER_MODEL
    tasks: int = Field(ge=1)
    ready: Literal[True] = True


class TransferCollectionSummary(_FrozenModel):
    schema_version: Literal[1] = 1
    tasks: int = Field(ge=1)
    collected: int = Field(ge=0)
    resumed: int = Field(ge=0)
    failed: int = Field(ge=0)
    task_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.collected + self.resumed + self.failed != self.tasks:
            raise ValueError("transfer collection counts do not partition tasks")
        if self.tasks != len(self.task_ids):
            raise ValueError("transfer collection task count is stale")
        return self


class TransferEnvironment(Protocol):
    config: Any

    def execute(
        self,
        action: dict[str, Any],
        cwd: str = "",
        *,
        timeout: int | None = None,
    ) -> dict[str, Any]: ...

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]: ...

    def serialize(self) -> dict[str, Any]: ...


class AttestedWorkspaceEnvironment:
    def __init__(self, environment: TransferEnvironment) -> None:
        self._environment = environment
        self.config = environment.config
        self._action_ordinal = 0
        self._finished = False
        self._attestations: list[WorkspaceAttestation] = []

    @property
    def attestations(self) -> tuple[WorkspaceAttestation, ...]:
        return tuple(self._attestations)

    @property
    def inner(self) -> TransferEnvironment:
        return self._environment

    def begin(self) -> WorkspaceAttestation:
        if self._attestations:
            raise ValueError("workspace attestation has already started")
        return self._append("initial")

    def execute(
        self,
        action: dict[str, Any],
        cwd: str = "",
        *,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        if not self._attestations or self._finished:
            raise ValueError("workspace environment is outside its collection lifecycle")
        self._action_ordinal += 1
        try:
            result = self._environment.execute(action, cwd, timeout=timeout)
        except BaseException:
            self._append("after_action", action_ordinal=self._action_ordinal)
            raise
        self._append("after_action", action_ordinal=self._action_ordinal)
        return result

    def finish(self) -> WorkspaceAttestation:
        if not self._attestations or self._finished:
            raise ValueError("workspace attestation cannot finish in its current state")
        self._finished = True
        return self._append("terminal")

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        return self._environment.get_template_vars(**kwargs)

    def serialize(self) -> dict[str, Any]:
        return self._environment.serialize()

    def _append(
        self,
        stage: Literal["initial", "after_action", "terminal"],
        *,
        action_ordinal: int | None = None,
    ) -> WorkspaceAttestation:
        attestation = WorkspaceAttestation(
            ordinal=len(self._attestations) + 1,
            stage=stage,
            action_ordinal=action_ordinal,
            tree_sha256=capture_workspace_tree(self._environment),
        )
        self._attestations.append(attestation)
        return attestation


def capture_workspace_tree(environment: TransferEnvironment) -> str:
    result = environment.execute(
        {"command": _WORKSPACE_DIGEST_COMMAND},
        cwd="/workspace",
        timeout=180,
    )
    if result.get("returncode") != 0:
        raise ValueError("workspace digest command failed")
    output = result.get("output")
    if not isinstance(output, str):
        raise ValueError("workspace digest command returned non-text output")
    lines = output.splitlines()
    if len(lines) != 1 or not lines[0].startswith(_DIGEST_PREFIX):
        raise ValueError("workspace digest command returned an invalid record")
    digest = lines[0].removeprefix(_DIGEST_PREFIX)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("workspace digest command returned an invalid SHA-256")
    return digest


def preflight_transfer_runtime(
    mini_swe_checkout: Path,
    programbench_checkout: Path,
    *,
    task_ids: tuple[str, ...],
) -> TransferRuntimePreflight:
    mini_swe = verify_checkout(
        mini_swe_checkout,
        expected_commit=MINISWE_COMMIT,
        expected_tree=MINISWE_TREE,
    )
    programbench = verify_checkout(
        programbench_checkout,
        expected_commit=PROGRAMBENCH_COMMIT,
        expected_tree=PROGRAMBENCH_TREE,
    )
    task_root = programbench_checkout.resolve() / "src" / "programbench" / "data" / "tasks"
    for task_id in task_ids:
        task_file = task_root / task_id / "task.yaml"
        if task_file.is_symlink() or not task_file.is_file():
            raise ValueError(f"ProgramBench cohort task is missing: {task_id}")
    return TransferRuntimePreflight(
        mini_swe=mini_swe,
        programbench=programbench,
        tasks=len(task_ids),
    )


def verify_checkout(
    checkout: Path,
    *,
    expected_commit: str,
    expected_tree: str,
) -> CheckoutIdentity:
    root = checkout.resolve()
    if not root.is_dir():
        raise ValueError(f"checkout is not a directory: {root}")
    actual_root = Path(_git_text(root, "rev-parse", "--show-toplevel")).resolve()
    if actual_root != root:
        raise ValueError(f"checkout is not a Git top level: {root}")
    commit = _git_text(root, "rev-parse", "HEAD")
    tree = _git_text(root, "rev-parse", "HEAD^{tree}")
    if (commit, tree) != (expected_commit, expected_tree):
        raise ValueError(f"checkout revision differs from the frozen identity: {root}")
    if _git_text(root, "status", "--short", "--untracked-files=all"):
        raise ValueError(f"checkout must be clean: {root}")
    return CheckoutIdentity(commit=commit, tree=tree)


def collect_transfer_trajectories(
    *,
    project_root: Path,
    mini_swe_checkout: Path,
    programbench_checkout: Path,
    env_file: Path,
    task_ids: tuple[str, ...],
) -> TransferCollectionSummary:
    preflight_transfer_runtime(
        mini_swe_checkout,
        programbench_checkout,
        task_ids=task_ids,
    )
    environment_file = env_file.resolve()
    if not env_file.is_absolute():
        raise ValueError("provider env file must use an absolute path")
    if environment_file.is_symlink() or not environment_file.is_file():
        raise ValueError("provider env file must be a regular file")

    root = project_root.resolve()
    output_root = root / TRANSFER_RUN_ROOT
    output_root.mkdir(parents=True, exist_ok=True)
    collected = 0
    resumed = 0
    failed = 0
    with _upstream_imports(mini_swe_checkout, programbench_checkout):
        _load_environment_file(environment_file)
        instances = _load_instances(programbench_checkout, task_ids)
        config = _load_config(mini_swe_checkout)
        for task_id in task_ids:
            task_output = output_root / task_id
            trajectory_path = task_output / f"{task_id}.traj.json"
            state = _existing_trajectory_state(trajectory_path)
            if state == "complete":
                resumed += 1
                continue
            if state == "invalid":
                raise ValueError(f"existing transfer trajectory is invalid: {task_id}")
            task_output.mkdir(parents=True, exist_ok=True)
            try:
                _collect_one(
                    instance=instances[task_id],
                    output_directory=task_output,
                    config=config,
                )
                adapt_miniswe_trajectory(trajectory_path.read_bytes())
                collected += 1
            except Exception:
                failed += 1
                failure_path = task_output / "collection-error.txt"
                failure_path.write_text(traceback.format_exc(), encoding="utf-8")
    return TransferCollectionSummary(
        tasks=len(task_ids),
        collected=collected,
        resumed=resumed,
        failed=failed,
        task_ids=task_ids,
    )


def _collect_one(
    *,
    instance: dict[str, Any],
    output_directory: Path,
    config: dict[str, Any],
) -> None:
    models = importlib.import_module("minisweagent.models")
    environments = importlib.import_module("minisweagent.environments")
    agents = importlib.import_module("minisweagent.agents.default")
    programbench = importlib.import_module("minisweagent.run.benchmarks.programbench")

    task_id = _required_runtime_string(instance, "instance_id")
    instance_config = _deep_copy_json(config)
    environment_config = _runtime_object(instance_config, "environment")
    image_name = _required_runtime_string(instance, "image_name")
    environment_config["image"] = f"{image_name}:task_cleanroom_v6"
    model = models.get_model(config=_runtime_object(instance_config, "model"))
    inner = environments.get_environment(environment_config, default_type="docker")
    agent = None
    exit_status: str | None = None
    extra_info: dict[str, str] = {}
    trajectory_path = output_directory / f"{task_id}.traj.json"
    submission_path = output_directory / "submission.tar.gz"
    try:
        inner.execute(
            {
                "command": (
                    'git config user.name "mini-swe-agent" && '
                    'git config user.email "mini-swe-agent@proton.me"'
                )
            }
        )
        attested = AttestedWorkspaceEnvironment(inner)
        attested.begin()
        agent_config = _runtime_object(instance_config, "agent")
        agent = agents.DefaultAgent(model, attested, **agent_config)
        agent.extra_template_vars = {"instance": instance}
        info = agent.run()
        exit_status = _optional_runtime_string(info.get("exit_status"))
    except Exception as exc:
        exit_status = type(exc).__name__
        extra_info = {
            "exception_str": str(exc),
            "traceback": traceback.format_exc(),
        }
    finally:
        if agent is not None:
            environment = agent.env
            if not isinstance(environment, AttestedWorkspaceEnvironment):
                raise AssertionError("transfer agent lost its attested environment")
            environment.finish()
            try:
                programbench.copy_submission(inner, submission_path)
            except Exception as exc:
                extra_info["submission_copy_error"] = str(exc)
            agent.save(
                trajectory_path,
                {
                    "info": {"exit_status": exit_status, **extra_info},
                    "instance_id": task_id,
                    "workspace_attestations": [
                        item.model_dump(mode="json") for item in environment.attestations
                    ],
                },
            )
        cleanup = getattr(inner, "cleanup", None)
        if callable(cleanup):
            cleanup()


def _load_instances(
    programbench_checkout: Path,
    task_ids: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    module = importlib.import_module("programbench.utils.load_data")
    tasks_dir = programbench_checkout.resolve() / "src" / "programbench" / "data" / "tasks"
    loaded = module.load_all_instances(tasks_dir=tasks_dir, include_tests=False)
    by_id = {
        _required_runtime_string(instance, "instance_id"): instance
        for instance in loaded
        if _required_runtime_string(instance, "instance_id") in set(task_ids)
    }
    if tuple(sorted(by_id, key=task_ids.index)) != task_ids:
        raise ValueError("ProgramBench loader did not return the fixed cohort")
    return by_id


def _load_config(mini_swe_checkout: Path) -> dict[str, Any]:
    module = importlib.import_module("minisweagent.config")
    path = (
        mini_swe_checkout.resolve()
        / "src"
        / "minisweagent"
        / "config"
        / "benchmarks"
        / "programbench.yaml"
    )
    config = module.get_config_from_spec(str(path))
    model = _runtime_object(config, "model")
    model["model_name"] = TRANSFER_MODEL
    return config


def _load_environment_file(path: Path) -> None:
    module = importlib.import_module("dotenv")
    values = module.dotenv_values(path)
    if not values:
        raise ValueError("provider env file is empty")
    for key, value in values.items():
        if value is not None:
            os.environ[key] = value


@contextmanager
def _upstream_imports(
    mini_swe_checkout: Path,
    programbench_checkout: Path,
) -> Iterator[None]:
    additions = (
        str(mini_swe_checkout.resolve() / "src"),
        str(programbench_checkout.resolve() / "src"),
    )
    previous = tuple(sys.path)
    sys.path[:0] = list(additions)
    importlib.invalidate_caches()
    try:
        yield
    finally:
        sys.path[:] = previous
        importlib.invalidate_caches()


def _existing_trajectory_state(
    path: Path,
) -> Literal["absent", "interrupted", "complete", "invalid"]:
    if not path.exists():
        return "absent"
    if path.is_symlink() or not path.is_file():
        return "invalid"
    try:
        data = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError):
        return "interrupted"
    if not isinstance(data, dict):
        return "invalid"
    messages = data.get("messages")
    attestations = data.get("workspace_attestations")
    if not isinstance(messages, list) or not isinstance(attestations, list):
        return "interrupted"
    if not messages or not attestations:
        return "interrupted"
    last_message = messages[-1]
    last_attestation = attestations[-1]
    terminal = (
        isinstance(last_message, dict)
        and last_message.get("role") == "exit"
        and isinstance(last_attestation, dict)
        and last_attestation.get("stage") == "terminal"
    )
    if not terminal:
        return "interrupted"
    try:
        adapt_miniswe_trajectory(path.read_bytes())
    except ValueError:
        return "invalid"
    return "complete"


def _runtime_object(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = value.get(field)
    if not isinstance(result, dict):
        raise ValueError(f"runtime config field must be an object: {field}")
    return result


def _required_runtime_string(value: Mapping[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ValueError(f"runtime field must be a non-empty string: {field}")
    return result


def _optional_runtime_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _deep_copy_json(value: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(value))
    if not isinstance(result, dict):
        raise AssertionError("runtime configuration stopped being an object")
    return result


def _git_text(checkout: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(checkout), *args),
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise ValueError(f"cannot execute Git: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.decode(errors="replace").strip()
        raise ValueError(f"Git {' '.join(args)} failed: {detail}")
    return completed.stdout.decode(errors="strict").strip()
