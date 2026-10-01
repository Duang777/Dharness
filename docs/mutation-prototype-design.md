# Stateful mutation prototype design

## Problem

The prototype must replay model-facing journal events and audit completion outcomes without
reusing the production `EvidenceGate`. Schema-2 journals mix globally sequenced command receipts
with unsequenced model, review, isolation, and terminal events. A completion review has no
`attempt_id`, while a verification receipt carries one only inside optional isolation evidence.
The design must preserve event order and exact source provenance.

## Usage

Load a source-bound prefix from exact journal bytes:

```python
from evidence_harness_mutation import load_state_prefix

prefix = load_state_prefix(
    journal_bytes,
    source_commit="289bea40d10e0e94b98b1247617d06a12fc46f52",
    expected_journal_sha256=manifest["journal_sha256"],
    through_line=42,
)
```

`through_line` is a one-based physical JSONL line. It is not a command receipt sequence because
most journal events have no sequence field. The digest always covers the full journal, including
the suffix after the selected prefix.

Replay recorded model calls through the production `ModelGateway` protocol:

```python
from evidence_harness.protocol import ModelGateway
from evidence_harness_mutation import ReplayGateway

gateway: ModelGateway = ReplayGateway.from_prefix(prefix)
decision = await gateway.decide("ignored during deterministic replay")
review = await gateway.review("ignored during deterministic replay")
gateway.assert_exhausted()
```

Audit a complete or mutated trace:

```python
from evidence_harness_mutation import (
    InvariantId,
    MutationId,
    MutationRequest,
    apply_mutation,
    audit_completion_trace,
)

mutation = apply_mutation(
    prefix,
    MutationRequest(operator=MutationId.CROSS_CANDIDATE_EVIDENCE),
)
report = audit_completion_trace(mutation.trace)
assert report.result(InvariantId.I4).status == "fail"
```

Run all seven frozen Historical-7 defect pairs against their production revisions:

```bash
uv run python scripts/run_historical7.py
```

This writes `evaluation/historical-7.json`. The command exits 0 when all 14 cells match, 1 for a
behavior mismatch, and 2 for a source or infrastructure error. The manifest fixes the scenarios,
commits, trees, properties, and expected outcomes at
`experiments/historical-7/manifest.json`; the CLI does not accept overrides for them.

Build and verify the current PrefixBench readiness census:

```bash
uv run python scripts/prefixbench.py build
uv run python scripts/prefixbench.py check
```

The report is `evaluation/prefixbench-readiness.json`. It freezes the outcome-blind task split and
records source exclusions. It is not a runnable five-phase benchmark.

## Shape

```text
src/evidence_harness_mutation/
  __init__.py
  historical.py
  model.py
  journal_loader.py
  replay.py
  attempts.py
  invariants.py
  operators.py
  reducer.py
  campaign.py
  prefixbench.py
scripts/
  historical_mutation_probe.py
  run_historical7.py
  prefixbench.py
```

`model.py` owns immutable provenance and result models. `journal_loader.py` validates the wire
boundary and emits ordered `RecordedEvent` values. `replay.py` implements one ordered model-call
tape. `attempts.py` owns completion-attempt association by event order. `invariants.py` evaluates
I1-I4 as pure functions, while `operators.py` applies deterministic structured mutations.
`reducer.py` owns source-anchored event and payload deletion. `campaign.py` owns deterministic
offline scheduling, classification, reduction, and canonical report serialization.
`historical.py` owns the fixed Historical-7 transaction: manifest validation, Git archives,
revision-isolated probes, outcome classification, and canonical report output.
`prefixbench.py` validates the canonical queue and raw result identity, fixes task-level split
membership, inventories explicit and legacy events separately, and emits the typed readiness
report.

The loader:

- hashes the complete input before selecting a prefix;
- requires a caller-supplied full Git object ID and expected journal SHA-256;
- accepts schema 2 only;
- rejects blank lines, malformed envelopes, duplicate starts, and invalid cutoffs;
- preserves unknown events so later operators do not lose context.

`ReplayGateway` consumes canonical `agent_decision` and `completion_review` events in their original
order. A wrong call kind, over-consumption, or leftover event raises `ReplayContractError`.
`model_decision` telemetry is ignored because it is not the controller-accepted protocol value.

The oracle opens an attempt on an `agent_decision` whose action is `finish`. The next
`agent_decision` or `run_finished` closes that window. Event order is the only valid way to attach a
review to an attempt. Explicit attempt IDs and candidate image IDs are cross-checks, not join keys.

- I1 requires the accepted verification receipt, every check receipt, and isolation evidence to
  match the attempt's recorded work epoch.
