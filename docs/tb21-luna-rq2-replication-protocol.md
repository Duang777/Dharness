# TB2.1 Luna RQ2 Replication Protocol

## Decision

This protocol preregisters an independent RQ2 replication with
`openai/modelhub/gpt-5.6-luna` on the existing frozen 61-task
Terminal-Bench 2.1 cohort.

It does not extend the Terra sensitivity study. The Terminal-Bench 2.0 fields
in the cohort record how task names were selected; only the Terminal-Bench 2.1
source identities, package digests, task trees, raw runs, and journals enter
the Luna analysis.

The protocol forbids:

- Terra or Terminal-Bench 2.0 outcomes as analysis inputs.
- Cross-model and cross-version inferential tests.
- Dataset-sensitivity or model-change causal claims.
- Replacement of the existing main-analysis conclusions.

## Frozen Contract

The protocol ID is `thesis-tb21-luna-rq2-replication-v1`. It binds:

- Model: `openai/modelhub/gpt-5.6-luna`.
- Cohort: `evaluation/matrix-prefixbench-tb21-sensitivity.json`, first
  committed at `69f7fb2da64f3f465f1b72a43469007e2cf1cc2a`.
- Dataset snapshot: Terminal-Bench 2.1 commit
  `5fc7d3b91d27ef0304b4eabe5002e6578160b817`.
- Five methods, four mutation operators, outcome taxonomy, task budgets, and
  the complete-cohort rule from RQ2.
- Choice namespace: `thesis-tb21-luna-rq2-replication-v1`.
- Bootstrap namespace:
  `thesis-tb21-luna-rq2-replication-v1/bootstrap-rq2-v1`.
- Seed `20261003` and 10,000 bootstrap resamples.
- A separate four-contrast Holm family.

The run produces a new raw root, canonical report, readiness report, offline
campaign, and final Luna-only RQ2 report. None of those paths overlap with the
Terra experiment.

## Chronology

The repository enforces three distinct stages:

1. `P`: this protocol first appears, with no executable or outcome path in the
   filesystem or any Git ref.
2. `E`: a later commit freezes all executable source bytes and the run plan,
   while every outcome path remains unused.
3. `O`: a later commit records all outcome paths after collection and
   derivation.

The required ancestry is `P < E < O`. Filesystem checks run before reading
protocol inputs so an existing file, directory, or broken symlink cannot be
overlooked. All-ref history checks reject a previously used path even if it
was deleted.

## Provider Boundary

Collection accepts credentials only from inherited `OPENAI_API_KEY` and
`OPENAI_BASE_URL`. The endpoint is bound by SHA-256. Provider variables are
removed from build and preflight subprocesses.

Credential files, credential command-line arguments, and persisted values are
forbidden. The run seal may contain only the variable names, presence flags,
and a domain-separated bundle digest. Harbor's saved trial configuration and
embedded result configuration must both identify the frozen Luna model before
a result becomes terminal.

## Artifact Graph

```text
frozen cohort + P
        |
        v
E run plan + patched Harbor runtime
        |
        v
raw receipts -> canonical -> readiness -> offline campaign -> Luna RQ2 report
```

Inference requires 61 admitted schema-2 journals. A terminal Harbor result is
absorbing regardless of reward or exception. Only a recorded process
interruption may create another attempt. Integrity, model, runtime, or
credential-boundary failures stop the formal run and are never selected away
through retries.
