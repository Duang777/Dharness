# Completion calibration design

## Problem

The canonical 89-task result set contains two independent completion signals:
the Harness stop reason and the external verifier reward. They disagree on 22
live trials. A useful calibration artifact must preserve the evidence behind
each decision without assigning current receipt-first semantics to old
review-first journals.

The source manifest hashes `result.json` and `config.json`, but not
`events.jsonl`. Sanitized trial snapshots omit journals. The corpus builder must
therefore verify every canonical source, derive each sibling journal, add a
journal hash, and reject ambiguous event histories.

## Usage

Build the frozen corpus and its calibration report:

```bash
uv run python scripts/completion_calibration.py build \
  --corpus-out evaluation/completion-disagreements.json \
  --report-out evaluation/completion-calibration.json
```

Check that committed artifacts still match their sources:

```bash
uv run python scripts/completion_calibration.py check \
  --corpus evaluation/completion-disagreements.json \
  --report evaluation/completion-calibration.json
```

The analyzer reports historical agreement only. It does not project benchmark
scores. Checks that never ran appear as experiment candidates, not successful
policy decisions.

## Shape

`scripts/completion_calibration.py` owns three operations:

```python
def build_completion_corpus(
    canonical_path: Path,
    matrix_path: Path,
    project_root: Path,
    *,
    expected: CohortExpectation,
) -> dict[str, Any]: ...


def analyze_completion_corpus(corpus: dict[str, Any]) -> dict[str, Any]: ...


def dump_json(value: object) -> bytes: ...
```

The builder is the filesystem boundary. It validates all 89 canonical rows
before selecting live runs where:

```text
(reward == 1.0) XOR (stop_reason == "verified")
```

The output contains no timestamp or absolute path. Cases remain in canonical
order. Each case binds its result, config, and journal bytes by SHA-256 and
preserves:

- the original instruction and run options;
- every finish proposal and requirement mapping;
- metadata and output digests for the six supporting receipts available at each
  proposal;
- the review, rejection, check receipts, and verification receipt in that
  attempt's event window;
- the terminal Harness state and external verifier outcome.

The committed corpus omits receipt `stdout` and `stderr` excerpts. It retains
their byte counts and SHA-256 values, and it reapplies key-aware redaction to
all other strings. The raw journal hash still binds the omitted source bytes
when a local audit needs them.

The legacy decoder uses JSONL line order. A finish attempt starts at an
`agent_decision` with `action=finish` and ends before the next
`agent_decision` or at `run_finished`. It never pairs by timestamp. Every check
receipt must match the proposed check by identifier, script, purpose, working
directory, and order.

The analyzer receives only the frozen object. Its deterministic policies do not
receive reward or terminal labels while deciding:

- `recorded_terminal_v1` reproduces the historical Harness decision.
- `same_attempt_review_required_v1` rejects an accepted terminal attempt when
  that same attempt has no accepting review. This detects the legacy
  review-budget bypass without pretending that a pre-check review is equivalent
  to current receipt-first review.

The report joins decisions to external labels only after policy evaluation. It
keeps write-policy candidates separate. An isolation candidate means a proposed
check was blocked before execution and should be tested in a disposable
workspace. It does not mean the check or a later semantic review would pass.

Historical corpus types stay in the analysis script. They do not enter
`evidence_harness.protocol`, so runtime schema changes cannot reinterpret
frozen evidence.

## Synthesis decision

Candidate 1 supplied the base: one deep build/verify/analyze interface, strict
source binding, and explicit recorded versus counterfactual result types.
Candidate 2 contributed full 89/85/4 population validation, prefix-only policy
views, and a rule that downgrades attempt attribution after later state-changing
commands. The larger package layouts from both candidates were rejected because
this repository already keeps offline evaluation logic in `scripts/`, and 22
cases do not justify a new public package hierarchy.

## Tradeoffs

- The corpus omits receipt output text to avoid copying runtime credentials.
  Receipt metadata and digests keep the policy analysis independent of
  gitignored run directories.
- Journal hashes attest to the bytes present now. The original canonical
  manifest did not bind journals at collection time.
- A disagreement-only corpus measures known failure cases. It cannot estimate
  overall accuracy or collateral changes on the 67 agreement cases.
- Legacy pre-check reviews remain historical facts. A current receipt-first
  acceptance still requires a prospective run.

## Verification

Tests cover source tampering, replay exclusion, deterministic bytes, legacy
attempt pairing, terminal event order, receipt identity, output omission,
malformed journals, label-blind policy inputs, no-review bypass detection, and
separation of isolation candidates. The delivery gate rebuilds both artifacts
from local raw runs when they are available. Without raw runs, it checks every
case's result/config path, hash, task provenance, and verifier label against the
canonical manifest.
