# Terminal-Agent Harness Research

Research cutoff: 2026-09-27. This report uses first-party documentation, papers,
repositories, and official evaluation artifacts only.

## Executive conclusion

The strongest transferable pattern is not a larger prompt or a more elaborate
planner. High-performing systems move critical responsibilities out of model prose
and into the harness:

1. Preserve the task and current machine state across long runs.
2. Expose remaining time, calls, and tokens before the model spends them.
3. Detect repeated failures and force a strategy change.
4. Execute commands through bounded, observable interfaces.
5. Reserve time and capacity for evaluator-like final checks.
6. Treat completion as a state transition backed by fresh evidence.

Evidence Harness already implements unusually strong versions of items 4 and 6:
structured actions, a single environment writer, command receipts, `work_epoch`
freshness, and an independent completion reviewer. Its next gains are more likely
to come from better completion calibration, a protected final-validation phase,
token-aware control, resumability, and richer failure-state tracking than from
adding more free-form reasoning.

The current leaderboard is not a controlled harness ablation. As of 2026-09-27,
NexAU-AHE leads at **84.7% ± 2.1**, followed by LemonHarness at **84.5% ± 2.6**,
Capy at **83.1% ± 2.1**, and Codex CLI at **82.2% ± 2.2**. Their confidence
intervals overlap, and their model/configuration stacks differ. Rank order alone
does not establish that one harness is better.

## Evidence standard

- **Evaluator-verified:** the Terminal-Bench team reports that it ran and verified
  the submission; immutable submission artifacts expose configuration and trials.
- **Implementation-verified:** behavior is present in public source at a pinned
  commit.
- **Author-reported:** a paper or project report describes a mechanism or result,
  but the exact submitted implementation is unavailable.
- **Marketing-only:** a product page makes a claim without enough public
  implementation or evaluation detail to inspect.

The official submission repository requires a `1.0` timeout multiplier, forbids
agent/verifier timeout and resource overrides, requires at least five trials per
task, and requires run artifacts. Since the 2026-04-19 integrity update, passing
trials also require ATIF trajectories; reward hacking receives zero and benchmark
modification is treated as cheating.

## Source matrix