- I2 requires the proposed, executed, and embedded check receipts to be complete, ordered, equal,
  and successful.
- I3 requires one accepting review after check execution and before verification when review is
  enabled. A missing, early, rejected, or failed review does not count.
- I4 requires the isolation start, candidate commit, check lifecycle events, and verification
  evidence to bind the same attempt and candidate image.

The oracle imports protocol data types but never imports `EvidenceGate` or `EvidenceLoop`.

The first four operators reuse the same attempt projection as the oracle:

- `stale_evidence_epoch` binds an internally consistent receipt to the preceding epoch;
- `reorder_check_receipts` changes the embedded receipt order;
- `review_timeout_fallback` replaces an accepted review with a recorded timeout;
- `cross_candidate_evidence` binds internally consistent evidence to a different candidate image.

Each application records the target line, its original SHA-256, and the expected invariant.

The Historical-7 worker runs once per cell under `python -I`. It inserts only the archived
revision's `src` directory before importing production code and rejects any `evidence_harness`
module loaded from elsewhere. Six cases call the archived `EvidenceLoop.run`; the receipt-order
case calls the archived `EvidenceGate.validate_proposal` and `EvidenceGate.decide`. Expected
outcomes remain in the parent manifest model and are not sent to the worker. The parent and worker
independently hash `pyproject.toml`, `uv.lock`, and every `src/evidence_harness/**/*.py` file.
Each result binds that source set to the commit, tree, embedded archive commit, manifest, worker,
coordinator, and schema module.

## Synthesis decision

Candidate A is the base because it returns an explicit pass, fail, or not-applicable result for
every invariant and specifies deep-copy behavior for replayed protocol values. Candidate B
contributed the provenance-preserving `ReplayGateway.from_prefix()` constructor, full-journal
suffix hash test, independent gateway test, and package build test.

Both candidates proposed an ordered event ledger and rejected `RunState` rehydration. The current
journal cannot reconstruct every internal transition, including an epoch increment followed by a
policy rejection with no command receipt. The oracle therefore treats
`completion_isolation_started.work_epoch` as the authoritative completion-attempt epoch.

## Tradeoffs accepted

- The package duplicates I1-I4 predicates so it can detect defects in the production gate.
- The first version rejects schema 1 rather than inventing isolation semantics for legacy traces.
- The caller supplies the Git commit because a journal cannot prove which source tree produced it.
- Replay reports call counts but cannot reconstruct token or cost usage from canonical events.
- The offline campaign does not resume `EvidenceLoop` or claim production killed/survived outcomes.

## Alternatives considered

Directly calling `EvidenceGate` would make the oracle agree with the implementation by
construction. Rehydrating `RunState` would require new production hooks and still could not recover
unrecorded transitions. Exposing public completion-attempt aggregates would enlarge the API before
there is a second consumer.

## Current status

The deterministic replay, I1-I4 oracle, four structured operators, source-anchored reducer,
offline campaign, and all seven Historical-7 pairs are implemented:

| Historical property | Vulnerable result | Fixed result |
|---|---|---|
| Review timeout fails closed | `survived` at `ba2cbae` | `killed` at `5f64d66` |
| Receipts match proposal order | `survived` at `6f74c19` | `killed` at `289bea4` |
| Review sees executed receipts | `survived` at `ba2cbae` | `killed` at `5f64d66` |
| Review quota fails closed | `survived` at `ba2cbae` | `killed` at `5f64d66` |
| Failed changes do not count as progress | `survived` at `ba2cbae` | `killed` at `5f64d66` |
| Repair budget counts granted repairs | `survived` at `ba2cbae` | `killed` at `5f64d66` |
| Work preserves finalization time | `survived` at `5f64d66` | `killed` at `3a3b83f` |

The canonical report is `evaluation/historical-7.json`. Repeated runs must produce identical
bytes. The separate offline campaign schedules every operator and attempt deterministically,
reports invalid and equivalent cases explicitly, and emits a reduced counterexample only for a
new expected invariant violation.

The PrefixBench readiness census is also implemented. Its current result is:

- 89 tasks: 85 live and four replay;
- 28 development tasks and 61 test tasks under the frozen task hash;
- zero admitted tasks;
- zero explicit `thinking`, `executing`, `finalizing`, `reviewing`, or `recovering` witnesses;
- status `source_cohort_unavailable`.

The 85 live schema-1 journals remain inventory evidence only. A runnable PrefixBench still requires
producer source attestation, explicit phase events, a frozen collection profile, canonical journal
bindings, and a new schema-2 collection. Cross-model evaluation remains future work.
