# Offline mutation reduction and campaign design

## Problem

The mutation package can load a source-bound journal prefix, apply one structured mutation,
and audit the resulting verified trace. It cannot yet remove irrelevant events or run a
repeatable matrix of mutation requests.

`StatePrefix` cannot represent a reduced counterexample. Its events are every physical JSONL
line from 1 through `through_line`, and each `RecordedEvent.line` and `line_sha256` identifies
the original source bytes. Renumbering retained events would break that provenance. Completion
attempts also use event order, so deleting an earlier finish proposal may change later attempt
ordinals.

The next stage needs a sparse trace type, source-anchored witnesses, deterministic reduction,
and campaign result names that do not imply production `EvidenceLoop` execution.

## Usage

Reduce one applied mutation:

```python
from evidence_harness_mutation import (
    MutationId,
    MutationRequest,
    apply_mutation,
    reduce_counterexample,
)

applied = apply_mutation(
    prefix,
    MutationRequest(
        operator=MutationId.CROSS_CANDIDATE_EVIDENCE,
        attempt_ordinal=1,
    ),
)
counterexample = reduce_counterexample(applied)

assert counterexample.provenance.minimality == "1-minimal-under-declared-removals"
assert counterexample.trace.source == prefix.source
assert counterexample.audit.result(applied.expected_invariant).status == "fail"
```

Run a deterministic offline campaign:

```python
from evidence_harness_mutation import (
    MutationId,
    MutationRequest,
    OfflineCampaignOutcome,
    run_offline_campaign,
)

report = run_offline_campaign(
    prefix,
    requests=(
        MutationRequest(operator=MutationId.STALE_EVIDENCE_EPOCH),
        MutationRequest(operator=MutationId.REVIEW_TIMEOUT_FALLBACK),
        MutationRequest(
            operator=MutationId.CROSS_CANDIDATE_EVIDENCE,
            attempt_ordinal=99,
        ),
    ),
)

assert report.summary.scheduled == 3
assert report.cases[-1].outcome is OfflineCampaignOutcome.MUTATION_NOT_APPLICABLE
```

`run_offline_campaign()` calls only the structured operators and
`audit_completion_trace()`. It does not run production `EvidenceLoop` or `EvidenceGate`.

## Shape

`OfflineTrace` is the common input to the oracle:

```python
class OfflineTrace(FrozenModel):
    source: JournalBinding
    through_line: EventOrdinal
    instruction: str
    completion_review_enabled: bool
    events: tuple[RecordedEvent, ...]


class StatePrefix(OfflineTrace):
    # Adds the existing contiguous 1..through_line constraint.
    ...
```

An `OfflineTrace` keeps events in strict source-line order and permits gaps. A
`StatePrefix` remains contiguous. `ReplayGateway` and `apply_mutation()` continue to accept
only `StatePrefix`; only the offline oracle accepts the sparse type.

`AppliedMutation` records three source anchors:

- the selected finish proposal;
- its verified terminal when present;
- the replaced source event and its before/after event types.

An anchor contains the physical line and original line SHA-256. Attempt ordinals remain
diagnostic values and are not reduction identity.

The reducer exposes one operation:

```python
def reduce_counterexample(mutation: AppliedMutation) -> ReducedCounterexample: ...
```

It derives a witness from the complete mutated trace. The witness contains the source
proposal and terminal anchors, mutation target, expected invariant, and exact ordered
violation details. Every accepted candidate must:

- retain the proposal, terminal, and target anchors;
- keep the target inside the same event-order attempt;
- end that attempt with the same verified terminal;
- reproduce the same invariant and exact detail tuple;
- produce the same audit in three consecutive runs.

The reducer first removes event batches, then individual events, then fields and list items
from the mutation target payload. It alternates individual event and payload passes until
neither accepts a deletion. The result is 1-minimal only for these declared deletion
operations. It is not a global minimum and does not simplify scalar values.

The campaign exposes one operation:

```python
def run_offline_campaign(
    prefix: StatePrefix,
    *,
    requests: tuple[MutationRequest, ...] | None = None,
) -> OfflineCampaignReport: ...
```

The default schedule is every current operator for every projected completion attempt. If
the prefix has no completion attempt, attempt 1 is still scheduled so non-applicability is
visible. Explicit requests are sorted by attempt ordinal and enum declaration order;
duplicates remain separate rows.

Each row has one of these offline outcomes:

- `mutation_not_applicable`: the operator precondition did not hold;
- `offline_invalid`: mutation, audit, or reduction could not produce a valid deterministic
  case;
- `oracle_equivalent`: the complete offline audit did not change;
- `offline_violation`: the mutation introduced the expected invariant violation on the
  source-anchored attempt;
- `other_oracle_change`: the audit changed without introducing that expected violation.

A pre-existing violation on the target attempt is never credited to the mutation. Only
`offline_violation` rows contain reduced counterexamples.

## Synthesis decision

Candidate A supplies the base shape: separate sparse and contiguous trace types, enriched
mutation provenance, a fixed three-run witness predicate, and discriminated campaign rows.

The final design keeps the current `InvariantViolation` model, as proposed by candidate B.
The reducer can map violations back to source attempts through terminal lines and internal
attempt projection, so changing the shared audit schema is unnecessary. Every candidate is
constructed through `model_validate()` rather than unchecked `model_copy(update=...)`.

The campaign adopts candidate C's baseline rule: an offline violation is new only when the
same invariant did not already fail on the anchored source attempt. Malformed or
nondeterministic cases become typed `offline_invalid` rows, preserving the complete
schedule.

The restored-target control from candidate C is not included. Restoring the complete source
event after payload reduction changes the mutation and every deleted payload field at once.
That is not a single-variable control. Exact source anchors, target event type, invariant,
detail tuple, and repeated audits provide the version-one cause-preservation contract.

## Tradeoffs accepted

- A second trace type preserves the strict physical meaning of `StatePrefix`.
- Three audits per accepted reduction candidate cost more CPU but make the reproducibility
  requirement explicit.
- Exact detail strings prevent cause switching now, but future structured reason codes will
  require a schema revision.
- Deletion-only reduction is deterministic and testable but does not find a global minimum.
- Sequential campaign execution favors stable ordering over throughput.
- Offline outcome names are less familiar than `killed` and `survived`, but they state what
  this runner actually observes.

## Alternatives considered

Relaxing `StatePrefix` would let sparse traces enter APIs that assume a physical prefix.
Renumbering events would break source coordinates and line hashes. Both alternatives were
rejected.

A public generic delta-debugging API would force callers to define source anchors, repeated
audits, malformed candidate handling, and cause equality. The helper remains private inside
`reducer.py`.

Merging this campaign with Historical-7 would mix offline oracle results with archived
production execution. The two experiment families remain separate.

## Open questions and risks

- Should invariant detail strings become stable reason codes before PrefixBench is frozen?
- Will a later operator need more than one mutation target?
- Should PrefixBench store reduced traces inline or by content digest?

## Implementation status

Implemented in `model.py`, `operators.py`, `reducer.py`, and `campaign.py`. Behavioral tests cover
all four operators, source-anchor preservation, deterministic shrinking, explicit request
ordering, duplicate retention, non-applicability, equivalent audits, pre-existing violations,
nondeterministic baselines, and canonical byte stability.

The next experiment step is to construct PrefixBench and run this offline campaign over its
frozen development split.
