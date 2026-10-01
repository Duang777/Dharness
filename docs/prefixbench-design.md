# PrefixBench readiness design

## Purpose

PrefixBench will test state-aware mutations at five controller phases:

1. thinking
2. executing
3. finalizing
4. reviewing
5. recovering

The current Terminal-Bench 2.0 corpus cannot support that experiment. The frozen
`canonical-89.json` manifest contains 85 live Evidence Harness runs and four replay results, but
the live journals use schema 1. They do not bind the journal to the canonical manifest or attest
the producer source revision. They also lack the explicit phase-entry events required to select a
prefix without inferring hidden controller state.

The first PrefixBench artifact is therefore a readiness report, not a benchmark dataset. It records
which source facts exist, fixes the future task split, and states why no current task is admitted.

## Two views

The readiness report keeps two views separate.

The phase inventory counts explicit phase-entry events and selected legacy completion signals in
the current journals. Inventory counts describe the recorded corpus only. A legacy event is never
used as a substitute for a phase-entry witness.

The runnable completion cohort will eventually contain source-attested schema-2 journals with a
PrefixBench collection profile and complete phase evidence. Only that cohort may produce
mutation-survival or invariant-detection results.

The readiness artifact has status `source_cohort_unavailable` while its admitted count is zero.
It must not be cited as a five-phase benchmark result.

## Source requirements

The builder validates the following facts before inspecting events:

- canonical and matrix schemas are version 1;
- both files name the same dataset;
- the canonical manifest binds the exact matrix SHA-256;
- canonical task order matches matrix task order;
- canonical indices are contiguous from 1;
- each result path is relative, remains under `runs/terminal-bench-2`, and resolves inside the
  project root;
- result and config bytes match their canonical SHA-256 values;
- result task name, task checksum, task Git URL, task Git commit, reward, status, and selected
  config identity fields match the canonical row;
- `config.json` matches the config embedded in `result.json`.

The current canonical schema has no journal binding or producer source attestation. The readiness
report records those as exclusions instead of inventing either value from the current checkout.

## Phase contract

Future collection must emit these explicit witnesses:

| Phase | Required event |
|---|---|
| thinking | `executor_turn_started` |
| executing | `work_batch_started` |
| finalizing | `finalization_started` |
| reviewing | `completion_review_started` |
| recovering | `recovery_required` |

The builder counts only these event types as phase evidence. It separately reports four legacy
signals:

- finish proposals, derived from `agent_decision.payload.action == "finish"`;
- `completion_review`;
- `completion_rejected`;
- `replanned`.

No nearest-event rule, timestamp window, or payload heuristic may turn a legacy signal into a phase
witness.

## Admission

A live task is excluded until all of these conditions hold:

- the canonical row binds the journal bytes;
- the journal uses schema 2;
- the producer commit and source tree are attested;
- the run declares the frozen PrefixBench collection profile;
- all five phase-entry events exist.

Replay results receive the single exclusion `non_live_result`. Current live results receive:

- `journal_not_bound_by_canonical`;
- `unsupported_journal_schema`;
- `producer_commit_unattested`;
- `prefixbench_profile_missing`;
- `phase_evidence_incomplete`.

The list is evidence, not a priority chain. The builder reports every applicable reason.

## Frozen task split

Development and test membership is outcome-blind and stable across future collections. For each
task, the builder joins these identity fields with NUL separators:

```text
prefixbench-task-split-v1
dataset
task name
task checksum
task Git URL
task Git commit
```

It hashes the resulting UTF-8 bytes with SHA-256, interprets the first eight digest bytes as an
unsigned big-endian integer, and takes the value modulo 10. Buckets 0, 1, and 2 are development;
buckets 3 through 9 are test.

The split does not use reward, Harness stop reason, journal events, failure labels, or mutation
outcomes. Quotas must be frozen against this task-level assignment. A later run of the same task
cannot move between splits.

## Artifact and CLI

`scripts/prefixbench.py build` writes `evaluation/prefixbench-readiness.json` atomically.
Serialization uses ASCII JSON, two-space indentation, sorted keys, and one trailing newline.

`scripts/prefixbench.py check` is read-only. It:

1. parses the committed artifact into the typed readiness model;
2. requires the file bytes to equal the model's canonical bytes;
3. verifies canonical and matrix file bindings;
4. rebuilds and compares the report when every bound raw result, config, and journal exists;
5. accepts an environment where none of those raw files exists;
6. rejects partial raw-source availability.

This policy lets a source checkout verify the committed artifact without shipping private run
outputs, while a collection checkout proves exact reproducibility.

## Live collection support

The collection path is now implemented:

1. `prefixbench-v1` freezes controller and model-generation options;
2. the launcher and Agent compare imported runtime files with `git archive HEAD`;
3. schema-2 journals record producer attestation and five explicit phase-entry events;
4. profile-mode collection binds result, config, and journal bytes in canonical schema 2;
5. readiness v2 separates source admission from semantic phase eligibility and applies the
   development coverage policy.

The implementation contract and commands are in
[`prefixbench-collection-design.md`](prefixbench-collection-design.md). No live PrefixBench
collection has been run yet. The next operational step is to commit a clean producer revision,
run the development split, and review its phase coverage before freezing mutation quotas or
touching the test split.

Schema-1 conversion is not allowed because it would assign phase semantics and source provenance
that the original records did not contain.
