# PrefixBench live collection design

## Purpose

PrefixBench requires a new live collection. Existing schema-1 journals do not prove which
committed runtime produced them and do not record exact controller phase entries. This milestone
adds that evidence without running the benchmark.

The collection contract has five links:

1. the launcher proves that runtime files in the imported checkout equal `git archive HEAD`;
2. the Harbor agent repeats the proof from the package it actually imported;
3. the leading journal event records the verified producer and frozen collection profile;
4. the collector binds the exact result, config, and journal bytes and copies producer fields
   from that journal;
5. PrefixBench readiness checks the source contract and phase-event semantics independently.

Ordinary live runs remain valid without a collection profile. Replay and isolation agents retain
their current option schemas.

## Usage

Generate the frozen 28-task development matrix from the source matrix and readiness artifact:

```bash
uv run python scripts/prefixbench.py matrix \
  --split development \
  --out evaluation/matrix-prefixbench-development.json
```

The builder requires the canonical readiness bytes and verifies the source matrix against the
readiness file binding. It preserves task metadata and source order. The 61-task test split is not
included.

Run the preflight and print the Harbor command without reading provider credentials or starting a
task:

```bash
uv run python scripts/run_evaluation.py \
  --model openai/modelhub/gpt-5.6-terra \
  --matrix evaluation/matrix-prefixbench-development.json \
  --collection-profile prefixbench-v1 \
  --agent-kwarg api_base=https://xpa-relay.bytedance.net/v1 \
  --dry-run
```

Run a resumable collection only after reviewing the dry-run output:

```bash
uv run python scripts/run_full_evaluation.py \
  --model openai/modelhub/gpt-5.6-terra \
  --env-file /absolute/path/to/provider.env \
  --matrix evaluation/matrix-prefixbench-development.json \
  --run-name prefixbench-v1-development-20261002 \
  --collection-profile prefixbench-v1 \
  --agent-kwarg api_base=https://xpa-relay.bytedance.net/v1
```

Build a schema-2 canonical manifest:

```bash
uv run python scripts/collect_evaluation_results.py \
  runs/terminal-bench-2/prefixbench-v1-development-20261002 \
  --matrix evaluation/matrix-prefixbench-development.json \
  --collection-profile prefixbench-v1 \
  --manifest-out evaluation/prefixbench-v1-canonical.json
```

Without `--collection-profile`, both evaluation launchers and the collector preserve their
existing behavior. The collector continues to emit canonical schema 1.

## Frozen profile

`FrozenCollectionProfile` is the single source of truth for `prefixbench-v1`. It owns:

- the exact agent options that affect loop, review, prompt, and model-generation behavior;
- the reserved Harbor option keys that callers may not override;
- Harbor argument expansion;
- validation of effective options recorded in `run_started`.

Connection routing such as `api_base`, the model name, task matrix, and provider environment are
bound by existing Harbor and run configuration files. They are not controller options.

The profile and producer fields are Harbor adapter inputs, not `LoopOptions` fields:

```text
prefixbench_profile
producer_commit
producer_tree
producer_source_sha256
```

All four must be absent for an ordinary run or present for a profiled run.

## Producer attestation

The runtime source set is:

```text
pyproject.toml
uv.lock
src/evidence_harness/**/*.py
```

The digest uses `pyproject.toml`, `uv.lock`, then sorted package paths. It hashes each normalized
relative path and file body with unsigned eight-byte big-endian length prefixes. The filesystem
and Git archive adapters call the same pure digest function.

Profile preflight:

1. resolves the Git top level for the imported `evidence_harness` package;
2. reads the `HEAD` commit and root tree;
3. rejects symlinked or missing runtime files;
4. reads the same paths from `git archive --format=tar HEAD`;
5. requires the workspace and archive bindings to match exactly;
6. returns the commit, tree, and runtime source SHA-256.

The comparison rejects modified, deleted, and untracked runtime files. Profiled wheels or source
archives fail because they cannot prove Git commit and tree identity. Ordinary runs do not require
Git provenance.

The full-run orchestrator repeats this check before each task. The Harbor agent repeats it again
from its imported package root before creating the journal. This closes source changes between
preflight and task startup.

## Journal contract

The leading event remains journal schema 2. A profiled run adds both collection fields:

