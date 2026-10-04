# PrefixBench held-out test campaign

## Problem

The 61-task PrefixBench test matrix is frozen, but no test result has been
collected. The held-out protocol must therefore be committed before collection
and must remain independent of the existing development adapter. The new
adapter reuses the frozen journal loader, attempt projection, operators,
oracle, reducer, and single-prefix campaign without changing any source file
bound by the development protocol.

The existing readiness status is not the test admission decision. A valid
test-only report can have status `phase_coverage_incomplete` because the
coverage policy asks for development witnesses. Test admission instead
requires 61 live schema-2 tasks, 61 source admissions, no exclusions, 61 test
identities, no development identities, the fixed profile, and one producer.

## Usage

Create the protocol before any held-out run or output exists:

```bash
uv run python scripts/prefixbench_test_campaign.py freeze
```

Commit the adapter, protocol, CLI, tests, and this document. Then verify the
committed preregistration:

```bash
uv run python scripts/prefixbench_test_campaign.py preflight
```

The preflight is read-only. It checks the committed test matrix, split
lineage, development protocol lineage, collection implementation, mutation
implementation, and runtime source. It also requires the fixed run directory
and all held-out outputs to be absent.

Run the registered collection:

```bash
uv run python scripts/run_full_evaluation.py \
  --model provider/model \
  --env-file "$PROVIDER_ENV_FILE" \
  --matrix evaluation/matrix-prefixbench-test.json \
  --run-name prefixbench-v1-test-20261002 \
  --collection-profile prefixbench-v1
```

Resume with the same command after an interruption. A completed result is
final regardless of reward, status, or exception. Do not create an
outcome-based retry matrix.

After all 61 tasks finish, build and commit the source artifacts:

```bash
uv run python scripts/collect_evaluation_results.py \
  runs/terminal-bench-2/prefixbench-v1-test-20261002 \
  --matrix evaluation/matrix-prefixbench-test.json \
  --collection-profile prefixbench-v1 \
  --manifest-out evaluation/prefixbench-v1-test-canonical.json

uv run python scripts/prefixbench.py build \
  --canonical evaluation/prefixbench-v1-test-canonical.json \
  --matrix evaluation/matrix-prefixbench-test.json \
  --expected-task-count 61 \
  --out evaluation/prefixbench-v1-test-readiness.json
```

Build and check the descriptive offline campaign:

```bash
uv run python scripts/prefixbench_test_campaign.py build
uv run python scripts/prefixbench_test_campaign.py check
```

The CLI exposes no split, task, retry, operator, schedule, reducer, claim, or
output-path option.

Python callers use complete operations:

```python
from evidence_harness_mutation.prefixbench_test_campaign import (
    build_prefixbench_test_campaign,
    check_prefixbench_test_campaign,
    freeze_prefixbench_test_protocol,
    preflight_prefixbench_test_campaign,
)

protocol = freeze_prefixbench_test_protocol(project_root)
preflight = preflight_prefixbench_test_campaign(project_root)
report = build_prefixbench_test_campaign(project_root)
errors = check_prefixbench_test_campaign(project_root)
```

## Shape

`src/evidence_harness_mutation/prefixbench_test_campaign.py` is one
test-specific adapter. It owns fixed paths, preregistration, collection
evidence, test admission, report aggregation, and artifact checking. It does
not import `prefixbench_campaign.py`, and the development adapter is absent
from the test source manifest.

The public API has four operations. Its internal flow is:

```text
freeze protocol
  -> reconstruct the 28/61 split
  -> bind collection and mutation sources
  -> write canonical protocol

preflight
  -> validate committed protocol bytes
  -> attest the current runtime source
  -> require no held-out artifacts

build
  -> load protocol before held-out data
  -> validate canonical and readiness inputs
  -> validate run-config and progress
  -> prove protocol bytes exist in the producer revision
  -> run one full-journal campaign per task
  -> derive summaries

check
  -> validate committed artifact bindings
  -> accept when all raw sources are absent
  -> reject partial raw-source availability
  -> rebuild and compare bytes when all raw sources exist
```

External JSON and Git output is parsed at the module boundary. The internal
campaign loop receives a validated immutable cohort.

## Preregistered protocol

`experiments/prefixbench-v1/test-mutation-protocol-v1.json` records:

- protocol ID `prefixbench-v1-test-offline-mutation-v1`;
- the exact 61 task names in matrix order;
- the matrix hash and the commit that froze it;
- the source 89-task matrix, split readiness, and development matrix;
- development protocol file and source-set lineage;
- model, API route, profile options, serial execution, and fixed run root;
- resume-only retry behavior and acceptance of every completed result;
- the runtime source hash and a separate orchestration source hash;
- operator order, expected invariant mapping, all five outcomes, and audit
  counts;
