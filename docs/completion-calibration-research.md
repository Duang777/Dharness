# Completion verification and calibration

Research date: 2026-09-27. Repository links below are pinned to the inspected
commit unless the source is an official product document.

## Executive findings

1. Mature agents separate the *decision to stop* from ordinary task execution,
   but their completion gates differ in strength. OpenHands uses an outside
   controller and a second LLM; Claude Code exposes blocking completion hooks;
   SWE-agent stages submission and can run a separate reviewer. None of these
   mechanisms alone proves the workspace satisfies an external grader.
2. Workspace snapshots and sandboxes solve different problems. Cline preserves
   recoverable state, while Codex constrains writes. For Evidence Harness,
   completion checks should execute in a disposable writable copy so compilers
   and test runners can behave normally without changing the candidate.
3. The reviewed implementations do not contain a general, authoritative
   classifier for "tests that write." Aider accepts an arbitrary test command,
   Codex delegates write enforcement to its sandbox, and SWE-bench detects a
   tracked diff after evaluation but only logs it. Static command matching is
   therefore suitable as an early warning, not as proof of purity.
4. SWE-bench supplies the cleanest calibration pattern: evaluate a submitted
   patch in a separate container and derive the external label from specified
   fail-to-pass and pass-to-pass tests. Evidence Harness can join that label to
   its internal completion receipt without exposing grader internals during the
   run.

## Observed implementations

### Completion is a separate control point

