# Historical-7 full-suite design

## Problem

Historical-7 executes seven vulnerable/fixed pairs against archived production code without
telling the worker which outcome is expected. The report binds every relevant source input and
remains byte-identical across repeated runs.

## Usage

The public API remains one operation:

```python
report = run_historical7(
    HistoricalRunRequest(
        repo_root=Path.cwd(),
        output_path=Path("evaluation/historical-7.json"),
    )
)

assert report.schema_version == 2
assert report.suite_id == "historical-7"
assert len(report.results) == 14
```

The CLI still maps `passed`, `failed`, and `error` to exit codes 0, 1, and 2:

```bash
uv run python scripts/run_historical7.py
```

Callers cannot select cases, revisions, scenarios, or expected outcomes.

## Shape

The implementation uses two package modules and the existing process worker:

```text
historical_cases.py
  HistoricalPropertyId
  schema-v2 manifest, scenario, observation, binding, and report models
  frozen seven-case catalog validation

historical.py
  run_historical7()
  Git archive materialization
  worker invocation and response validation
  seven pure classifiers
  canonical report writing

historical_mutation_probe.py
  exact request validation
  three mechanism-level probes
  archived production import guard
  normalized observations only
```

This is a knowledge split. It does not split the transaction into load, validate, run, and save
layers. `run_historical7()` remains the only public operation.

The worker uses three mechanisms:

1. A scripted `EvidenceLoop.run` probe for five loop-control cases.
2. A reordered-receipt `EvidenceGate.decide` probe.
3. A fake-clock `EvidenceLoop.run` probe for the wall-time deadline case.

Seven parent classifiers remain separate because each historical property has a different truth
table. Every near miss is `inconclusive`. Classifiers cannot inspect the revision role, commit, or
expected outcome.

`HistoricalPropertyId` is separate from the offline trace oracle's `InvariantId`. The two
taxonomies overlap for two cases but have different scope.

The worker request contains only case identity, commit, tree, manifest digest, archived source
digest, source root, and scenario. It never contains the revision role, expected outcome, match
flag, property statement, or classifier data.

Each materialized revision binds:

- the full commit and tree;
- the commit embedded in the Git archive;
- `pyproject.toml`;
- `uv.lock`;
- every regular `src/evidence_harness/**/*.py` file in sorted path order.

The parent and worker independently calculate the same length-framed source-set digest. The report
also binds the manifest, worker, coordinator, and schema module before and after execution.

The seven cases retain the existing two-case prefix, followed by:

1. `review_receipt_order`, `ba2cbae` to `5f64d66`;
2. `review_quota_no_bypass`, `ba2cbae` to `5f64d66`;
3. `failed_change_progress`, `ba2cbae` to `5f64d66`;
4. `max_repairs_exact`, `ba2cbae` to `5f64d66`, with `max_repairs=1` and two failed finishes;
5. `wall_time_command_deadline`, `5f64d66` to `3a3b83f`, through the complete public
   `EvidenceLoop.run` path.

The wall-time scenario uses a fake clock. Work starts at 850 seconds in a 1,000-second run and
requires 80 seconds. The vulnerable revision gives the command 150 seconds. The fixed revision
gives it 50 seconds, records a timeout at 900 seconds, enters wall-clock finalization, and consumes
the same scripted stop decision.

## Synthesis decision

Arena candidate A is the base because it had the correct revision matrix, public production
entrypoints, parent-only classification, and complete source binding. The retry cross-judge scored
A, B, and C at 26, 23, and 22 out of 30. The first judge produced no artifact and is recorded as a
dropout.

The final design takes B's two-module knowledge split. It takes C's three mechanism-level worker
registry but keeps that registry in the existing worker script. It also removes callable
signatures, API support flags, Python versions, and Pydantic versions from behavioral observations
and canonical output.

Candidate B's wall-time revisions, private entrypoint, and real-time probe were rejected. Candidate
C's four-module worker stack and private `_handle_execute` probe were rejected. Candidate A's
single 1,200-line coordinator and seven parallel worker implementations were rejected.

## Tradeoffs accepted

- We accept explicit schema types for seven frozen cases in exchange for rejecting mismatched
  case/scenario/observation combinations at the boundary.
- We accept duplicate source-digest code across the parent and worker in exchange for an
  independent process-boundary check.
- We accept sequential execution in exchange for stable cell and error ordering.
- We bind archive contents by commit, tree, embedded commit, and normalized source set. The report
  may record the tar digest, but the manifest does not freeze it because tar bytes can depend on
  Git implementation details.

## Alternatives considered

A single expanded `historical.py` keeps the file count low but would mix schema, frozen catalog,
classification, Git plumbing, and report assembly in more than 1,100 lines.

A four-module protocol, classifier, probe, and coordinator package spreads one fixed experiment
across too many files and turns the worker entry script into a pass-through.

A generic assertion or scenario language would move classification policy toward the worker and
create a second language for seven fixed cases.

## Risks

- The runner requires all five historical commits to remain available in the local Git clone.
- The installed dependency set is trusted only because every selected revision has the same frozen
  `uv.lock` digest; a future suite with different locks needs per-revision environments.
- New historical production shapes must produce `inconclusive` until the schema and classifier are
  revised deliberately.

## Implemented result

All 14 cells match the frozen matrix. Every vulnerable revision survives its historical trigger,
and every fixed revision kills the same trigger. The test suite runs the complete matrix twice and
requires byte-identical reports. It also checks request non-disclosure, archived source bindings,
control-file bindings, and the mechanism-specific observations for all seven properties.
