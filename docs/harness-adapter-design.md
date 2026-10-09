# Harness adapter design

## Problem

`CompletionController` currently accepts `RunState`, `LoopOptions`, proposal fields, and
Docker-shaped isolation evidence as separate arguments. `EvidenceLoop` must coordinate
those arguments with candidate snapshotting and cleanup. This binds the completion protocol
to Dharness runtime objects and exposes the isolation transaction in the caller. The adapter
must keep the existing snapshot, isolated checks, source attestation, and cleanup operation
atomic because candidate identity does not exist before the snapshot.

## Usage

A caller creates one adapter for one completion proposal. The controller reads the adapter's
frozen view and invokes one isolated verification operation.

```python
adapter = StubHarnessAdapter(
    view=view,
    candidate_files={"answer.txt": b"ready\n"},
    checks={"answer": lambda files: files["answer.txt"] == b"ready\n"},
)
controller = CompletionController(adapter)

admission = controller.admit(now=1.0)
run = await controller.verify(admission)
acceptance = controller.evaluate_accept(run, state=verifying_state, now=2.0)
completion = controller.evaluate_complete(
    acceptance,
    assessment=None,
    state=verifying_state,
)

assert completion.accepted
assert controller.authorizes(completion.permit, verifying_state)
```

Dharness captures the mutable loop data once, then passes the adapter to the same controller.

```python
adapter = DharnessAdapter.capture(
    contract=contract,
    options=options,
    state=state,
    decision=decision,
    isolation=completion_isolation,
    execute_check=execute_check,
)
controller = CompletionController(adapter)
```

`EvidenceLoop` still owns phase changes, budget counter mutations, semantic review, repair,
and journal events during this migration.

## Shape

`HarnessAdapter` has two members:

```python
class HarnessAdapter(ABC):
    @property
    @abstractmethod
    def view(self) -> CompletionTransactionView: ...

    @abstractmethod
    async def run_checks_isolated(self) -> IsolatedCheckRun: ...
```

`CompletionTransactionView` contains the proposal, independent task contract, trace, policy,
and a completion-specific state value. Each adapter captures a deep copy and returns defensive
copies. The controller reads one copy during construction, so later host mutations cannot
change the transaction.

`IsolatedCheckRun` contains the backend snapshot identity, candidate content digest, command
receipts, and isolation proof. Each receipt binds the completion attempt and content digest.
The operation returns only after cleanup. It raises the existing isolation error when the
backend cannot complete cleanup.

The controller validates that the snapshot identity, content digest, attempt, and work epoch
agree with the receipts, isolation proof, and frozen view. It binds admission, acceptance, and
permits to one controller and portable state values. It no longer imports `RunState`,
`CommandRunner`, `RunJournal`, `CompletionIsolation`, Harbor, or Docker.

The interface is small because the adapter hides the candidate lifecycle and execution
backend. Callers still own host state transitions and semantic review because moving them is
not required to establish the portability boundary.

## Synthesis decision

Candidate 2 is the base because it keeps isolation as one atomic operation and gives
`CompletionController` a transaction-scoped dependency. The final design takes canonical
candidate hashing and copy-per-check behavior from candidate 1. It takes the import-boundary
test from candidate 3.

The implementation omits the candidates' new journal schemas, duplicate receipt models,
backend metadata, and test-only production constructors. Existing protocol records remain the
wire format for compatibility.

## Tradeoffs accepted

- We accept an adapter per finish proposal in exchange for a coherent frozen input view.
- We accept continued use of existing receipt records in exchange for avoiding a schema
  migration in this change.
- We accept host-owned semantic review and phase mutation in exchange for a smaller first
  portability boundary.
- We accept candidate identity becoming available only after isolation in exchange for keeping
  snapshot cleanup atomic.

## Alternatives considered

- Seven getter and lifecycle methods expose ordering and cleanup rules to every caller. This is
  a shallow interface and was rejected.
- A controller that snapshots candidates itself still depends on Dharness isolation types and
  splits one failure domain across two modules.
- Moving review, journaling, repair, and all loop state into the adapter would hide more code,
  but it would turn a protocol boundary change into a loop rewrite.

## Open questions and risks

- Should a later change replace the existing Docker-named isolation receipt fields with a
  backend-neutral record?
- Should semantic review move behind a separate protocol interface after the adapter boundary
  has proven stable?

## Next implementation step

Add standalone Stub tests for execution, candidate binding, state mismatch rejection, and
portable import boundaries before changing the controller.