| System or source | Primary sources and date | What the source establishes | Evidence limit |
|---|---|---|---|
| Terminal-Bench 2.0 | [Official leaderboard](https://www.tbench.ai/leaderboard/terminal-bench/2.0), accessed 2026-09-27; [submission repository](https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard), accessed 2026-09-27; [integrity update](https://www.tbench.ai/news/leaderboard-integrity-update), 2026-04-19 | Current scores, run constraints, minimum trial count, artifact requirements, and integrity policy | Scores compare complete agent-model stacks, not harnesses under one fixed model |
| NexAU-AHE | [Paper v4](https://arxiv.org/html/2604.25850), 2026-05-18; [source at `8b2a55d`](https://github.com/china-qijizhifeng/agentic-harness-engineering/tree/8b2a55d97590363fe50c3cc6b5e833b020a4bb4c), 2026-08-03; [official submission commit `dcfb47e`](https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard/commit/dcfb47ed59980473bea884bf8321a87cb9a3e285), 2026-05-14 | Official 84.7% score; evaluator-oriented prompt rules; execution-risk middleware; layered trajectory analysis; falsifiable harness-edit manifests | The repository's evolution-only completion gate, failover, and budget reminder are not proven to be in the submitted task-agent configuration |
| LemonHarness | [Technical report v1](https://arxiv.org/html/2606.24311), 2026-06-23; [official submission commit `9ae28d0`](https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard/commit/9ae28d01e9bd363a9587568cef2584adad8160b7), 2026-05-14; [public LemonAgent repository](https://github.com/Open-Lemon/LemonAgent), accessed 2026-09-27 | Official 84.5% submission; paper-described workspace boundary, structured tools, rule knowledge, time phases, and final reserve | The repository says implementation is under internal review and does not expose a clearly matching LemonHarness; mechanisms are author-reported |
| Capy | [Official submission commit `79042e3`](https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard/commit/79042e3f65d57675e2b56cdca0f1f5dd3fdd62f4), 2026-05-14; [product site](https://capy.ai), accessed 2026-09-27 | Official 83.1% score; sampled artifact identifies `CapyBuildAgent`, GPT-5.5, build mode, `--handoff`, runtime, iterations, and tokens | No public implementation or technical mechanism documentation was found; product copy is marketing-only |
| Terminus 2 | [Official documentation](https://harborframework.com/docs/agents/terminus-2), accessed 2026-09-27; [source at `cdb76ba`](https://github.com/harbor-framework/harbor/blob/cdb76bae6dc88d5bca1c8f0754bbba300d6574b4/src/harbor/agents/terminus_2/terminus_2.py), 2026-09-23; [original Terminus announcement](https://www.tbench.ai/news/terminus), 2025-05-19 | Tmux mono-tool, host-side controller, proactive and overflow-triggered context recovery, ATIF output, and configurable turn limit | A high turn ceiling supports persistence but is not evidence of efficient budget control or correct completion |
| Terminus-KIRA | [Technical post](https://www.krafton.ai/blog/posts/2026-02-20-terminus_kira/terminus-en.html), 2026-02-20; [source at `652dacb`](https://github.com/krafton-ai/kira/blob/652dacbf14d29ea93a83c496ee91e0e5ba286721/terminus_kira/terminus_kira.py), 2026-05-29 | Native tool calls, marker-based command completion, 30 KB output cap, block timeout, image tool, and two-step completion confirmation | The claimed reduction in false completion is not accompanied by a public controlled ablation; confirmation remains model self-review |
| OpenHands SDK | [Agent architecture](https://docs.openhands.dev/sdk/arch/agent), accessed 2026-09-27; [context condenser](https://docs.openhands.dev/sdk/guides/context-condenser), accessed 2026-09-27; [source at `e21d776`](https://github.com/OpenHands/software-agent-sdk/tree/e21d77673b738f056676044600c4ad81c5a575c8/openhands-sdk/openhands/sdk), 2026-09-23 | Stateless atomic steps, typed action/observation events, persistence and resume, configurable iteration cap, confirmation policy, and context condensation | The documentation's “up to 2x” cost reduction is an author claim; no evaluator-like default completion gate was identified |
| Codex CLI | [CLI features](https://developers.openai.com/codex/cli/features), [hooks](https://developers.openai.com/codex/hooks), and [security](https://developers.openai.com/codex/agent-approvals-security), accessed 2026-09-27; [source at `4083a68`](https://github.com/openai/codex/tree/4083a68f88375bb0bc90a41b8c454d9e2d7c5281/codex-rs/core/src), 2026-09-24 | Resume, compaction, approvals, lifecycle hooks, structured plan updates, PTY process handles, bounded output, and an experimental shared rollout-token budget | Stop-time validation is configurable rather than a universal evaluator gate; rollout budget support is experimental |

## Mechanism comparison

| System | Completion verification | Context management | Budget control | Recovery | Tool execution and planning |
|---|---|---|---|---|---|
| NexAU-AHE | Prompt and middleware require evaluator-like semantic checks, canonical entry points, margin checks, and preservation of the last passing publish state | Raw traces are distilled into layered reports with links to source evidence; task agent allows a large context | Iteration and context limits; runtime hints after long commands/timeouts | Detects repeated error families, repeated dependency probes, shallow checks, and post-success mutation | One structured shell tool; harness components and edits are separately observable |
| LemonHarness | Paper says final state receives external validation | Controlled workspace, execution records, and reusable rule knowledge remain available to later decisions | Remaining time every turn; monotonic phases; `0.05T` phase grace and final `0.1T` no-new-mutation reserve | Time pressure causes phase changes rather than open-ended retries | Structured file, command, dependency, and process tools; exploration moves toward implementation and validation |
| Capy | Unknown | Unknown | One sampled successful trial reports 14 iterations, 467,332 tokens, and 462,310 ms | Unknown | Public artifact shows build mode and a `--handoff` flag; semantics are undocumented |
| Terminus 2 | Model decides when to finish; Harbor verifier remains external | Three-agent summary/question/answer compaction below 8,000 free tokens; multi-stage overflow fallback; linear trajectories can split at compaction boundaries | Configurable `max_turns`, with a very high default | Retries plus progressive context-overflow recovery | One interactive tmux terminal; planning remains in the model response |
| Terminus-KIRA | First `task_complete` call produces a checklist with original task and terminal state; a second call commits | Inherits Terminus-style summarization; adds recent-message prompt caching | Bounded calls and output, but no published phase reserve | Prompted adaptive replanning; block timeout; completion-marker polling avoids guessed full sleeps | Native command and image tools; unique shell markers detect actual command return |
| OpenHands | Typed events and optional confirmation/security policies; no default semantic completion proof found | First events and recent events survive LLM condensation; full event state can persist and resume | Per-run iteration cap and usage accounting | Atomic steps, pause/resume, persisted state, explicit error observations | Typed tools; event-driven action/observation loop; task tracker available |
| Codex CLI | Optional deterministic `Stop` hooks can require validation; the default agent still decides when to stop | Auto/manual compaction preserves initial context; local transcripts resume; full rollout data and live truncated context are separated | Status visibility plus an experimental weighted token budget shared by the session tree | Command retry, session resume, undo/fork, and compaction fallback | PTY-backed unified execution with process IDs, timeouts, yield intervals, output caps; structured `update_plan` events |
| Evidence Harness | Hard `finish` schema, requirement-to-check mapping, read-only checks rerun by the controller, reviewer, and epoch freshness | Deterministic state projection keeps task, plan, budgets, errors, changes, and bounded receipts; full output remains in the journal | Hard turn, environment-call, repair, recovery, command, model-call, and wall-time caps; three calls reserved for verification | One schema repair, transport retries, failure classes, repeated-cycle detection, forced replan, receipt-driven replay | Strict JSON actions, single `CommandRunner`, fail-fast batches, bounded output, plan-in-action |

## What appears to transfer

### 1. Completion needs evidence, not stronger wording

KIRA's second confirmation and AHE's prompt rules reduce casual early stopping, but
both still depend on model judgment. Evidence Harness's controller-rerun checks and
epoch invalidation are structurally stronger. The remaining problem is **semantic
calibration**: the model proposes both the requirements and their checks, so a
self-consistent but incomplete interpretation can pass internally.

The local full run demonstrates both directions of error:

- 10 live tasks ended `verified` but received reward 0.
- 12 tasks received reward 1 while the harness ended in `budget_exhausted`,
  `model_stopped`, or `model_failure`.

Therefore, the next completion change should target agreement with externally
observable behavior, not make the reviewer more demanding in general.

### 2. A final phase needs protected wall time, not only command slots

Evidence Harness reserves three environment calls, but ordinary work may consume
nearly all 1,800 seconds. The model receives remaining time, yet the controller
does not change allowed actions as the deadline approaches. LemonHarness's most
directly transferable idea is a final `0.1T` phase in which new mutations stop and
the best current candidate is validated and preserved.

### 3. Context should preserve facts and provenance separately

Evidence Harness's deterministic projection is safer than relying entirely on an
LLM summary: the original task, budget, current plan, errors, epochs, receipt
hashes, and recent outputs are reconstructed from controller state every turn.
This already follows the useful part of OpenHands, Terminus 2, and Codex
compaction while avoiding summary hallucination.

The missing layer is a compact semantic ledger of requirements, hypotheses tested,
and evidence status. Current `current_plan` is at most eight strings and old
receipts become generic summaries. A model can forget *why* a path failed even
when the journal still contains the bytes.

### 4. Budget telemetry should also be an enforcement input

The harness records input, cache, output tokens, cost, and model-call count, but
does not cap them or include remaining token/cost budget in the executor prompt.
Only turns, environment calls, repairs, recoveries, and wall time are enforced.
Codex's experimental shared weighted-token budget supplies the missing control
shape: one budget across executor, reviewer, repairs, and future subagents, with
explicit remaining-budget reminders and a validation reserve.

### 5. Recovery should classify repeated causes, not only repeated commands

Evidence Harness detects identical commands and short command/observation cycles.
AHE additionally groups repeated error signatures, dependency probes, long
foreground waits, shallow validation, and post-success mutations. That is useful
because two syntactically different commands can express the same failed
hypothesis. OpenHands-style persisted state would also allow continuation after a
controller or provider interruption; current journal replay starts a fresh
container and replays commands but cannot resume model reasoning in place.

### 6. Long-running execution needs a handle

Timeout containment now cleans up Docker processes reliably, but
`BaseEnvironment.exec` is still one blocking request. Terminus-KIRA's marker
polling and Codex's PTY process IDs show the useful abstraction: start, poll,
stream bounded output, and terminate by handle. This would reduce guessed waits
and let the model inspect progress without issuing an equivalent command again.

## Contradictions and unknowns

1. **Leaderboard rank is not causal evidence.** The top four confidence intervals
   overlap and model stacks differ.
2. **AHE's paper score and current leaderboard score answer different questions.**
   The paper reports 77.0% for its frozen experimental harness; the current
   GPT-5.5 leaderboard entry is 84.7%. Neither number isolates the harness from
   model and run configuration.
3. **AHE repository scope is easy to overstate.** `ralph_loop.py`, token reminders,
   and provider failover are present in the broader evolution system, but public
   evidence does not establish that they ran in the official submitted task agent.
4. **LemonHarness is not reproducible from the linked repository.** Its paper
   describes detailed mechanisms, but the public LemonAgent README says code is
   still under internal review. The paper's 86.52% GPT-5.5 result is
   author-reported; the verified leaderboard entry is 84.5% and labeled
   “Multiple.”
5. **Capy's mechanism is unknown.** Its score and sampled runtime metadata are
   verifiable, but `--handoff` is not enough to infer context, planning, or
   completion architecture.
6. **Self-confirmation is not independent verification.** KIRA's checklist may
   improve behavior, but the same model still proposes and accepts completion.
7. **Compaction trades cost for evidence loss.** OpenHands and Terminus summarize
   old history; Codex warns that repeated compaction can reduce accuracy. Keeping
   immutable raw traces outside the model context is necessary for diagnosis and
   replay.
8. **Published efficiency claims are not uniformly comparable.** OpenHands's
   “up to 2x” cost reduction and KIRA's claimed false-completion reduction are
   author claims under their own setups.
9. **Evidence Harness's 59/89 is not a leaderboard result.** It combines 85 live
   trials and four receipt-driven replays across controlled recovery runs and
   evolving harness revisions. It is valuable engineering evidence, not a
   five-trial-per-task pass@1 submission comparable to the official board.

## Prioritized experiments

### P0: Calibrate completion on the 22 disagreement cases

Build a fixed retrospective suite from the 10 `verified/reward=0` and 12
`reward=1/not-verified` tasks. Record requirement, proposed check, reviewer
decision, receipt, final state, and verifier outcome. Test task-family-specific
semantic checks and canonical-entry-point warnings against this frozen set.

**Gate:** reduce total internal/external disagreements by at least 50%, with no
increase in `verified/reward=0`. Then run at least five fresh trials per selected
task before attributing a gain.

### P0: Run completion checks in an isolated snapshot

Allow compilers and programs to create temporary verification artifacts in a
snapshot or scratch copy, then compare the live deliverable before and after.
This resolves the current contradiction where the reviewer requests a
write-producing check that policy correctly forbids in the scored workspace.

**Gate:** convert known `reward=1` completion-protocol failures to internal
`verified`, while a byte-level live-workspace digest proves the check made no
scored-state changes.

### P0: Add a deadline-driven finalization phase

Reserve 10% of wall time, plus the existing environment calls, for finalization.
At the boundary, prohibit new broad exploration and unbounded mutations; permit
only focused repair, canonical checks, and publish-state preservation. Surface
phase and reserve in the prompt.

**Gate:** on the current budget-exhausted cohort, reduce unfinished deliverables
and completion repairs without lowering external reward or increasing median
runtime.

### P1: Add failure-family and publish-state middleware

Derive stable signatures from command purpose, return code, stderr class, and
observation fingerprint. After two same-family failures, require a new hypothesis.
After an evaluator-like check passes, warn before cleanup or unrelated mutation
and invalidate publish state only on an actual change receipt.

**Gate:** fewer repeated-failure calls and fewer post-success regressions, with
false warnings reported separately.

### P1: Make runs resumable from controller state

Checkpoint `RunState`, pending action identity, journal offset, budgets, and model
usage after every atomic step. On restart, reconcile the last command receipt
before continuing. Keep full replay as a separate disaster-recovery mode.

**Gate:** injected termination after every state transition resumes without
duplicating a change command, exceeding the original budget, or changing the final
receipt chain.

### P1: Introduce process handles for long-running commands

Add bounded `start`, `poll`, `read`, and `terminate` operations over a PTY or
background process. Preserve one-writer ordering and journal every handle
transition. Do not expose raw sleep duration as the only synchronization method.

**Gate:** long-running task fixtures finish sooner, emit bounded context, and
leave no child process after timeout or cancellation.

### P2: Enforce a shared token/cost budget

Count weighted input and output tokens across executor, reviewer, schema repair,
and any future helper model. Reserve enough for one completion proposal and its
review, then stop optional model work before that reserve is consumed.

**Gate:** lower p95 tokens and cost on the hard-task cohort without reducing
scored pass rate; report quality per million tokens as well as raw pass rate.

### P2: Replace plan strings with an evidence-linked task ledger

Represent each plan item as `pending`, `in_progress`, `blocked`, or `done`, with
requirement IDs and receipt hashes. Preserve only one active item and force a
replan when its hypothesis is contradicted.

**Gate:** fewer turns spent rediscovering old failures and fewer finish proposals
with unmapped requirements.

## Recommended order

Run the two completion experiments first because the local evidence already shows
22 calibration failures. Add the finalization phase next because budget exhaustion
is the dominant controllable failure boundary. Then implement failure-family
recovery and resumability. Process handles, token budgeting, and a richer plan
ledger should follow only after the first experiments establish stable outcome
metrics.

The durable design principle is: **keep model judgment flexible, but make state,
budget, execution, provenance, and permission to finish deterministic.**