- the full-journal prefix rule and default request schedule;
- reducer algorithm, minimality rule, and inline counterexample policy;
- canonical JSON settings;
- the descriptive claim boundary;
- one source manifest for collection code, shared mutation semantics, and the
  test adapter.

The protocol contains no producer commit or tree. Those values do not exist
until the protocol commit becomes the producer revision.

## Chronology and collection evidence

Preflight requires the protocol and all bound source files to match `HEAD`.
After collection, campaign admission requires all 61 readiness rows to share
one producer commit, tree, and runtime source hash.

The adapter then verifies:

1. The producer commit resolves to the attested tree.
2. The preregistration commit is an ancestor of the producer.
3. The producer commit contains the exact test protocol, test matrix, and
   development protocol bytes.
4. `run-config.json` matches the registered matrix, model, profile, producer,
   agent argument hash, orchestration source hash, and `start_at=None`.
5. `progress.jsonl` contains exactly one completion for every task in matrix
   order and ends with a complete 61-task summary.
6. Every progress result path matches the canonical result path.

Repeated starts and failed launches are allowed before a task completes.
They model process interruption and resume. A second completion for a task,
an outcome-selected rerun, a partial start, or another run root is rejected.

## Offline execution

For each admitted task, the adapter reads only the journal bound by readiness:

```python
prefix = load_state_prefix(
    journal_bytes,
    source_commit=producer.commit,
    expected_journal_sha256=journal.sha256,
)
projected_attempts = len(project_completion_attempts(prefix.events))
campaign = run_offline_campaign(prefix)
```

`run_offline_campaign()` receives no request override. The task model
independently checks the default attempt-major, operator-minor request order.
Every task uses the complete journal. Every summary is derived from nested
cases.

## Claim boundary

The first held-out artifact is descriptive. It may report task counts,
attempt counts, mutation classifications, and reduced counterexamples. It
states only that held-out outcomes did not influence selection, order, retry
policy, operators, oracle, reducer, formulas, or claim scope.

The artifact marks these claims `not_evaluated`:

- RQ2 baseline superiority;
- RQ3 production mutation score;
- RQ4 live cost savings;
- inferential statistics.

A later analysis needs its own preregistered protocol.

## Module map

| File | Responsibility |
|---|---|
| `src/evidence_harness_mutation/prefixbench_test_campaign.py` | Test protocol, preflight, admission, collection evidence, campaign, and checker |
| `scripts/prefixbench_test_campaign.py` | Fixed commands, atomic writes, summaries, and exit codes |
| `experiments/prefixbench-v1/test-mutation-protocol-v1.json` | Canonical preregistration |
| `tests/mutation/test_prefixbench_test_campaign.py` | Domain and evidence tests |
| `tests/test_prefixbench_test_campaign_script.py` | CLI tests |
| `evaluation/prefixbench-v1-test-canonical.json` | Post-collection canonical source artifact |
| `evaluation/prefixbench-v1-test-readiness.json` | Post-collection source admission artifact |
| `evaluation/prefixbench-v1-test-offline-campaign.json` | Post-collection descriptive campaign |

## Synthesis decision

Three independent designs converged on a separate test adapter. Candidate 2
was selected because it binds protocol chronology to the actual producer and
turns retry policy into checked run evidence.

Candidate 1 contributed full split reconstruction and guarded protocol
creation. Candidate 3 contributed the explicit preregistration ancestor and
negative tests that prevent preflight from reading held-out outputs or the
report from acquiring unregistered claims.

The design rejects a configurable cohort runner. Such a runner would modify a
development-bound source file and expose experimental choices to callers.

## Tradeoffs accepted

- We accept duplicate cohort wrapper types in exchange for an independent
  test source manifest.
- We accept a fixed model, API route, run root, and collection date in
  exchange for removing post-preregistration choices.
- We accept one large report in exchange for retaining every case and reduced
  counterexample in one artifact.
- We accept Git-history checks in exchange for proving that the protocol
  predates the producer revision.
- We accept artifact-only validation when raw runs are absent. Full
  recomputation still requires every bound raw file.

## Open questions and risks

- Will the publication package include raw result, config, journal,
  run-config, and progress files for independent recomputation?
- Will the fixed API route remain available for the collection window?
- Should later RQ2, RQ3, or RQ4 analysis use one combined preregistration or
  separate protocols?
- Could the 61-task report exceed repository artifact-size limits?

## Next implementation step

Commit the preregistration, run preflight from that commit, then start the
fixed collection only after a provider environment file is available.