```json
{
  "journal_schema_version": 2,
  "instruction": "...",
  "options": {},
  "prefixbench_profile": "prefixbench-v1",
  "producer": {
    "commit": "...",
    "tree": "...",
    "source_sha256": "..."
  }
}
```

The loop emits five explicit entry witnesses. Each payload has a typed model.

| Phase | Event | Placement |
|---|---|---|
| thinking | `executor_turn_started` | Before each non-finalizing executor model call |
| executing | `work_batch_started` | After work guards and `work_epoch += 1`, before command handling |
| finalizing | `finalization_started` | On the existing finalization latch transition |
| reviewing | `completion_review_started` | After mechanical acceptance and review count increment |
| recovering | `recovery_required` | On `must_replan: false -> true` |

An entry event proves that the controller entered a state. It does not claim that prompt
construction, a model call, or a command later succeeded.

`executor_turn_started` has an attempt ID independent of the turn budget. It is absent for
finalization calls. `recovery_required` is edge-triggered, so repeated feedback in one recovery
episode cannot create duplicate witnesses.

## Canonical schema 2

Profile-mode collection validates the latest selected result for each task. It does not fall back
to an older result when the latest result violates the contract.

Every selected result must be a live Evidence Harness run with:

- one journal at `<trial>/agent/evidence-harness/events.jsonl`;
- one leading schema-2 `run_started`;
- the requested profile;
- a valid producer attestation;
- profile-controlled options that match `prefixbench-v1`.

The collector hashes the journal and records its path and SHA-256. Producer fields come only from
the journal. It never reads Git state. All selected rows must have one producer attestation.

The top-level manifest declares schema 2 and `collection_profile`. Each task row retains the
schema-1 identity fields and adds the journal, profile, and producer fields.

## Readiness

Readiness separates source admission from phase eligibility.

A task is source-admitted when its result, config, journal, profile, producer, and frozen options
form one consistent schema-2 record. A source-admitted task contributes only the phase entries
that pass semantic validation. It does not need to enter all five phases.

The phase validator checks:

- executor attempt IDs increase and no thinking entry occurs after finalization;
- work epochs increase and each work entry has a later command or policy outcome for that epoch;
- finalization occurs at most once and has a non-empty valid trigger set;
- review ordinals and attempt IDs increase and each review entry has a review or error outcome;
- recovery ordinals increase and an episode closes through replan or finalization before another
  recovery entry;
- every witness is after `run_started` and before `run_finished`.

The initial coverage policy applies to the development split. It requires at least one valid
source-admitted witness for each phase across the collected development cohort. Test coverage is
reported but does not gate readiness until the benchmark execution protocol freezes test quotas.

Status derives from evidence:

```text
no source-admitted tasks
    -> source_cohort_unavailable
source-admitted tasks but incomplete development phase coverage
    -> phase_coverage_incomplete
all five development phases represented
    -> ready
```

The committed schema-1 readiness artifact remains parseable and checkable. It continues to report
the historical unavailable cohort. Only a schema-2 report can represent the new collection.

## Synthesis decision

The selected design uses the strongest end-to-end source chain and exact phase-entry placement.
The synthesis also adopts a frozen profile object, typed event payloads, a semantic phase
validator, and a per-task orchestrator recheck.

The following alternatives are rejected:

- deriving producer identity from the collector checkout;
- placing provenance in `LoopOptions`;
- requiring every task to contain all five phases;
- inferring entry points from legacy events;
- accepting multiple producer revisions in one canonical cohort;
- incrementing the journal schema for additive events and optional profile fields.

## Verification

Tests must cover:

1. clean, dirty, missing, untracked, and symlinked runtime source sets;
2. Git archive and filesystem digest equivalence;
3. ordinary option compatibility and reserved profile overrides;
4. profile preflight, per-task recheck, and agent-side re-attestation;
5. event placement, ordering, failure paths, and recovery episode uniqueness;
6. unchanged schema-1 collection;
7. schema-2 journal binding, profile validation, replay rejection, and mixed-producer rejection;
8. semantic phase validation and the three readiness statuses;
9. validation of the existing schema-1 readiness artifact.

This milestone runs unit, static, build, and artifact checks only. It does not start a live
PrefixBench collection.
