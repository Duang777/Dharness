# PrefixBench development offline mutation campaign

## Problem

The repository can validate the source-attested PrefixBench development cohort and run a
deterministic offline campaign for one `StatePrefix`. It cannot yet bind those operations into
one cohort artifact. The missing layer must use only the 28 committed development tasks, preserve
every scheduled outcome, freeze the mutation implementation, and remain checkable when the
ignored raw run directory is absent.

The current operators and oracle inspect completion attempts. A phase-entry witness does not
contain the later evidence required by these checks. Version 1 therefore uses one complete
journal prefix per task and treats phase eligibility as bound readiness metadata. It does not
claim five-phase mutation results.

## Usage

Build the fixed development report:

```bash
uv run python scripts/prefixbench_campaign.py build
```

Verify the committed report:

```bash
uv run python scripts/prefixbench_campaign.py check
```

The command has no split, model, provider, Docker, operator, request, or journal-selection option.
It cannot reach the 61-task test split or run the production loop.

Python callers use two operations:

```python
from evidence_harness_mutation import (
    build_prefixbench_development_campaign,
    check_prefixbench_development_campaign,
)

report = build_prefixbench_development_campaign(project_root)
errors = check_prefixbench_development_campaign(
    project_root=project_root,
    report_path=report_path,
)
```

## Shape

`src/evidence_harness_mutation/prefixbench_campaign.py` owns the cohort contract. It loads the
fixed readiness report, verifies the existing PrefixBench source gate, loads each bound journal,
and calls `run_offline_campaign()` once per task. The existing single-prefix campaign remains
unchanged.

The report shape is:

```python
class PrefixBenchCampaignTask(FrozenModel):
    index: int
    name: str
    task_identity_sha256: Sha256
    journal: PrefixBenchFileBinding
    journal_lines: int
    projected_attempts: int
    campaign: OfflineCampaignReport


class PrefixBenchCampaignSummary(FrozenModel):
    tasks: int
    tasks_with_attempts: int
    tasks_with_verified_attempts: int
    projected_attempts: int
    verified_attempts: int
    outcomes: OfflineCampaignSummary
    operators: tuple[PrefixBenchOperatorSummary, ...]


class PrefixBenchDevelopmentCampaignReport(FrozenModel):
    schema_version: Literal[1]
    benchmark: Literal["PrefixBench"]
    dataset: Literal["terminal-bench@2.0"]
    split: Literal["development"]
    campaign: Literal["completion-offline-mutation-v1"]
    claim_boundary: Literal["development-completion-offline-only-no-test-or-production-execution"]
    sources: PrefixBenchCampaignSources
    producer: ProducerAttestation
    protocol: BoundPrefixBenchMutationProtocol
    tasks: tuple[PrefixBenchCampaignTask, ...]
    summary: PrefixBenchCampaignSummary
```

Each task embeds its complete `OfflineCampaignReport`. An `offline_violation` therefore keeps the
existing inline `ReducedCounterexample`, sparse trace, audit, witness, provenance, and reduction
metrics. No result row is filtered or normalized into a weaker shape.

The task validator requires:

- `campaign.source` to match the journal hash and cohort producer commit;
- `campaign.through_line` to equal the complete journal line count;
- the case requests to equal every projected attempt crossed with `MutationId` declaration order;
- the fallback schedule to contain attempt 1 for every operator when no attempt exists.

The report derives all totals and per-operator counts from nested cases. It requires contiguous
task indices, unique names and journal paths, one producer, the manifest task count, and the exact
summary.

## Frozen protocol

`experiments/prefixbench-v1/mutation-protocol-v1.json` is the explicit protocol freeze. It records:

- cohort ID, task count, full-journal prefix policy, readiness task order, and sequential execution;
- every operator and its expected invariant in declaration order;
- all five offline outcomes;
- three-run campaign audits and three-run reducer audits;
- the new-violation baseline rule;
- the reducer algorithm, minimality claim, and inline counterexample policy;
- component entrypoints and the source paths each component depends on;
- one length-framed SHA-256 source set for the complete implementation dependency closure.

The source set includes `pyproject.toml`, `uv.lock`, the production protocol and collection
profile, PrefixBench admission, the mutation model, attempt projection, journal loader,
operators, oracle, reducer, single-prefix campaign, and cohort runner. The manifest is canonical
JSON. The report stores both its file binding and parsed value, so artifact-only validation can
recompute the manifest hash without raw journals.

