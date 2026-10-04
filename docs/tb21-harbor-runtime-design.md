# TB2.1 Harbor compatibility runtime

## Problem

The frozen TB2.1 preflight requires Harbor to resolve
`tasks/dataset.toml` as an ordered list of 61 digest-pinned package tasks.
Stock Harbor 0.23.0 treats that file as a registry directory and later drops
package task identities from repository datasets. The protocol, executable,
project dependency files, and analysis code are frozen, so the compatibility
fix must live outside those files. The runtime must also prove that the
imported Harbor package, its distribution metadata, and the `harbor`
executable on `PATH` all come from one installation.

## Usage

Run one project-owned command from any directory:

```bash
tools/harbor-tb21-runtime \
  preflight \
  --tb21-checkout "$TB21_CHECKOUT"
```

An explicit cache root is useful in CI:

```bash
tools/harbor-tb21-runtime preflight \
  --cache-root "$RUNNER_TEMP/harness4-runtimes" \
  --tb21-checkout "$RUNNER_TEMP/terminal-bench-2-1"
```

The command prints the frozen preflight result, runtime ID, runtime path, and
`provider_calls=0`. It has no collection subcommand and accepts no Provider
environment file.

Do not invoke the launcher through `uv run`. The launcher uses the project's
existing Python only to load the builder. The frozen preflight runs with the
dedicated runtime's interpreter and `PATH`.

## Shape

The public operation is:

```python
run_frozen_preflight(
    project_root: Path,
    tb21_checkout: Path,
    *,
    cache_root: Path | None = None,
) -> PreflightResult
```

It owns source verification, wheel builds, installation, cache recovery,
attestation, and preflight execution. Callers do not coordinate those stages.
This keeps the interface deep and avoids temporal decomposition.

The reviewed specification at
`config/tb21-harbor-runtime.json` binds:

- the annotated Harbor v0.23.0 tag object, peeled commit, and source tree;
- the patch path, patch SHA-256, and exact changed-path set;
- `pyproject.toml` and `uv.lock` SHA-256 values;
- the exact `uv_build` version and `SOURCE_DATE_EPOCH`;
- the frozen preflight and executable artifact SHA-256 values;
- the expected selector path, member count, and zero-Provider condition.

The runtime ID also binds the project commit and tree, Python executable bytes
and version, `uv` executable bytes and version, build-constraint bytes, and
platform identity.

The builder exports the project with `git archive HEAD`. It never builds the
installed project from the working tree. It fetches the exact annotated Harbor
tag, verifies all three Git object IDs, applies the reviewed patch, and rejects
any changed path outside the four-file allowlist.

Both distributions are built with `uv_build==0.8.17`. The builder first syncs
production dependencies from the archived `uv.lock`, then installs the two
locally built wheels by exact distribution requirement from isolated
wheelhouses. This avoids editable and direct-URL installs.

The final receipt records both wheel hashes and aggregate hashes of every
installed distribution file. A runtime is reusable only when the canonical
receipt, input model, wheel files, imported module paths, metadata paths,
distribution versions, aggregate hashes, interpreter, and `PATH` executable
all revalidate. The builder writes the receipt last. A missing or invalid
receipt causes a complete rebuild under a per-runtime file lock.

The preflight subprocess uses:

```text
<runtime>/venv/bin/python -I scripts/tb21_sensitivity.py preflight ...
```

Its environment removes `PYTHONHOME`, `PYTHONPATH`, `PYTHONSTARTUP`,
`PYTHONUSERBASE`, `VIRTUAL_ENV`, and `UV_PROJECT_ENVIRONMENT`.
`<runtime>/venv/bin` is first on `PATH`, and `PYTHONNOUSERSITE=1`.

## Synthesis decision

The selected design uses one content-addressed runtime owner and one preflight
command. The other candidates contributed two checks: build the project from a
Git archive, and bind exact tool identities. Separate public build, verify,
inspect, collect, and generic run commands were rejected because callers would
need to understand lifecycle ordering. A Provider-capable wrapper was also
rejected because acceptance only needs the frozen preflight.

The patched distribution reports `0.23.0+tb21.1`. PEP 440 treats that local
version as satisfying the unchanged `harbor==0.23.0` requirement, while the
metadata remains distinguishable from the stock artifact.

## Tradeoffs accepted

- We accept one network fetch on a cold cache in exchange for verifying the
  upstream tag object rather than trusting a mutable local checkout.
- We accept a project-specific runtime builder in exchange for keeping the
  frozen protocol, executable, `pyproject.toml`, and `uv.lock` unchanged.
- We retain built wheels in each runtime in exchange for verifying their bytes
  on every cache reuse.
- We reject Git archives containing symlinks in exchange for a simple
  extraction boundary with no path-alias ambiguity.

## Alternatives considered

A global Harbor replacement would hide setup from this repository but could
silently affect unrelated projects and would not bind import, metadata, and
executable identity.

A `PYTHONPATH` overlay passed the selector prototype, but distribution metadata
and `PATH` still described the stock installation. It could not produce an
honest runtime attestation.

Separate build, verify, and preflight commands exposed one invariant as a
caller-managed sequence. The chosen command keeps recovery and validation
inside the runtime owner.

## Risks

The wheel hash is an output in the receipt, not a platform-independent constant
in the specification. The runtime ID binds the platform and build tools. A
future multi-platform release should compare wheel reproducibility before
promoting one global expected hash.

The patch is local until an equivalent upstream Harbor release exists. Moving
to that release requires a new reviewed specification and a new runtime ID.
