# Receipt-aware completion control

## Problem

The current completion path reviews proposed checks before they execute. After
`max_completion_reviews` calls, it skips review and can accept the next passing
check set. This turns a resource limit into a semantic-review bypass. The loop
also has no reserved executor decisions for finalization, counts failed change
commands as progress, and grants one more repair than `max_repairs`.

The canonical 89-task traces make both failure modes measurable. Ten externally
failing tasks ended as internally `verified`, while ten other tasks exhausted
their turn budget. The implementation must improve these controller behaviors
without task-specific rules.

## Usage

The caller remains unchanged:

```python
report = await EvidenceLoop(
    model=model,
    journal=journal,
    options=LoopOptions(),
).run(instruction, environment)
```

With completion review enabled, `verified` now means:

1. the proposed checks and requirement mapping passed deterministic validation;
2. the checks executed successfully in the current work epoch; and
3. the semantic reviewer accepted those executed receipts.

Exhausting or losing the required reviewer ends the run without reporting
`verified`. Explicit `enable_completion_review=False` remains a supported
mechanical-only mode for compatibility.

## Shape

`EvidenceLoop` owns one receipt-first completion transaction:

```python
async def _handle_finish(state, runner, decision):
    validate_proposal()
    require_review_capacity_when_enabled()
    receipts = await execute_checks()
    if mechanical_evidence_failed(receipts):
        reject_or_stop()
        return
    assessment = await review_executed_receipts_when_enabled()
    evidence = gate.decide(receipts, assessment)
    accept_or_reject(evidence)
```

`EvidenceGate.decide` is the sole acceptance predicate. It requires fresh,
successful receipts and, when configured, an accepted `SemanticAssessment`.
The assessment is a domain value rather than the model-facing
`ReviewDecision`.

`RunState.completion_findings` stores the latest unresolved completion findings.
The journal keeps the full history. The executor and the next reviewer receive
the live findings, so prompt compaction cannot silently remove the current
completion contract.

Finalization is a controller-enforced, sticky phase derived from both existing
budgets. It begins when three executor decisions remain or when 10% of the wall
time remains. The turn reserve is capped so every run gets at least one
unrestricted decision. Before any completion rejection, only `finish` and
`stop` are allowed. After a rejection, one repair `execute` batch may run if one
later decision remains; the following decision must finish or stop. Disallowed
actions do not reach the shell.

The wall-time reserve is an internal policy rather than a public option. Before
finalization, executor model calls and work commands receive a deadline at the
start of the reserved window instead of the run's final deadline. A model call
that times out at this boundary starts a fresh finalization decision instead of
ending as a model-service failure. The controller also rechecks the wall
boundary after each model response and between commands in a batch. `RunState`,
the executor prompt, and `finalization_started` journal events record whether
the turn budget, wall clock, or both triggered the transition.

Productive progress is:

```python
receipt.succeeded and (
    receipt.mode is CommandMode.CHANGE or receipt.observation_fingerprint not in prior_fingerprints
)
```

Repair accounting checks `repair_count < max_repairs` before granting and
incrementing a repair. `max_repairs=N` therefore permits exactly `N` returns to
repair.

## Module map

| Module | Responsibility |
|---|---|
| `budget.py` | Define the shared turn and wall-time finalization policy |
| `protocol.py` | Add finalization state, semantic assessment, active findings, and assessment-bearing evidence |
| `run_loop.py` | Sequence checks before review, enforce budgets and finalization, classify progress, grant repairs |
| `evidence.py` | Enforce the complete mechanical and semantic acceptance conjunction |
| `prompting.py` | Render executed receipts, active findings, and controller-allowed actions |
| `tests/` | Exercise the state machine through scripted model and fake shell boundaries |

No completion coordinator is added. It would need the model, runner, journal,
clock, options, and mutable run state, but would hide no distinct domain.

## Synthesis decision

Three isolated designs converged on receipt-first review, hard review exhaustion,
persistent findings, controller finalization, successful-change progress, and
pre-increment repair admission.

Candidate 1 is the base because it puts the final acceptance conjunction in
`EvidenceGate`, normalizes the model decision into a domain assessment, and
keeps the public interface unchanged. The independent judge scored it 94/100,
ahead of Candidate 2 at 81 and Candidate 3 at 80.

The synthesis takes Candidate 3's sticky finalization sequence, with the reserve
derived internally instead of exposed as two new options. It takes Candidate
2's parameterized repair-limit test, while rejecting that candidate's
post-increment `>=` algorithm because it grants only `N - 1` repairs.

The following ideas are deferred:

- public finalization turn and wall-time settings or an adaptive reserve;
- a new `RunReport.control_reason` field;
- a separate completion coordinator;
- pre-review plus post-review, which doubles model calls;
- deterministic parsing of task semantics in `EvidenceGate`.

## Tradeoffs

- Weak but policy-valid checks may execute before semantic rejection. This lets
  the reviewer assess facts that occurred without adding a second model call.
- Reviewer failure now fails closed. This may convert an internally successful
  artifact into `model_failure`, but it cannot create a false `verified`.
- Three late executor decisions are reserved for finish, one repair batch, and
  a revised finish. This reduces unrestricted work by at most two decisions.
- Ordinary work receives 90% of the wall budget. A command that needs the final
  10% will time out at the finalization boundary unless it is the one permitted
  repair batch.
- The evidence schema gains an optional semantic assessment. Old serialized
  evidence remains readable because the field defaults to `None`.

## Verification

Behavior tests must prove:

1. two rejected reviews cannot be bypassed by a third finish;
2. the reviewer sees actual receipt output before acceptance;
3. finalization blocks late exploration but permits one bounded repair;
4. a wall-clock boundary can start finalization while many turns remain;
5. ordinary model calls and work commands cannot consume the wall-time reserve;
6. a model timeout at the boundary starts a finalization decision;
7. a model response that crosses the wall boundary has its actions rechecked;
8. failed change commands accumulate stagnation;
9. `max_repairs` grants exactly `N` repair cycles for `N` in `0, 1, 2, 4`;
10. review timeout or protocol failure cannot produce `verified`.

The full project gate must pass before this design is considered implemented.