Changing a bound implementation file requires an explicit protocol manifest update. Once the
test split is collected, semantic changes require a new protocol ID instead of replacing version
1.

## Build and check

The builder reads these fixed inputs:

```text
evaluation/prefixbench-v1-development-readiness.json
evaluation/prefixbench-v1-development-canonical.json
evaluation/matrix-prefixbench-development.json
experiments/prefixbench-v1/mutation-protocol-v1.json
```

Before execution it requires:

1. canonical readiness bytes, schema 2, status `ready`, profile `prefixbench-v1`;
2. 28 development tasks, zero test tasks, and source admission for every task;
3. canonical and matrix bindings that match the fixed paths and bytes;
4. the existing readiness checker to accept all raw source files;
5. the three development inputs to equal their committed `HEAD` bytes;
6. one producer attestation across the cohort;
7. the protocol manifest and every implementation file to match their hashes.

The builder follows readiness order and reads only each task's bound journal path. It never scans
`runs/`. It loads a prefix without a cutoff and calls `run_offline_campaign(prefix)` without an
explicit request list.

The checker first validates the report, fixed input bindings, and protocol source hashes. It then
uses the same all-or-none rule as PrefixBench readiness:

- no bound raw source files: validate the self-contained artifact and return success;
- all bound raw source files: rebuild in memory and require byte equality;
- partial raw source files: return an error without rebuilding.

Both report and manifest serialization use ASCII JSON, sorted keys, two-space indentation, and
one trailing newline. The CLI writes through a temporary file, `fsync()`, and `os.replace()`.

## Module map

| File | Responsibility |
|---|---|
| `src/evidence_harness_mutation/prefixbench_campaign.py` | Protocol types, cohort report, fixed admission, build, and check |
| `scripts/prefixbench_campaign.py` | `argparse`, atomic output, summaries, and exit codes |
| `experiments/prefixbench-v1/mutation-protocol-v1.json` | Frozen protocol and implementation hashes |
| `tests/mutation/test_prefixbench_campaign.py` | Domain, boundary, schedule, source, and determinism tests |
| `tests/test_prefixbench_campaign_script.py` | CLI defaults, output, and exit codes |
| `evaluation/prefixbench-v1-development-offline-campaign.json` | Canonical development result |

## Synthesis decision

Candidate A is the base because it validates the exact request sequence and gives each protocol
role an explicit dependency mapping. Candidate C contributed dataset binding, unique journal
validation, path confinement, and separate package and CLI tests. The cross-judge scored A 29/30
and C 26/30.

The final design replaces A's repeated per-role source hashes with one source set plus component
path references. This keeps one authoritative hash per file while preserving each component's
dependency declaration. It also uses a checked-in protocol manifest rather than constructing the
freeze only at report time.

Candidate B did not produce an artifact before the design deadline and was excluded.

## Tradeoffs accepted

- We accept a development-specific API in exchange for making test-split access unavailable.
- We accept a roughly 1.5 MB report in exchange for retaining all cases and inline reductions.
- We accept byte-sensitive source hashes in exchange for exact implementation identity.
- We accept sequential execution in exchange for stable order and no shared writer.
- We accept structural checking without raw runs in exchange for normal-clone verification.

## Alternatives considered

A configurable split runner would expose test selection and scheduling policy to callers. It
would make the public API larger without hiding more work.

Extending `campaign.py` would mix benchmark admission and filesystem policy into the reusable
single-prefix engine.

Separate loader, executor, aggregator, and serializer services would organize code by execution
time and repeat the same cohort representation across modules.

Detached counterexample files would reduce the main file size but create multiple atomic outputs
and allow missing or mismatched evidence.

## Open questions and risks

- Future phase-specific operators need a new protocol and an outcome boundary after each phase
  witness. Current phase lines are not valid cutoffs for completion invariants.
- A clone without raw runs can verify the artifact and its implementation identity, but it cannot
  recompute outcomes.
- Publication packaging may later need a distributable journal bundle if independent rebuilds are
  required outside the collection environment.

## Next implementation step

Implement the protocol and report models, then prove the fixed admission boundary before wiring
the existing single-prefix runner.
