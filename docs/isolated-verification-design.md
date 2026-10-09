# Isolated completion verification design

## Problem

`EvidenceLoop` currently runs completion checks in the live Harbor task
container. A compiler, test runner, redirect, or imported program can change
the candidate that Harbor later scores. Static shell inspection cannot prove
that a command is read-only.

The replacement must preserve each check's script and absolute working
directory while preventing writes from reaching the live candidate. It must
also preserve the existing `work_epoch`, command sequence, environment-call
budget, receipt fingerprint, journal order, semantic review, and execute-only
replay rules.

Harbor 0.23.0 does not expose an environment snapshot API. The first
implementation therefore supports only Harbor's local Linux
`DockerEnvironment` and fails closed for configurations that Docker commit
cannot reproduce.

## Usage

`EvidenceHarnessAgent` constructs the isolation provider while it still holds
the concrete Harbor environment:

```python
isolation = completion_isolation_for_harbor(
    journal=journal,
    environment=environment,
)
loop = EvidenceLoop(
    model=model,
    journal=journal,
    options=self.options.loop_options(),
    completion_isolation=isolation,
    on_progress=update_context,
)
```

`EvidenceLoop` submits one completion attempt. The provider calls the supplied
executor once for each reached check:

```python
result = await isolation.verify(
    CompletionIsolationRequest(
        attempt_id=attempt_id,
        work_epoch=state.work_epoch,
        checks=decision.checks,
        deadline_monotonic=state.deadline_monotonic,
    ),
    execute_check,
)
```

The callback creates a `CommandRunner` for the isolated child and delegates to
`EvidenceLoop._run_command()`. That method remains the only owner of command
sequence numbers, environment-call accounting, receipts, and observations.

## Shape

### One isolation transaction

`CompletionIsolation.verify()` owns the complete lifecycle:

1. Resolve and inspect one running Compose `main` container.
2. Reject unsupported runtime state before executing a check.
3. Pause the source and hash its canonical `docker diff`.
4. Commit the source once.
5. Start one mount-free, network-disabled child per reached check.
6. Execute the unchanged script at the unchanged `cwd`.
7. Record the child's complete `docker diff`.
8. If Docker reports a changed path, compare its type with an untouched child
   from the candidate image. This distinguishes directory metadata changes
   from a file that the check replaced with a directory.
9. Remove the check child before starting the next check.
10. Confirm that the paused source still has the same diff.
11. Resume the source first, then remove the baseline child and snapshot image.

The public provider has one operation. Callers cannot omit cleanup, reuse a
child, attach a source mount, or copy child state back.

### Evidence

Isolation evidence is separate from `CommandReceipt`. Historical command and
observation fingerprints do not change.

Each check record binds these values:

- the completion attempt, `work_epoch`, and candidate content digest;
- the check identifier;
- the command sequence and observation fingerprint;
- the committed image identifier;
- a hash of the child container identifier;
- the complete filesystem-delta hash;
- bounded added, modified, and deleted path lists; and
- child disposal.

Attempt evidence also records the source diff before and after execution,
whether the source remained paused, source resumption, snapshot disposal, the
host-operation count, child count, and duration.

The candidate content digest is separate from the Docker image ID. After
`docker commit`, the provider reads the image's ordered RootFS layer diff IDs
and computes SHA-256 over a domain separator, the layer count, and each
length-framed diff ID. The image ID remains the runtime identity used to start
children. The content digest is copied into `RunState`, every completion
`CommandReceipt`, and `CompletionIsolationEvidence`, so an image-identity check
cannot substitute for the `Fresh` content-binding dimension.

Each prospective experiment also binds one aggregate SHA-256 over
`pyproject.toml`, `uv.lock`, and every Python module under
`src/evidence_harness`. The committed report lists each member hash, so changes
outside the Docker provider cannot silently reuse an older runtime result.

`VerificationReceipt.isolation` is optional only so schema-1 journals remain
decodable. A live schema-2 completion attempt requires isolation evidence.

### Acceptance

`EvidenceGate` remains the only mechanical acceptance authority. It rejects an
attempt unless:

- `work_epoch`, the attempt identifier, and the candidate content digest match
  the current completion state;
- every reached receipt carries that same candidate content digest;
- isolation evidence carries that same digest while its Docker image ID remains
  the child-launch identity;
- every reached receipt has one matching isolation record;
- each isolation record matches the receipt sequence and observation hash;
- every child started from the same committed image and was removed;
- no pre-existing non-directory was modified or deleted;
- the source diff stayed unchanged while the source was paused;
- the source was resumed and the snapshot image was removed;
- all required checks ran and succeeded; and
- semantic review accepted when review is enabled.

New files in a child are allowed. They are verification-only output and cannot
prove that the frozen candidate already contained the same file.

### Policy

Forbidden benchmark, verifier, credential, Docker socket, and host-control
paths remain hard failures. Empty checks, excessive timeouts, bulk environment
dumps, and display-only checks also remain hard failures.

