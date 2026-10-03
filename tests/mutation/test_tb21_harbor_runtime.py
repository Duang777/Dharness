from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_harness_mutation import tb21_harbor_runtime as runtime

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SHA = "a" * 64


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _source_repo(root: Path) -> str:
    root.mkdir()
    (root / "tracked.txt").write_text("committed\n", encoding="utf-8")
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "fixture")
    return _git(root, "rev-parse", "HEAD")


def _inputs(spec: runtime.RuntimeSpec) -> runtime.RuntimeInputs:
    return runtime.RuntimeInputs(
        spec=spec,
        spec_sha256=_SHA,
        project_commit="b" * 40,
        project_tree="c" * 40,
        python=runtime.ToolIdentity(path="/python", version="3.12.13", sha256=_SHA),
        uv=runtime.ToolIdentity(path="/uv", version="uv 0.11.19", sha256=_SHA),
        uv_build_constraint_sha256=_SHA,
        platform="test-platform",
    )


def _distribution(
    root: Path,
    *,
    name: str,
    version: str,
    module: str,
) -> runtime.InstalledDistribution:
    site_packages = root / "venv" / "lib" / "python3.12" / "site-packages"
    return runtime.InstalledDistribution(
        name=name,
        version=version,
        imported_path=str(site_packages / module / "__init__.py"),
        metadata_path=str(site_packages / f"{module}-1.dist-info"),
        distribution_files=3,
        distribution_sha256=_SHA,
        direct_url=False,
    )


def _probe(root: Path, spec: runtime.RuntimeSpec) -> runtime.RuntimeProbe:
    return runtime.RuntimeProbe(
        python_executable=str(root / "venv" / "bin" / "python"),
        harbor=_distribution(
            root,
            name=spec.upstream.distribution_name,
            version=spec.upstream.distribution_version,
            module="harbor",
        ),
        project=_distribution(
            root,
            name=spec.project.distribution_name,
            version=spec.project.distribution_version,
            module="evidence_harness",
        ),
        harbor_executable=str(root / "venv" / "bin" / "harbor"),
    )


def _prepared_runtime(
    root: Path,
    spec: runtime.RuntimeSpec,
    inputs: runtime.RuntimeInputs,
) -> runtime.PreparedRuntime:
    project = _distribution(
        root,
        name=spec.project.distribution_name,
        version=spec.project.distribution_version,
        module="evidence_harness",
    )
    harbor = _distribution(
        root,
        name=spec.upstream.distribution_name,
        version=spec.upstream.distribution_version,
        module="harbor",
    )
    artifact = runtime.ArtifactBinding(path="artifacts/test.whl", bytes=1, sha256=_SHA)
    receipt = runtime.RuntimeReceipt(
        runtime_id=inputs.runtime_id,
        inputs=inputs,
        harbor_wheel=artifact,
        project_wheel=artifact,
        harbor=runtime.HarborInstallation(
            distribution=harbor,
            executable=runtime.ArtifactBinding(
                path="venv/bin/harbor",
                bytes=1,
                sha256=_SHA,
            ),
        ),
        project=project,
    )
    return runtime.PreparedRuntime(root=root, receipt=receipt)


def test_reviewed_spec_and_patch_are_canonical_and_hash_bound() -> None:
    spec, data = runtime._load_spec(PROJECT_ROOT)

    assert data == spec.canonical_bytes()
    assert spec.patch.changed_paths == runtime._PATCHED_PATHS
    assert spec.upstream.distribution_version == "0.23.0+tb21.1"
    assert spec.acceptance.expected_member_count == 61


def test_spec_rejects_a_changed_patch(tmp_path: Path) -> None:
    spec_root = tmp_path / "project"
    spec_path = spec_root / runtime.RUNTIME_SPEC
    patch_path = spec_root / "patches" / "harbor-v0.23.0-tb21-dataset-toml.patch"
    spec_path.parent.mkdir(parents=True)
    patch_path.parent.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / runtime.RUNTIME_SPEC, spec_path)
    shutil.copy2(
        PROJECT_ROOT / "patches" / "harbor-v0.23.0-tb21-dataset-toml.patch",
        patch_path,
    )
    patch_path.write_bytes(patch_path.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="patch SHA-256"):
        runtime._load_spec(spec_root)


def test_runtime_id_binds_tool_and_project_identities() -> None:
    spec, _data = runtime._load_spec(PROJECT_ROOT)
    inputs = _inputs(spec)

    changed_tool = inputs.model_copy(
        update={
            "uv": inputs.uv.model_copy(update={"version": "uv 0.11.20"}),
        }
    )
    changed_tree = inputs.model_copy(update={"project_tree": "d" * 40})

    assert inputs.runtime_id != changed_tool.runtime_id
    assert inputs.runtime_id != changed_tree.runtime_id


