# PrefixBench development analysis

## Problem

The development campaign records every task, mutation case, audit, and reduced
counterexample. Its canonical JSON is about 1.5 MB, so research-facing values
are difficult to inspect and easy to quote with the wrong denominator.

The analysis layer turns that fixed campaign into a small, typed report. It
reads no journal, test task, provider configuration, Docker state, or
production controller. The report is descriptive development evidence. It
does not complete RQ2, RQ3, or RQ4.

## Usage

Build the fixed report:

```bash
uv run python scripts/prefixbench_analysis.py build
```

Verify its source binding, schema, canonical encoding, formulas, and bytes:

```bash
uv run python scripts/prefixbench_analysis.py check
```

Python callers use the same fixed boundary:

```python
from evidence_harness_mutation import (
    build_prefixbench_development_analysis,
    check_prefixbench_development_analysis,
)

report = build_prefixbench_development_analysis(project_root)
assert report.cases.offline_violations.count == 28
assert report.cases.offline_violations.population == 91
assert check_prefixbench_development_analysis(project_root) == ()
```

The API has no split, input, model, provider, journal, or execution option.

## Source contract

Analysis v1 accepts only:

```text
evaluation/prefixbench-v1-development-offline-campaign.json
SHA-256 395c10022f9682ff31246701e058a94aa983b1f236c9f3a6b164e8dc62c6aa83
```

The loader requires a regular file, parses it as
`PrefixBenchDevelopmentCampaignReport`, and requires its bytes to equal the
model's canonical bytes. The resulting `PrefixBenchFileBinding` records the
observed path, byte count, and SHA-256.

The campaign already binds the development readiness report, canonical
collection, task matrix, producer source, frozen mutation protocol, 28 journal
hashes, 168 cases, and 28 inline counterexamples. The analysis report does not
copy those values. One campaign digest binds them transitively.

The checker always rebuilds from the campaign JSON and compares canonical
analysis bytes. It does not call `check_prefixbench_development_campaign()`,
which may inspect raw journals when they exist.

## Metric contract

`ExactRate` stores three values:

```text
count
population
reduced fraction
```

The count and population preserve the observed denominator. The reduced
fraction provides a canonical exact ratio without binary floating point. For
example, the offline violation rate is stored as `28 of 91` and `4/13`.

Campaign cases use this partition:

```text
scheduled = mutation_not_applicable + offline_invalid + applicable
applicable = oracle_equivalent + offline_violation + other_oracle_change
```

`OfflineInvalidCase` is not an `ApplicableCampaignCase`. This distinction is
part of analysis protocol v1 even though the current invalid count is zero.

Each operator row preserves its complete outcome counts, its applicable cases
among scheduled cases, and its offline violations among applicable cases.
Reduction dimensions pool before and after sizes across all offline violation
counterexamples. They do not average case-level percentages.

Changing a denominator, case classification, aggregation method, split,
baseline, or claim scope requires a new analysis protocol ID.

## Development result

The fixed campaign produced these case counts:

| Observation | Count and population | Exact fraction |
|---|---:|---:|
| Applicable cases | 91 / 168 | 13 / 24 |
| Mutation not applicable | 77 / 168 | 11 / 24 |
| Offline invalid | 0 / 168 | 0 / 1 |
| Oracle equivalent | 63 / 91 applicable | 9 / 13 |
| Offline violation | 28 / 91 applicable | 4 / 13 |
| Other oracle change | 0 / 91 applicable | 0 / 1 |

The operator rows show different applicability:

| Operator | Invariant | Applicable / scheduled | Violations / applicable |
|---|---|---:|---:|
| `stale_evidence_epoch` | I1 | 26 / 42 | 7 / 26 |
| `reorder_check_receipts` | I2 | 23 / 42 | 7 / 23 |
| `review_timeout_fallback` | I3 | 16 / 42 | 7 / 16 |
| `cross_candidate_evidence` | I4 | 26 / 42 | 7 / 26 |

Task and attempt observations are:

- 25 of 28 tasks contain a projected completion attempt.
- 7 of 28 tasks contain a verified attempt.
- 7 of 39 projected attempts are verified.
- 12 of 28 tasks contain an applicable case.
- 7 of 28 tasks contain an offline violation.

The 28 reduced counterexamples have these pooled sizes:

| Dimension | Before | After | Retained fraction |
|---|---:|---:|---:|
| Events | 1,996 | 230 | 115 / 998 |
| Recursive payload members | 35,582 | 6,465 | 6,465 / 35,582 |
| Compact canonical JSON bytes | 4,044,814 | 716,196 | 358,098 / 2,022,407 |

The reducer evaluated 15,587 candidates, ran 7,938 audits, and accepted 568
reductions. These are algorithm work counts, not wall-clock or cost
measurements.

## Claim boundary

The report describes only the fixed development campaign. Its typed claim
block marks these items as `not_evaluated`:

- RQ2 comparisons with random, AgentChaos-style, or stateless baselines;
- phase-specific target-state reachability;
- RQ3 production test-suite mutation score or killed/survived results;
- RQ4 timing, token, or live-rerun savings;
- model-call and container-call telemetry;
- inferential statistics;
- test-split or general conclusions.

`oracle_equivalent` means the mutated and baseline offline audit reports are
equal. It does not prove semantic equivalence. `offline_invalid` covers
offline pipeline failures and is not a general schema-validity measure.
Phase witnesses remain readiness metadata, not campaign cutoffs.

## Shape

`src/evidence_harness_mutation/prefixbench_analysis.py` owns:

- the versioned analysis protocol;
- exact fractions and count rates;
- case, task, operator, and reduction observations;
- the fixed campaign loader;
- pure aggregation;
- canonical build and check operations.

The public package exports only:

```python
PrefixBenchDevelopmentAnalysisReport
build_prefixbench_development_analysis(project_root)
check_prefixbench_development_analysis(project_root, report_path=None)
```

The CLI owns fixed paths, atomic writes, summaries, and process exit codes.
Tests cover the formulas, validators, source-only build, stale output, command
shape, and exact committed values.

## Synthesis decision

Three independent candidates converged on a fixed artifact-only module. The
cross-review scores were A 29/30, B 28/30, and C 26/30. Candidate A became the
base because it preserved per-operator applicability and used the source
type's correct case partition.

Candidate B contributed the named reduction dimension with before, after, and
retained fraction. Candidate C contributed a narrow list of campaign fields
that aggregation may inspect. The final design rejects B and C's
`applicable = audit_evaluable + offline_invalid` formula because
`OfflineInvalidCase` is outside the applicable-case type hierarchy.

The final design also rejects a hash of the analysis implementation. A
versioned protocol ID and literal formula contract bind semantics without
invalidating the result after a behavior-preserving refactor. The fixed
campaign SHA binds the observation input.

## Tradeoffs accepted

- The report is tied to one development campaign in exchange for preventing
  silent test-split or replacement-campaign analysis.
- It stores count, population, and reduced fraction in exchange for preserving
  both the original denominator and canonical arithmetic.
- It reparses the campaign on every check in exchange for avoiding caches and
  environment-dependent state.
- It publishes one JSON artifact rather than a generated Markdown report in
  exchange for one authoritative machine-readable result.

## Alternatives considered

A generic analysis function with path, split, and metric options would expose
source selection and claim policy to callers. The fixed API hides those
decisions and cannot select the test split.

Adding the metrics to `prefixbench_campaign.py` would modify a file in the
frozen mutation protocol. It would also mix campaign execution evidence with
downstream interpretation.

A Markdown-only report would make denominators, source hashes, and claim
limits string conventions. The canonical JSON report makes them validation
errors.
