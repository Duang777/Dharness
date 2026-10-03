# Terminal-Bench 2.1 sensitivity executable

## Scope

The executable implements the preregistered
`thesis-tb21-sensitivity-v1` protocol. It does not change the frozen matrix,
protocol, protected source files, method set, operator set, outcome classes,
task count, deterministic seed, or output paths.

The executable has six public operations:

```python
freeze(project_root)
load(project_root)
preflight(project_root, tb21_checkout=...)
collect(project_root, tb21_checkout=..., env_file=...)
build(project_root)
check(project_root)
```

The CLI exposes the same operations:

```bash
uv run python scripts/tb21_sensitivity.py freeze
uv run python scripts/tb21_sensitivity.py preflight \
  --tb21-checkout /absolute/path/to/terminal-bench-2-1
uv run python scripts/tb21_sensitivity.py collect \
  --tb21-checkout /absolute/path/to/terminal-bench-2-1 \
  --env-file /absolute/path/to/provider.env
uv run python scripts/tb21_sensitivity.py build
uv run python scripts/tb21_sensitivity.py check
```

The CLI has no task, method, operator, seed, retry, bootstrap, start offset, or
output path controls.

## Module ownership

`tb21_sensitivity.py` is the public facade. The three private modules own
separate contracts:

- `_tb21_manifest.py` owns source bindings, the 61-task run plan, and
  `P -> E -> HEAD` chronology.
- `_tb21_harbor.py` owns Harbor capability checks, immutable task receipts,
  process handling, and interrupted-only resume.
- `_tb21_analysis.py` owns the fixed artifact dependency graph, TB2.1
  deterministic choices, statistics, and the cross-version comparison.

The executable imports the frozen protocol but never edits it.

## Identity

Each scheduled task uses this primary key:

```text
terminal-bench-2-1 + source_identity_sha256
```

The run plan also binds:

- matrix ordinal;
- full `terminal-bench/<name>` registry name;
- `sha256:<digest>` package reference;
- pinned Git task tree;
- invocation digest.

Short task names are not scheduling or join keys. The full registry name and
package digest must survive Harbor resolution unchanged.

## Freeze and chronology

`freeze` checks the filesystem before it reads protocol inputs. Broken
symlinks count as existing paths. It then checks every ref with:

```bash
git log --all --format=%H -- <path>
```

The six outcome paths must have no filesystem entry and no Git history. The
executable path may be absent or may contain the same uncommitted bytes during
an idempotent pre-commit rerun, but it must have no Git history.

The manifest binds:

- the protocol file and preregistration commit `P`;
- the sensitivity matrix;
- the protected protocol source set;
- every new executable, test, and design file;
- the imported main-analysis kernels;
- the derived run-plan digest;
- the Harbor, collector, random-choice, bootstrap, and artifact contracts.

The manifest excludes itself. After commit, `load` rebuilds every binding from
the worktree, compares every bound file with `HEAD`, finds the first commit
that contains the manifest bytes as `E`, and proves:

```text
P -> E -> HEAD
```

## Harbor capability check

Preflight does not read a Provider env file and does not create the run root.
It verifies the supplied checkout against the pinned commit, root tree, tasks
tree, manifest blob, manifest SHA-256, all 61 package digests, and all 61 task
trees.

It then checks Harbor behavior. A passing result requires the repository
resolver to:

1. treat `tasks/dataset.toml` as the registry file;
2. resolve the frozen dataset through the real repository dataset API;
3. return package task identities;
4. preserve all 61 full registry names and package digests in matrix order;
5. make zero Provider calls.

Harbor 0.23.0 fails the first requirement. Its repository client maps the
configured path to:

```text
tasks/dataset.toml/registry.json
```

Its local dry-run also reports Git dataset membership, filters, and trial count
as skipped. A successful 0.23 dry-run is therefore not execution evidence.
Preflight returns:

```text
ready_for_provider_execution=false
blocker=repo_dataset_toml_not_resolved
harbor_version=0.23.0
observed_registry_path=tasks/dataset.toml/registry.json
```

The executable does not generate a replacement `registry.json`, switch to
local task paths, or shorten registry names. Those changes would violate the
frozen selector.

## Collection

Collection starts only from an in-memory passing preflight result. The run
seal records the executable, protocol, matrix, run plan, Harbor runtime,
resolved members, producer attestation, model, profile, and Provider env file
SHA-256. It stores neither the env path nor its contents.

Tasks run serially in matrix order with:

```text
--n-concurrent 1
--n-attempts 1
--max-retries 0
--include-task-name <full-registry-name>
```

The command inherits the frozen TB2.0 per-task Debian source mount map. This
keeps the operating-system package source condition aligned across versions.

Each task has immutable receipts:

```text
tasks/<ordinal>-<source-id-prefix>/attempt-<n>/
  intent.json
  launch.json
  process.lease
  interrupted.json | terminal.json | fault.json
  harbor/
```

A terminal Harbor result is absorbing. Reward zero, verifier failure, agent
failure, timeout, and other completed exceptions are outcomes and are never
retried. Only a forwarded signal, `CancelledError`, or `KeyboardInterrupt`
permits another attempt with the same invocation digest. A dead launch without
a result or interruption receipt becomes faulted.

The collector scans only this run root. It does not select a latest result,
combine run roots, or create a retry matrix.

## Artifact graph

The fixed graph is:

```text
raw receipts
  -> canonical
  -> readiness
  -> offline campaign
  -> TB2.1 method comparison
  -> final sensitivity report
```

`build` computes all reachable bytes in memory. The CLI validates every
existing destination before it writes any missing file. Different existing
bytes stop the entire write.

Canonical rows retain the version-scoped key, registry name, package digest,
result, config, journal, reward, and exception. Readiness independently loads
each schema-2 journal. The campaign runs only against admitted journals.

The method report requires all 61 journals. It retains the five frozen methods,
four operators, six outcomes, one slot grid, exact McNemar test, 10,000 task
bootstrap resamples, and one four-contrast Holm family.

TB2.1 choices use:

```text
thesis-tb21-sensitivity-v1
```

Bootstrap seeds use:

```text
thesis-tb21-sensitivity-v1/bootstrap-rq2-v1
```

Neither domain reuses `thesis-main-analysis-v1`.

The final report reads the frozen TB2.0 method report and reports direction
agreement and disposition agreement for each comparator. It does not pool
tasks, recompute a cross-version p-value, join Holm families, or change the
main RQ disposition.

## Verification boundary

The synthetic tests use temporary files and repositories. They do not read a
held-out outcome, RQ report, CrossHarness result, or sensitivity outcome. The
real preflight may inspect the pinned TB2.1 checkout and installed Harbor
package, but it cannot call a Provider.