def test_runtime_environment_removes_python_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (*runtime._PYTHON_ENV_KEYS, "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV"):
        monkeypatch.setenv(key, "untrusted")
    monkeypatch.setenv("PATH", "/usr/bin")

    env = runtime._runtime_environment(tmp_path)

    for key in (*runtime._PYTHON_ENV_KEYS, "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV"):
        assert key not in env
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PATH"] == f"{tmp_path / 'venv' / 'bin'}:/usr/bin"


def test_project_archive_uses_committed_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    commit = _source_repo(source)
    (source / "tracked.txt").write_text("working tree\n", encoding="utf-8")
    (source / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    destination = tmp_path / "archive"

    runtime._extract_project_archive(source, commit, destination)

    assert (destination / "tracked.txt").read_text() == "committed\n"
    assert not (destination / "untracked.txt").exists()


def test_project_archive_rejects_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    commit = _source_repo(source)
    (source / "linked.txt").symlink_to("tracked.txt")
    _git(source, "add", "linked.txt")
    _git(source, "commit", "--quiet", "-m", "add symlink")
    commit = _git(source, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match="not a regular file"):
        runtime._extract_project_archive(source, commit, tmp_path / "archive")


def test_wheel_metadata_must_match_name_and_version(tmp_path: Path) -> None:
    wheel = tmp_path / "example.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "example-1.0.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: example\nVersion: 1.0\n",
        )

    runtime._verify_wheel_metadata(wheel, "example", "1.0")
    with pytest.raises(ValueError, match="wheel metadata mismatch"):
        runtime._verify_wheel_metadata(wheel, "example", "2.0")


def test_artifact_binding_rejects_paths_outside_runtime(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("data", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the runtime root"):
        runtime._artifact_binding(outside, root)


def test_runtime_attestation_checks_import_metadata_and_path_alignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, _data = runtime._load_spec(PROJECT_ROOT)
    root = tmp_path / "runtime"
    probe = _probe(root, spec)

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, probe.model_dump_json(), "")

    monkeypatch.setattr(runtime, "_run", fake_run)

    assert runtime._attest_runtime(root, spec) == probe


def test_runtime_attestation_rejects_direct_url_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, _data = runtime._load_spec(PROJECT_ROOT)
    root = tmp_path / "runtime"
    payload = _probe(root, spec).model_dump(mode="json")
    payload["harbor"]["direct_url"] = True

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(runtime, "_run", fake_run)

    with pytest.raises(ValueError, match="invalid runtime attestation"):
        runtime._attest_runtime(root, spec)


def test_frozen_preflight_uses_only_the_prepared_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, spec_data = runtime._load_spec(PROJECT_ROOT)
    inputs = _inputs(spec)
    prepared = _prepared_runtime(tmp_path / "runtime", spec, inputs)
    checkout = tmp_path / "tb21"
    checkout.mkdir()
    expected = (
        f"ready_for_provider_execution=true executable_commit={spec.acceptance.executable_commit}\n"
    )

    monkeypatch.setattr(runtime, "_git_project_root", lambda _path: PROJECT_ROOT)
    monkeypatch.setattr(runtime, "_load_spec", lambda _root: (spec, spec_data))
    monkeypatch.setattr(runtime, "_read_inputs", lambda *_args: inputs)
    monkeypatch.setattr(runtime, "_ensure_runtime", lambda *_args: prepared)

    def fake_run(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        assert command == [
            str(prepared.python),
            "-I",
            str(PROJECT_ROOT / spec.acceptance.preflight_script),
            "preflight",
            "--tb21-checkout",
            str(checkout),
        ]
        assert cwd == PROJECT_ROOT
        assert check is False
        assert env is not None
        assert env["PATH"].split(":")[0] == str(prepared.root / "venv" / "bin")
        assert "PYTHONPATH" not in env
        return subprocess.CompletedProcess(command, 0, expected, "")

    monkeypatch.setattr(runtime, "_run", fake_run)

    result = runtime.run_frozen_preflight(
        PROJECT_ROOT,
        checkout,
        cache_root=tmp_path / "cache",
    )

    assert result.runtime == prepared
    assert result.stdout == expected


def test_frozen_preflight_rejects_symlinked_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    link = tmp_path / "checkout-link"
    link.symlink_to(checkout, target_is_directory=True)

    with pytest.raises(ValueError, match="must be a directory"):
        runtime.run_frozen_preflight(PROJECT_ROOT, link)


def test_cli_has_no_collection_command() -> None:
    with pytest.raises(SystemExit):
        runtime._parse_args(["collect"])


def test_runtime_probe_schema_rejects_missing_harbor_executable(
    tmp_path: Path,
) -> None:
    spec, _data = runtime._load_spec(PROJECT_ROOT)
    payload = _probe(tmp_path, spec).model_dump(mode="json")
    payload["harbor_executable"] = None

    with pytest.raises(ValidationError):
        runtime.RuntimeProbe.model_validate(payload)
