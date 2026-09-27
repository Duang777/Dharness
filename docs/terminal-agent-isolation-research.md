# Terminal agent isolation and finalization mechanisms

Research cutoff: 2026-09-27.

## Scope and evidence rules

This report examines three questions:

1. How terminal agents verify a frozen candidate rather than a moving workspace.
2. How they retain actions, tokens, or wall time for finalization.
3. How they prevent verification from changing the candidate or producing a false pass.

The source set is limited to project-owned repositories and source code,
official Terminal-Bench leaderboard and submission artifacts, and official
documentation. Author papers, third-party analyses, and marketing pages are
excluded. Citation URLs were rechecked on 2026-09-27; implementation claims
remain pinned to immutable commits.

Claims use these labels:

- **Direct evidence** means the linked source implements or specifies the behavior.
- **Inference** means the recommendation follows from direct evidence, but the
  source does not implement or test the complete recommendation.
- **Limit** identifies what the source does not prove.

This report does not claim that any mechanism improves Evidence Harness's score.

## What the leaderboard establishes

**Direct evidence.** The official
[Terminal-Bench 2.0 leaderboard](https://www.tbench.ai/leaderboard/terminal-bench/2.0)
reports NexAU-AHE at 84.7%, LemonHarness at 84.5%, Capy at 83.1%, Codex CLI at
82.2%, and Terminus-KIRA at 74.8% with Gemini 3.1 Pro. The page states that a
Terminal-Bench team member ran and verified the results.

**Direct evidence.** The official
[submission specification](https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard/raw/main/README.md)
requires a `1.0` timeout multiplier, forbids agent-timeout, verifier-timeout,
and resource overrides, requires at least five trials per task, and requires
run artifacts.
The official
[integrity policy](https://www.tbench.ai/news/leaderboard-integrity-update)
also requires ATIF trajectories for passing trials and assigns reward zero to
reward hacking.

**Limit.** A leaderboard row evaluates an agent, model, and run configuration
together. It is not a harness ablation. The public NexAU-AHE repository revision
reviewed below postdates its leaderboard submission. The official
[LemonHarness submission metadata](https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard/raw/main/submissions/terminal-bench/2.0/LemonHarness_GPT-5.3-CodeX/metadata.yaml)
links to LemonAgent, but the pinned
[LemonAgent repository](https://github.com/Open-Lemon/LemonAgent/tree/19044daae1bbced9084b19f0878d4ac20208a00b)
contains documentation and figures rather than the submitted harness, and its
[README says the code is under internal review](https://github.com/Open-Lemon/LemonAgent/blob/19044daae1bbced9084b19f0878d4ac20208a00b/README.md#L113-L115).
The official
[Capy submission metadata](https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard/raw/main/submissions/terminal-bench/2.0/Capy__GPT-5.5/metadata.yaml)
links to a product site, not source code. No public implementation matching
either submission was available for mechanism-level review. No mechanism below
is credited with causing a leaderboard score.

## Mechanism inventory

| Mechanism | Directly implemented behavior | What it does not establish |
|---|---|---|
| Harbor separate verifier | Stops the agent environment, creates a dedicated verifier environment, uploads declared artifacts, applies verifier network policy, and runs the verifier there | Declared artifacts may omit required state; this is reconstruction, not a live snapshot, and cleanup failure is logged rather than reflected in the verifier result |
| Harbor regrade | Copies recorded artifacts into a new trial, requires separate verification, validates artifact coverage, and never modifies the source trial | The manifest has no content digest, so it does not cryptographically bind the recorded bytes |
| Codex Stop hook | Runs deterministic code at attempted stop and can inject a continuation prompt | The hook runs from the session working directory and is not an isolated verification environment |
| Claude Code TaskCompleted hook | Can run a deterministic check and reject a task's transition to completed | It covers task-state transitions, not every final response, and its example checks the mutable working directory |
| OpenHands stop hook and run snapshot | Lets a stop hook revert `FINISHED` to `RUNNING`; the remote client prefers a post-run full-state snapshot over an early status | After 30 seconds of repeated terminal REST status, the client accepts a fallback without the snapshot |
| OpenHands stuck detector | Detects repeated action-result cycles, repeated action errors, monologues, and alternating cycles; it nudges once on an error streak and then enters `STUCK` if repetition continues | It uses bounded history and exact normalized event comparison; context-window-loop detection is declared but not implemented |
| Terminus-KIRA confirmation | Requires two `task_complete` calls and presents the original task, current terminal output, and a checklist between them | The same model confirms itself; no candidate digest or isolated check is produced |
| NexAU-AHE RALPH gate | For runs it classifies as code-modifying, requires a recent command whose text resembles a test and whose exit code is zero before accepting `complete_task` | Its default modification detector covers only three edit tools, its test detector uses substring matching and permits stale history, and it force-allows completion after a configured number of blocks |
| NexAU-AHE debugger budget | Tells a 20-call helper agent to make call 20 `complete_task` and to stop fresh exploration near the limit | This is prompt enforcement in a helper agent, not a controller reserve in the submitted task agent |
| mini-swe-agent ProgramBench budget | Exposes elapsed wall time and calls, warns below 20 calls or 600 seconds, then enforces hard limits before the next model call | The warning does not prevent new mutations from consuming the remaining budget |
| Codex rollout budget | Shares weighted accounting for output and non-cached input tokens across a root thread and subagents, injects threshold reminders, and raises an error at exhaustion | It is one shared ceiling, not an earmarked finalization reserve |

## 1. Frozen-candidate verification

### Harbor's separate verifier is the strongest reviewed isolation boundary

**Direct evidence.** Harbor 0.23.0 defines `shared` and `separate` verifier
modes. Separate mode uses either an explicit verifier environment or a fresh
copy of the task environment configuration
([`config.py` lines 555-597](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/models/task/config.py#L555-L597),
[`verifier_mode.py` lines 33-64](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/models/task/verifier_mode.py#L33-L64)).
For a single-step trial, Harbor collects artifacts, stops the agent environment
in separate mode, and only then runs verification
([`single_step.py` lines 38-55](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/trial/single_step.py#L38-L55)).

**Direct evidence.** Harbor creates the verifier environment, uploads declared
artifacts to their original paths, applies the verifier network policy, runs the
verifier with its own timeout, and calls `stop` in a `finally` block. The only
runtime mount this path supplies is the verifier log directory
([`trial.py` lines 787-910](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/trial/trial.py#L787-L910)).
Candidate files reach the verifier through artifact upload rather than a writable
mount back to the stopped agent environment.

**Limit.** This boundary reproduces declared files, not arbitrary machine state.
Processes, memory, sockets, undeclared paths, sidecar state, mounted data, and
external service state are not automatically part of the candidate. Harbor also
catches and logs verifier-environment cleanup errors instead of changing the
verifier result. Separate mode therefore isolates the rematerialized filesystem
inputs, but does not attest complete runtime isolation or successful cleanup.
This agrees with the more detailed runtime analysis in
`isolated-verification-runtime-research.md`; that material is not repeated here.

### Harbor regrade is a reusable frozen-record pattern

**Direct evidence.** Harbor's regrade trial replaces the agent phase with
recorded outputs, runs separate verifiers, and states that the source trial is
never modified
([`regrade.py` lines 1-13](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/trial/regrade.py#L1-L13)).
It copies the recorded agent and artifact directories into a new trial and
runs fresh verification against the copy
([`regrade.py` lines 574-630](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/trial/regrade.py#L574-L630)).

**Direct evidence.** Regrade fails when:

- the replacement task's verifier does not use separate mode, regardless of
  how the source trial was originally verified
  ([`regrade.py` lines 97-122](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/trial/regrade.py#L97-L122));
- the artifact manifest is absent or unreadable
  ([`regrade.py` lines 233-265](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/trial/regrade.py#L233-L265)); or
- a declared artifact was never collected, collided, failed collection, moved,
  changed type, or used incompatible exclusion filters
  ([`regrade.py` lines 388-516](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/trial/regrade.py#L388-L516)).

**Inference.** This is stronger than merely starting a clean verifier container.
It checks that the recorded candidate contains the inputs the new verifier
expects.

**Direct evidence.** The live artifact downloader is explicitly best effort,
and the uploader skips a host path that does not exist
([`artifact_handler.py` lines 132-213](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/trial/artifact_handler.py#L132-L213)).
Regrade's coverage checks close that silent-omission path for replay.

**Limit.** `ArtifactManifestEntry` records source, destination, type, status,
service, and exclusions, but no content hash
([`artifact_manifest.py` lines 6-18](https://github.com/laude-institute/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/src/harbor/models/trial/artifact_manifest.py#L6-L18)).
Regrade proves structural coverage, not byte identity. A changed recorded file
with the same path and type can pass the coverage check.

### Transferable frozen-candidate contract

**Inference.** Freeze should be an explicit transaction:

1. Close the candidate to further writes and assign a monotonically increasing
   candidate epoch.
2. Capture every declared verifier input plus a canonical manifest.
3. Hash file contents, paths, types, modes, symlink targets, exclusions, task
   identity, and verifier specification into a candidate ID.
4. Materialize those bytes in a fresh verifier environment.
5. Bind every check result to the candidate ID and verifier ID.
6. Invalidate all results if any later mutation advances the candidate epoch.

Harbor supplies the environment and artifact-coverage parts. Content hashing
and candidate-epoch binding are additions, not Harbor 0.23.0 behavior.

## 2. Reserving budget for finalization

### Prompt-level reserves exist, but they are advisory

**Direct evidence.** A NexAU-AHE debugger helper prompt assigns itself a
20-tool-call budget and tells the model to plan so call 20 is `complete_task`.
Its workflow assigns calls 16 to 18 to comparison and call 20 or earlier to
finalization
([`system_prompt.md` lines 24-49](https://github.com/china-qijizhifeng/agentic-harness-engineering/blob/8b2a55d97590363fe50c3cc6b5e833b020a4bb4c/agents/evolve_agent/skills/agent-debugger-cli/_source/agent_debugger_core/runtime/system_prompt.md#L24-L49)).
The runtime separately configures `max_iterations: 25` and reports a
`budget-exceeded` result if that cap is reached without `complete_task`
([`agent_config.yaml` lines 1-13](https://github.com/china-qijizhifeng/agentic-harness-engineering/blob/8b2a55d97590363fe50c3cc6b5e833b020a4bb4c/agents/evolve_agent/skills/agent-debugger-cli/_source/agent_debugger_core/runtime/agent_config.yaml#L1-L13),
[`runner.py` lines 92-129](https://github.com/china-qijizhifeng/agentic-harness-engineering/blob/8b2a55d97590363fe50c3cc6b5e833b020a4bb4c/agents/evolve_agent/skills/agent-debugger-cli/_source/agent_debugger_core/runtime/runner.py#L92-L129)).
This is a concrete action-reservation instruction.

**Limit.** The controller does not reserve call 20 as a distinct capability.
The enforced cap is five iterations later, so the model can still spend the
prompt's nominal finalization call incorrectly. This helper is part of the later
AHE evolution repository and is not proven to be part of the leaderboard
submission.

**Direct evidence.** mini-swe-agent's ProgramBench configuration injects a
warning when fewer than 20 model calls remain and another when less than 600
seconds remain. Both warnings direct the model to compile, record unfinished
work, and submit
([`programbench.yaml` lines 238-256](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/config/benchmarks/programbench.yaml#L238-L256)).
The agent exposes elapsed seconds to templates and enforces step, cost, and wall
limits before a model call
([`default.py` lines 19-67](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py#L19-L67),
[`default.py` lines 130-150](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py#L130-L150)).

**Limit.** The warning and hard limit are separate mechanisms. Ordinary model
calls and commands remain available inside the warning window, so this is not a
protected reserve.

### Shared accounting prevents hidden budget consumption

**Direct evidence.** Codex's rollout budget belongs to one root-thread session
tree. It weights output and non-cached input tokens, tracks reminder delivery per
thread, and marks a reminder delivered only after inserting it into history
([`rollout_budget.rs` lines 18-112](https://github.com/openai/codex/blob/18344a972ddf06aa0bb80773bfb7564576e8991b/codex-rs/core/src/rollout_budget.rs#L18-L112)).
The reminder becomes a developer message stating the remaining weighted tokens
([`context/rollout_budget.rs` lines 4-31](https://github.com/openai/codex/blob/18344a972ddf06aa0bb80773bfb7564576e8991b/codex-rs/core/src/context/rollout_budget.rs#L4-L31)).
The controller raises `SessionBudgetExceeded` once accounting reaches the limit
([`agent/control/budget.rs` lines 11-17](https://github.com/openai/codex/blob/18344a972ddf06aa0bb80773bfb7564576e8991b/codex-rs/core/src/agent/control/budget.rs#L11-L17)).
Its API states that inference and compaction usage count
([`agent/api.rs` lines 101-113](https://github.com/openai/codex/blob/18344a972ddf06aa0bb80773bfb7564576e8991b/codex-rs/core/src/agent/api.rs#L101-L113)).

**Inference.** Finalization needs the same shared accounting scope. Executor,
reviewer, repair, compaction, and subagent calls must draw from one ledger.
Otherwise a nominal reserve can be consumed by work hidden in another actor.

**Limit.** Codex's cited mechanism is a shared ceiling with reminders. It does
not earmark tokens for a final check.

### Hard reservation needs admission control

**Inference.** A finalization reserve should be unavailable to ordinary work
from the beginning of the run:

- `work_actions = total_actions - final_actions`
- `work_deadline = run_deadline - final_time`
- `work_tokens = total_tokens - final_tokens`

When any work budget reaches zero, action admission becomes monotonic:

- allow candidate freeze, bounded checks, one focused repair, cleanup, and finish;
- reject broad exploration, dependency churn, and unrelated mutations; and
- recheck the wall-clock boundary after model latency and after each command,
  not only at turn start.

This is stricter than the prompt-level mechanisms above. No reviewed primary
source exposed a matching controller-enforced wall-clock reserve in a scored
submission, so the reserve size remains a local experiment rather than an
industry constant.

### Detect repeated failure before it consumes the remaining budget

**Direct evidence.** OpenHands enables stuck detection by default and scans at
most 20 recent events after the last user message. Its detector checks repeated
action-observation pairs, repeated errors from the same action, agent
monologues, and alternating action-observation patterns
([`conversation.py` lines 65-79](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/conversation.py#L65-L79),
[`stuck_detector.py` lines 18-154](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/stuck_detector.py#L18-L154)).
The default thresholds are four repeated action-observation pairs, three
repeated action errors, three consecutive agent messages, and six alternating
cycles
([`types.py` lines 136-160](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/types.py#L136-L160)).

**Direct evidence.** For repeated action errors, OpenHands emits one corrective
nudge when the streak first reaches the threshold. If the same failure
continues past the threshold, the run loop sets the terminal state to `STUCK`
([`stuck_detector.py` lines 192-248](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/stuck_detector.py#L192-L248),
[`local_conversation.py` lines 735-756](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py#L735-L756)).

**Inference.** Loop detection should have one bounded recovery transition and a
distinct exhausted outcome. It should not consume the finalization reserve by
repeating identical calls. The detector complements, rather than replaces,
action, token, cost, and wall-clock limits.

**Limit.** OpenHands compares normalized recent events, so semantically
equivalent attempts with changing arguments can evade detection. Its
context-window-error detector currently returns `False`
([`stuck_detector.py` lines 314-323](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/stuck_detector.py#L314-L323)).

## 3. Preventing side effects and false positives

### A completion veto is useful but not sufficient

**Direct evidence.** Codex Stop hooks run when a turn is about to stop. A hook
can return a blocking decision and a reason, which Codex inserts as a new
continuation prompt
([official Stop hook documentation](https://developers.openai.com/codex/hooks#stop)).
The turn loop records the prompt, sets `stop_hook_active`, and continues
([`turn.rs` lines 647-695](https://github.com/openai/codex/blob/18344a972ddf06aa0bb80773bfb7564576e8991b/codex-rs/core/src/session/turn.rs#L647-L695)).

**Direct evidence.** The same documentation states that hook commands run with
the session working directory. It also states that `PostToolUse` cannot undo a
tool's side effects. A Stop hook is therefore a completion policy hook, not a
verification sandbox.

**Inference.** Use a stop hook to request or validate a receipt. Do not let the
hook itself run a write-capable check against the candidate.

### Task-state completion hooks provide a narrower veto

**Direct evidence.** Claude Code's `TaskCompleted` hook runs when an agent marks
a task complete with `TaskUpdate` or when a teammate finishes while tasks remain
in progress. A command hook that exits with status 2 prevents the task from
being marked complete and sends its stderr text back to the model. The official
example runs the test suite and exits 2 on failure
([official `TaskCompleted` hook documentation](https://code.claude.com/docs/en/hooks#taskcompleted)).

**Limit.** This hook applies to Claude Code task objects, not every attempt by
the main agent to finish a response. Its input contains task and session
metadata but no frozen candidate identifier, and the documented example tests
the current working directory. It is a stronger task-state veto than a prompt
reminder, but it is not an isolated verifier or an attestation.

### Freshness must be tied to a run, not a status string

**Direct evidence.** OpenHands lets a stop hook deny completion and changes
`FINISHED` back to `RUNNING`
([`local_conversation.py` lines 1944-1972](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py#L1944-L1972)).
Its remote client therefore treats a per-field `FINISHED` update as advisory.
It prefers a post-run full-state snapshot, ignores an initial subscription
snapshot unless the run is armed, and reconciles events before returning
([`remote_conversation.py` lines 1064-1104](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py#L1064-L1104),
[`remote_conversation.py` lines 1295-1360](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py#L1295-L1360)).

**Limit.** If the WebSocket snapshot never arrives, 30 seconds of repeated
terminal REST status becomes a hard fallback
([`remote_conversation.py` lines 1364-1414](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py#L1364-L1414)).
The implementation explicitly accepts this residual early-return risk to avoid
an indefinite wait.

**Inference.** Verification acceptance should require a run-scoped completion
event emitted after all check results and cleanup records are durable. A stale
`passed` status from a prior candidate must not satisfy a new finish attempt.

### Judge failure and budget exhaustion need distinct terminal states

**Direct evidence.** OpenHands's goal judge returns `complete=false` with score
zero when its response cannot be parsed
([`judge.py` lines 43-70](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/goal/judge.py#L43-L70),
[`judge.py` lines 89-120](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/goal/judge.py#L89-L120)).
The controller returns `status="capped"` rather than `"complete"` when audit
iterations run out
([`controller.py` lines 103-133](https://github.com/OpenHands/software-agent-sdk/blob/da28c7736ea667ceae51cf3a3b9b37ab5f528f22/openhands-sdk/openhands/sdk/conversation/goal/controller.py#L103-L133)).

**Limit.** The judge evaluates a rendered event transcript. It does not inspect
an independently captured candidate filesystem. The fail-closed fallback covers
responses from which no JSON object can be parsed. For parsed JSON, the code
coerces `complete` with Python `bool`, so a malformed non-empty value such as
the string `"false"` becomes true. A self-consistent transcript can also omit a
broken deliverable.

**Inference.** Keep at least four distinct outcomes: verified, verification
failed, verification unavailable, and budget exhausted. Only the first may
produce a successful completion receipt.

### Command completion is not check success

**Direct evidence.** Terminus-KIRA appends a unique echo marker after a command
and polls until that marker appears
([`terminus_kira.py` lines 234-288](https://github.com/krafton-ai/kira/blob/652dacbf14d29ea93a83c496ee91e0e5ba286721/terminus_kira/terminus_kira.py#L234-L288)).
It also requires a second `task_complete` call after showing the original task,
terminal output, and a checklist
([`terminus_kira.py` lines 320-335](https://github.com/krafton-ai/kira/blob/652dacbf14d29ea93a83c496ee91e0e5ba286721/terminus_kira/terminus_kira.py#L320-L335)).

**Limit.** The marker proves that the shell reached the following `echo`; it does
not encode the preceding command's exit status. The confirmation is performed
by the same model and is not bound to a candidate digest.

**Inference.** A verification receipt needs the check's exit status, bounded
stdout and stderr hashes, candidate ID, check ID, start and end times, and
cleanup result. A shell-return marker is useful process synchronization, but it
cannot be the pass predicate.

### Recent-success heuristics admit stale or unrelated evidence

**Direct evidence.** For a conversation it classifies as code-modifying,
NexAU-AHE's RALPH middleware searches recent history for a shell command
containing one of several test-like substrings and accepts it if the associated
exit code is zero
([`ralph_loop.py` lines 44-60](https://github.com/china-qijizhifeng/agentic-harness-engineering/blob/8b2a55d97590363fe50c3cc6b5e833b020a4bb4c/agents/evolve_agent/middleware/ralph_loop.py#L44-L60),
[`ralph_loop.py` lines 164-223](https://github.com/china-qijizhifeng/agentic-harness-engineering/blob/8b2a55d97590363fe50c3cc6b5e833b020a4bb4c/agents/evolve_agent/middleware/ralph_loop.py#L164-L223)).
After `max_blocks`, it force-allows `complete_task`
([`ralph_loop.py` lines 95-158](https://github.com/china-qijizhifeng/agentic-harness-engineering/blob/8b2a55d97590363fe50c3cc6b5e833b020a4bb4c/agents/evolve_agent/middleware/ralph_loop.py#L95-L158)).

**Limit.** The default modification detector recognizes only `write_file`,
`replace`, and `apply_patch`. A run that changes code only through the shell can
be classified as a non-code task and bypass the gate
([`ralph_loop.py` lines 61-130](https://github.com/china-qijizhifeng/agentic-harness-engineering/blob/8b2a55d97590363fe50c3cc6b5e833b020a4bb4c/agents/evolve_agent/middleware/ralph_loop.py#L61-L130),
[`ralph_loop.py` lines 245-260](https://github.com/china-qijizhifeng/agentic-harness-engineering/blob/8b2a55d97590363fe50c3cc6b5e833b020a4bb4c/agents/evolve_agent/middleware/ralph_loop.py#L245-L260)).
A command such as `python -c`, `curl`, or `diff` can exit zero without testing
the task's requirements. A passing command from before a later mutation can also
satisfy the lookback. Force-allow converts repeated gate rejection into
completion permission.

**Inference.** Do not infer verification from command text or recency. Require a
declared check, an exact candidate ID, a fresh receipt, and a pass predicate
defined before execution. Verification failure must never become success merely
because the retry budget ended.

## Recommended end-state protocol

The source-backed mechanisms combine into this protocol:

1. **Reserve.** Allocate separate work and finalization budgets at run start.
   Account for every actor in the same action, token, and wall-clock ledger.
2. **Freeze.** Stop candidate writes, increment the candidate epoch, and create
   a content-addressed manifest of declared verifier inputs.
3. **Validate coverage.** Fail before execution if a required input is absent,
   collided, failed collection, changed type, or has incompatible filters.
4. **Materialize.** Create a fresh verifier environment with no writable mount
   back to the candidate. Restore inputs at their original absolute paths.
5. **Constrain.** Apply a verifier-specific network policy and hard deadline.
   Give every check a clean child or restore the frozen image before the next
   check when check-order independence matters.
6. **Attest.** Record candidate ID, verifier ID, check specification hash, exit
   status, output hashes, filesystem delta, timeout state, and cleanup outcome.
7. **Commit completion.** Emit one run-scoped terminal event only after required
   checks pass and cleanup completes. Any later candidate mutation invalidates
   the receipt.
8. **Fail closed.** Missing evidence, an unavailable verifier, a stale receipt,
   cleanup failure, or exhausted finalization budget is not verified completion.

## Bottom line

The most complete reviewed mechanism is Harbor's verifier-only replay of a
recorded artifact set, especially its fail-closed coverage checks. The budget
lesson is to distinguish reminders from admission control: the reviewed agents
warn before hard limits and detect repeated loops, but a protected reserve
requires the controller to withhold actions, time, and tokens from ordinary
work. The false-positive lesson is to separate shell return, model confirmation,
terminal status, and verified completion into different states bound to one
candidate.

No reviewed primary source proves an Evidence Harness score uplift from these
mechanisms.