- **OpenHands Software Agent SDK.** `run_goal` lets an inner conversation finish,
  then calls `GoalController.on_run_finished`; the controller invokes
  `judge_goal` and either sends a follow-up or returns `complete`/`capped`.
  `judge_goal` receives only the objective and LLM-convertible transcript events,
  excludes system messages, and returns an incomplete verdict when its response
  cannot be parsed. This is genuine control-plane separation, but not independent
  workspace verification because the judge has no workspace input.
  Sources: [`run_goal`](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/goal/runner.py#L30-L61),
  [`GoalController.on_run_finished`](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/goal/controller.py#L78-L132),
  [`judge_goal` and `_parse_verdict`](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/goal/judge.py#L43-L121).

- **Claude Code.** User-defined `TaskCompleted` hooks can run tests and block
  completion with exit code 2. A `Stop` hook can return `decision: "block"` and
  feed a reason back into the conversation; repeated continuation is bounded by
  an eight-consecutive-continuation cap. These are extension points rather than a
  built-in correctness oracle: the configured hook owns the check semantics.
  Source: [official hooks reference, `TaskCompleted` and `Stop`](https://code.claude.com/docs/en/hooks#taskcompleted).

- **SWE-agent.** `tools/review_on_submit_m/bin/submit` materializes the staged
  patch and intercepts submission stages. Its default submission message asks the
  agent to rerun its reproduction, remove that script, revert test-file edits,
  and submit again. Separately, `Reviewer.review` scores a submission and
  `ScoreRetryLoop` controls retries using score, attempt, acceptance, and cost
  limits. These mechanisms add review boundaries, but the review score remains
  an internal signal.
  Sources: [`submit`](https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/tools/review_on_submit_m/bin/submit#L13-L45),
  [default staged-review prompt](https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/config/default.yaml#L44-L61),
  [`Reviewer` and `ScoreRetryLoop`](https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/sweagent/agent/reviewer.py#L375-L638).

### Workspace preservation and check isolation

- **Cline.** At the inspected commit, `createUntrackedParentCommit` uses a
  separate `GIT_INDEX_FILE` to capture non-ignored untracked files without
  touching the real index. `createWorktreeStashCommit` combines that tree with a
  stash-compatible tracked-state commit. Before destructive restoration,
  `beginWorktreeRestoreTransaction` takes an include-untracked snapshot behind a
  private ref. `applyCheckpointToWorktree` refuses restoration after `HEAD`
  moves and uses compare-and-swap `update-ref` before resetting. This is strong
  recovery machinery, not automatic verification isolation. Ignored files are
  deliberately outside its rewind behavior.
  Sources: [`createUntrackedParentCommit` and `createWorktreeStashCommit`](https://github.com/cline/cline/blob/252082b9e93b4f91253876391e35b4c13326f5e6/sdk/packages/core/src/hooks/checkpoint-hooks.ts#L249-L426),
  [`createCheckpointHooks`](https://github.com/cline/cline/blob/252082b9e93b4f91253876391e35b4c13326f5e6/sdk/packages/core/src/hooks/checkpoint-hooks.ts#L497-L710),
  [`beginWorktreeRestoreTransaction`](https://github.com/cline/cline/blob/252082b9e93b4f91253876391e35b4c13326f5e6/sdk/packages/core/src/session/checkpoint-restore.ts#L50-L159),
  [`applyCheckpointToWorktree`](https://github.com/cline/cline/blob/252082b9e93b4f91253876391e35b4c13326f5e6/sdk/packages/core/src/session/checkpoint-restore.ts#L376-L477).

- **Codex.** `SandboxPolicy::new_read_only_policy` denies disk writes and network
  access. The execution policy still recognizes dangerous commands, but for
  non-dangerous unmatched commands in restricted modes it explicitly relies on
  the sandbox to enforce restrictions. This supports a general lesson: parse
  commands for policy and diagnostics, but enforce write boundaries at the
  filesystem layer.
  Sources: [`SandboxPolicy::new_read_only_policy`](https://github.com/openai/codex/blob/18344a972ddf06aa0bb80773bfb7564576e8991b/codex-rs/protocol/src/protocol.rs#L1204-L1219),
  [restricted execution-policy decision](https://github.com/openai/codex/blob/18344a972ddf06aa0bb80773bfb7564576e8991b/codex-rs/core/src/exec_policy.rs#L780-L855),
  [official security documentation](https://developers.openai.com/codex/agent-approvals-security#sandbox-and-approvals).

### External grading and mutation evidence

- **SWE-bench.** `run_instance` creates a container, applies the submitted patch,
  runs `/eval.sh`, and records `git diff` before and after evaluation. A changed
  tracked diff is logged, not rejected or restored. `get_eval_report` derives
  `resolved` from parsed results for the instance's `FAIL_TO_PASS` and
  `PASS_TO_PASS` sets. Thus the external label belongs to the submitted patch and
  a separately controlled environment, not the agent's self-report.
  Sources: [`run_instance`](https://github.com/SWE-bench/SWE-bench/blob/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/swebench/harness/run_evaluation.py#L229-L410),
  [`get_eval_report`](https://github.com/SWE-bench/SWE-bench/blob/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/swebench/harness/grading.py#L329-L392),
  [official benchmark description](https://www.swebench.com/original.html),
  [SWE-bench paper](https://openreview.net/forum?id=VTF8yNQM66).
  SWE-agent's `SweBenchEvaluate.on_end` is the corresponding post-run handoff to
  `sb-cli`: [source](https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/sweagent/run/hooks/swe_bench_evaluate.py#L107-L129).

- **Aider.** `Commands.cmd_test` executes the configured command as an arbitrary
  shell string in the repository root and interprets a non-zero exit as errors;
  `BaseCoder.send_message` can invoke this path automatically after edits.
  Aider's documentation explicitly notes that formatter-style linters can modify
  files and make exit status ambiguous. The reviewed path has no before/after
  workspace comparison.
  Sources: [`Commands.cmd_test`](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/commands.py#L993-L1023),
  [`BaseCoder.send_message` auto-test path](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/coders/base_coder.py#L1607-L1624),
  [official lint/test documentation](https://aider.chat/docs/usage/lint-test.html).

## Transfer proposals for Evidence Harness

The following are proposals, not behaviors observed in the cited systems.

### 1. Verify a frozen candidate in a disposable workspace

At the start of `_handle_finish`, create a content-addressed candidate snapshot
that includes the intended deliverables, then materialize it into a temporary
copy-on-write workspace. Run all completion checks there with writable scratch,
cache, and build directories. Discard the verifier workspace after collecting
receipts; never copy its output back to the live task workspace.

This avoids the false choice between rejecting normal compilers/tests because
they write and allowing those writes to alter the candidate. Cline's snapshot
details are useful for preserving tracked and untracked inputs, while Codex's
sandboxing shows that write constraints should be enforced below command-name
classification.

Each verification receipt should bind:

- candidate content digest and work epoch;
- verifier image/toolchain identity;
- command, exit status, and output digests;
- deliverable manifest before and after the check;
- paths created, changed, or removed inside the disposable workspace.

A changed verifier workspace is evidence about the command, not automatically a
failed candidate. Policy can reject changes to source deliverables while allowing
declared scratch/cache/build paths.

### 2. Demote write detection from gate to advisory classifier

Keep `validate_check` checks for explicit redirects and obvious mutators because
they cheaply catch bad proposals. Rename or model their result as
`suspected_write_effect`, not `read_only`. Commands such as `pytest`, compilers,
package scripts, and arbitrary programs can write without a write verb appearing
in the shell text; shell syntax alone cannot certify their effects.

The authoritative signal should be filesystem enforcement plus the before/after
manifest. If isolation is unavailable, fail closed or preserve-and-restore from
a verified snapshot; do not accept a command merely because its text passed the
regex.

### 3. Calibrate internal completion against external outcomes

Persist one immutable join key from internal run to external evaluation:
`candidate_digest + task_instance + harness_version`. For every frozen evaluation
set, record the internal decision (`verified` or not), reviewer score/verdict,
check families, and failure category alongside only the eventual external label.

Report a confusion matrix, especially:

- internal `verified`, external failure: unsafe completion;
- internal non-verified, external success: unnecessary rejection;
- internal and external failure: execution versus verification diagnosis;
- internal and external success: confirmed completion.

Choose thresholds and reviewer prompts on a development split, then lock them
before evaluating a held-out split. Do not reveal hidden tests, grader output, or
task-specific labels to the running agent. This measures calibration; it does not
imply or claim a benchmark improvement.

## Recommended sequence

1. Add a candidate digest and external-evaluation join record without changing
   completion behavior.
2. Move completion commands into a disposable writable verifier workspace and
   record before/after manifests.
3. Convert static mutation rejection into an advisory classification once the
   isolation boundary is enforced.
4. Produce calibration tables from frozen historical runs before changing
   thresholds or prompts.
