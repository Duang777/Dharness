# Contract-bound completion control

## Problem

The receipt-aware path prevents review failures, stale checks, and failed
commands from producing `verified`. It previously accepted the agent's
`RequirementCoverage` rows as the complete requirement set. An agent could omit
a requirement, and only the semantic reviewer could detect the omission.

The completion decision also lacked one object that joined the evidence,
budget, phase, and review decisions. Those checks existed in separate branches
of `EvidenceLoop`, which made the acceptance formula hard to audit.

## Usage

The default caller remains unchanged. `EvidenceLoop` freezes the full
instruction as requirement `REQ-1` and permits every `CheckKind` for that root
requirement:

```python
report = await EvidenceLoop(
    model=model,
    journal=journal,
    options=LoopOptions(),
).run(instruction, environment)
```

Callers that have structured requirements can supply a narrower contract:

```python
contract = CompletionContract.create(
    requirements=(
        TaskRequirement(
            id="artifact",
            statement="answer.txt exists",
            evidence_kinds=(CheckKind.ARTIFACT,),
        ),
        TaskRequirement(
            id="behavior",
            statement="the command prints ready",
            evidence_kinds=(CheckKind.BEHAVIOR,),
        ),
    ),
    options=options,
)
loop = EvidenceLoop(
    model=model,
    journal=journal,
    options=options,
    completion_isolation=isolation,
    completion_contract=contract,
)
```

The executor prompt includes each requirement ID, statement, and allowed
evidence kind. `RequirementCoverage.requirement` carries the ID. The
`EvidenceGate` rejects missing IDs, unknown IDs, duplicate IDs, and evidence
kind mismatches before it executes completion checks.

## Shape

`CompletionContract` records the four contract sets:

| Field | Meaning |
|---|---|
| `e_req` | Fixed requirement IDs, statements, and allowed `CheckKind` values |
| `o_req` | The required execution order for proposed checks |
| `v_req` | Freshness, success, integrity, process binding, and isolation rules |
| `b_req` | Turn, environment-call, repair, recovery, review, wall-time, and verification-reserve limits |

`CompletionController` evaluates the completion transaction:

```python
async def _handle_finish(state, runner, decision):
    controller.admit_proposal(...)
    receipts = await execute_checks()
    if not controller.evaluate_evidence(...).accepted:
        reject_or_stop()
        return
    assessment = await review_executed_receipts_when_enabled()
    acceptance = controller.evaluate_accept(...)
    completion = controller.evaluate_complete(acceptance, assessment=assessment)
    finish_with_controller_permit(completion)
```

The controller keeps each predicate separate:

| Predicate | Owner |
|---|---|
| Coverage, Type, Fresh, Integrity, ProcessBound, Order | `EvidenceGate` |
| BudgetBound | `BudgetGuard` |
| PhaseOK | `PhaseGuard` |
| ReviewOK | `ReviewGate` |

`AcceptEvaluation` is the conjunction of the evidence, budget, and phase
results. `CompletionEvaluation` adds the review result. Each result retains its
own rejection reasons.

Only an accepted `CompletionEvaluation` contains a private completion permit.
`EvidenceLoop._finish(..., StopReason.VERIFIED)` checks the permit and calls
`phase_ok` again. A run with review enabled can complete only from `REVIEWING`.
A run with review disabled can complete only from `VERIFYING`.

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
| `completion_contract.py` | Freeze `E_req`, `O_req`, `V_req`, and `B_req` |
| `completion_control.py` | Compose the evidence, budget, phase, and review guards |
| `run_loop.py` | Execute the transaction and require a controller permit for `verified` |
| `evidence.py` | Check coverage, type, freshness, integrity, process binding, and order |
| `prompting.py` | Render the contract, receipts, findings, and allowed actions |
| `control_invariants.py` | Audit I1-I8 from a schema-2 journal |
| `control_operators.py` | Apply deterministic I1-I8 mutations |

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

The following ideas remain out of scope:

- public finalization turn and wall-time settings or an adaptive reserve;
- a new `RunReport.control_reason` field;
- pre-review plus post-review, which doubles model calls;
- automatic semantic decomposition of free-text instructions.

## Control audit

New runs retain journal schema 2 and add `control_audit_version=1`. The
`run_started` event stores the complete frozen contract. The runtime also writes
these control facts:

- `work_batch_admission` records the reserve calculation before normal work;
- `work_batch_finished` records successful and failed progress inputs;
- `completion_proposal_admission` records each proposal guard result;
- `completion_guard_result` records the four final guard results;
- `repair_admission` records the count before and after a repair decision.

`audit_control_trace` returns one `ControlAuditReport` with I1-I8. It translates
the frozen I1-I4 auditor results and evaluates I5-I8 from the new facts. A
schema-2 journal without `control_audit_version=1` reports I6-I8 as
`unsupported`; it never reports those invariants as passed.

For control-audit v1, the auditor also binds work batches to their raw command
receipts, checks monotonic work epochs, requires an accepted
`completion_guard_result` before `verified`, and compares recorded repair and
review limits with the frozen contract. It does not treat self-reported summary
fields as independent evidence.

`apply_control_mutation` exposes one deterministic mutation for each invariant.
The I1-I4 operators delegate to the frozen PrefixBench v1 operators. The I5-I8
operators mutate only control-audit v1 facts.

The committed PrefixBench v1 protocol and its I1-I4 implementation remain
unchanged. Runtime changes after that experiment intentionally fail its live
producer preflight. The new control audit is a separate version, so it does not
rewrite frozen experiment inputs or outcomes.

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

1. a finish proposal cannot omit a fixed contract requirement;
2. a check kind must match every requirement that the check covers;
3. every Guard reports its own rejection reasons;
4. an illegal phase cannot produce a completion permit;
5. `_finish` rejects `VERIFIED` without a current permit;
6. two rejected reviews cannot be bypassed by a third finish;
7. failed change commands accumulate stagnation;
8. `max_repairs` grants exactly `N` repair cycles for `N` in `0, 1, 2, 4`;
9. the control auditor accepts a valid I1-I8 trace;
10. each I1-I8 mutation fails its target invariant.

The full project gate must pass before this design is considered implemented.