Static write detection becomes advisory for completion checks because every
accepted check must run through isolation. Unsupported environments fail with
`completion_isolation_unsupported`; they never run a completion check in the
live task container.

### Supported runtime

The first provider accepts only:

- Harbor 0.23.0;
- Harbor's concrete local Linux `DockerEnvironment`;
- one running Compose `main` service;
- a reachable Docker CLI and daemon;
- no task sidecars, devices, GPUs, Docker socket, privileged mode, host
  namespaces, image-declared volumes, or task-defined mounts;
- only known Harbor control mounts, which children omit;
- no agent-started background process;
- non-service completion checks; and
- at most three sequential checks.

Every child uses `--network none`. The provider does not claim to reproduce a
service, sidecar, mounted volume, process-memory, socket, or external-network
state.

### Budgets and ordering

Every reached check consumes one existing environment-call unit through
`_run_command()`. Docker controller operations do not consume model-controlled
environment calls. Isolation evidence records their separate count and
duration.

The provider reserves cleanup time before starting. Each Docker command has a
bounded timeout, and the attempt has a host-operation cap. Normal operations
cannot enter the final 95 seconds. Cleanup has one shared 90-second deadline,
resumes the source before removing temporary resources, and is shielded from
cancellation. An outer deadline also cancels a cleanup task whose Docker client
does not return. If `docker pause` times out, cleanup still sends `unpause` and
then verifies the source state. At most one check child and one untouched
baseline child exist at the same time. Container names and image references are
recorded before their Docker create operations begin, so a client timeout cannot
leave an unowned resource.

The successful schema-2 event order is:

```text
agent_decision(finish)
completion_isolation_started
completion_candidate_committed(candidate_image_id, candidate_digest)
completion_check_started
command_receipt(work_epoch, attempt_id, candidate_digest)
completion_check_isolated
completion_check_disposed
completion_source_attested
completion_source_resumed
completion_snapshot_disposed
completion_review
verification_receipt
run_finished
```

Lifecycle failure completes cleanup and source resumption before
`run_finished(infrastructure_failure)`. `run_finished` stays final.

## Synthesis decision

Candidate A is the base because it fits Harbor's current custom-agent
invocation and preserves absolute paths without a copy-back operation.

The design uses Candidate B's one-method `DockerCli.run()` port and explicit
setup, child, and cleanup limits. It uses Candidate D's one-check-exec rule,
monotonic attempt allocation, operation count, and support census. The
prospective experiment runner uses Candidate E's source bindings, but the
production agent does not adopt E's custom `Trial` or file-transfer runtime.

The design rejects a second source commit, public child networking, read-only
task-bind reconstruction, and shared children. These features enlarge the
fidelity claim without helping the three frozen calibration candidates.

## Tradeoffs accepted

- We accept a Docker-only implementation in exchange for exact candidate paths
  and a true no-copy-back boundary under the current Harbor invocation.
- We accept one child start per check in exchange for independent receipts.
- We accept fail-closed behavior for stateful tasks in exchange for evidence
  that does not overstate snapshot fidelity.
- We accept temporary source pause time in exchange for a stable candidate
  filesystem and a direct source-drift attestation.
- We accept child additions in exchange for running real compilers and test
  programs. The reviewer sees those additions and cannot count them as
  submitted artifacts.

## Alternatives considered

### Fresh Harbor environment plus file transfer

This design is provider-neutral but needs an environment factory or custom
`Trial`. The current `harbor run --agent` path passes one already-started
environment to the agent, so the agent cannot construct this runtime without
changing the evaluation entrypoint.

### Scratch copy inside the source container

This design preserves ordinary relative paths but not absolute `/app` or
`/workspace` references. A check can also escape the copied directory and
write to the scored environment.

### Shared snapshot child

One child for all checks is cheaper, but later checks can consume output from
earlier checks. That weakens receipt independence and can hide incomplete
candidate state.

## Verified boundaries and remaining risks

- The static census found no source-level rejection among the 89 tasks. It did
  not start those task containers, so runtime support remains unproved for 86
  tasks. CI reconstructs the census with the production rejection function from
  a committed minimal source snapshot bound to the dataset commit and matrix
  hash; it does not accept the committed report as evidence for its own result.
- The Docker smoke proves that one check can write a temporary file in its
  child while the live source diff remains unchanged.
- Three frozen completion candidates exercise two checks each after a full
  command replay. Their report records replay count, check result, filesystem
  delta, source attestation, cleanup, semantic review status, and official
  reward. All three received official reward 1.0; two passed the mechanical
  isolation gate; none ended as internally `verified`.
- `fix-ocaml-gc` is the mechanical failure. Its structural check passed, but a
  frozen check for exact test-summary text returned nonzero. The official
  verifier still passed, so this trial records an internal/external
  disagreement rather than an isolation failure.
- A task with a sidecar, a task mount, an image-declared volume, a service
  check, or an agent-started background process still fails closed.

The generated evidence is in
[completion-isolation-support.md](completion-isolation-support.md) and
[completion-isolation-experiments.md](completion-isolation-experiments.md).
Neither report changes the canonical 59/89 score.
