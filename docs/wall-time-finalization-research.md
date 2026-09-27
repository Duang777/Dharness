# Wall-time finalization research

Research cutoff: 2026-09-27.

## Question

How should Evidence Harness reserve enough wall time for completion without
claiming that one fixed schedule is universally optimal?

## Primary-source findings

| Source | Direct evidence | Limit |
|---|---|---|
| [LemonHarness technical report, section 3.4](https://arxiv.org/html/2606.24311#S3.SS4), 2026-06-23 | The runtime exposes elapsed and remaining time each turn. Its schedule is Explore at 0% to 30%, Implement at 30% to 60%, Validate at 60% to 90%, and Reserve at 90% to 100%. The paper calls the final `0.1T` a hard reserve and instructs the model to stop new state-changing actions. Phase transitions are monotonic. | The public LemonAgent repository does not expose a clearly matching submitted harness. The paper does not isolate the score contribution of the 10% reserve. Its final restriction is described as an instruction to the model, not a controller rejection rule. |
| [Python 3.12 asyncio task documentation](https://docs.python.org/3.12/library/asyncio-task.html#timeouts), accessed 2026-09-27 | `asyncio.timeout()` enforces a deadline by cancelling the current task and turns the cancellation into `TimeoutError` outside the context. The cancellation guidance requires cleanup in `finally` and propagation after cleanup. | A logical timeout does not prove that an external process has stopped. Cleanup can outlive the deadline, so the environment adapter must also receive the shortened timeout and drain the process safely. |
| [OpenHands agent architecture](https://docs.openhands.dev/sdk/arch/agent), accessed 2026-09-27 | The agent runs atomic, event-driven steps. Each action produces an observation, and a step can be interrupted or resumed through conversation state. | The reviewed architecture page does not specify a fixed final wall-time reserve or an evaluator-style completion gate. |
| [Codex CLI features](https://developers.openai.com/codex/cli/features), accessed 2026-09-27 | Codex persists transcripts, supports resume, and exposes completed actions in the session transcript. | The reviewed feature documentation does not establish a fixed wall-time finalization percentage. It cannot justify copying LemonHarness's 10% value as an industry standard. |

The only direct source found for a final 10% phase is the LemonHarness paper.
The transferable mechanism is monotonic phase control based on a deadline. The
exact ratio remains a hypothesis that needs local measurement.

## Local retrospective evidence

`scripts/analyze_failure_traces.py` applies the same 10% boundary to the frozen
89-task journals. Among the 10 reward-zero `budget_exhausted` tasks:

- 3 reached the wall-time boundary;
- 12 model decisions occurred after the boundary;
- those decisions proposed 6 state-changing commands;
- none proposed `finish`.

`build-pov-ray` accounts for 10 late decisions and 5 change commands.
`train-fasttext` accounts for 2 late decisions and 1 change command.
`extract-moves-from-video` crossed the boundary inside a long-running command,
so it had no decision after the boundary. That case proves that checking time
only between model turns does not preserve the reserve.

None of the 10 reward-zero tasks that ended internally as `verified` reached the
same wall boundary. This is useful cohort evidence, but it does not show that
successful tasks outside this failure-only sample are unaffected.

## Transfer to Evidence Harness

The implemented policy uses the paper's 10% value as a private experiment:

1. A run enters the existing sticky finalization state when either three model
   turns or 10% of wall time remains.
2. The controller records `turn_budget`, `wall_clock`, or both as transition
   triggers and exposes the effective reserve in the executor prompt.
3. Ordinary executor model calls use the start of the finalization window as
   their deadline. A timeout at that boundary starts a new finalization decision
   instead of ending as a model-service failure.
4. A model response that crosses the wall boundary is checked against the new
   finalization permissions before any command runs.
5. Ordinary work commands also use the finalization boundary as their deadline.
   Bootstrap, completion checks, and the one permitted finalization repair
   continue to use the run deadline.
6. A multi-command work batch stops before its next command if the prior command
   reaches the boundary.

This is stricter than the paper's prompt-only description. Evidence Harness
already owns action admission and command timeouts, so controller enforcement is
testable and cannot depend on the model following prose.

## Risks and next evidence

- The 10% ratio may be too small for a slow completion check plus semantic
  review, or too large for tasks whose final useful build dominates runtime.
- A command adapter that ignores its timeout can delay cleanup past the logical
  boundary. The shell runner waits for cleanup to avoid leaked processes.
- A fixed ratio does not account for model latency, check count, or command
  history. An adaptive reserve should wait for measured latency distributions.
- Retrospective traces show which actions would be blocked, not what the model
  would do after receiving the finalization prompt.

The next valid gate is a same-model regression on the three wall-boundary tasks,
followed by a broader sample. Compare reward, stop reason, unfinished artifacts,
finish attempts, repairs, and runtime. Until that run exists, the canonical
score remains 59/89.
