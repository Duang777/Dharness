# TB2.1 Luna RQ2 Replication Executable

## Scope

This executable implements protocol
`thesis-tb21-luna-rq2-replication-v2`. It runs the frozen 61-task
Terminal-Bench 2.1 cohort with `openai/modelhub/gpt-5.6-luna` and produces a
Luna-only RQ2 report.

It supersedes the V1 executable, which stopped during runtime-source
attestation before any model call. V1 raw files remain preserved locally and
are never retried or used as inputs.

Terra outcomes and Terminal-Bench 2.0 outcomes are not inputs. The report does
not make dataset-sensitivity, cross-model, cross-version, or model-change
causality claims.

## Frozen Execution

Freeze the executable before any Provider call:

```bash
uv run python scripts/tb21_luna_rq2_v2.py freeze
git add \
  docs/tb21-luna-rq2-replication-executable-v2.md \
  scripts/tb21_luna_rq2_v2.py \
  tools/harbor-tb21-luna-v2 \
  src/evidence_harness_mutation/_tb21_luna_analysis_v2.py \
  src/evidence_harness_mutation/_tb21_luna_harbor_v2.py \
  src/evidence_harness_mutation/_tb21_luna_manifest_v2.py \
  src/evidence_harness_mutation/tb21_luna_rq2_v2.py \
  src/evidence_harness_mutation/tb21_luna_runtime_v2.py \
  tests/mutation/test_tb21_luna_analysis_v2.py \
  tests/mutation/test_tb21_luna_harbor_v2.py \
  tests/mutation/test_tb21_luna_manifest_v2.py \
  tests/mutation/test_tb21_luna_runtime_v2.py \
  tests/test_tb21_luna_rq2_v2_script.py \
  experiments/prefixbench-v1/tb21-luna-rq2-replication-executable-v2.json
git commit
```

The executable commit must descend from the protocol commit and precede every
outcome path.

## Provider Boundary

The formal collector reads only inherited `OPENAI_API_KEY` and
`OPENAI_BASE_URL`. There is no credential file or credential CLI option. The
endpoint must match the protocol's frozen SHA-256.

Run the Provider-free preflight through the content-addressed runtime:

```bash
tools/harbor-tb21-luna-v2 preflight --tb21-checkout /path/to/terminal-bench-2-1
```

Then export the two variables in a private shell and start collection:

```bash
tools/harbor-tb21-luna-v2 collect --tb21-checkout /path/to/terminal-bench-2-1
```

Build and preflight subprocesses receive no Provider variables. Collection
passes only the two allowlisted variables. The run seal records key names,
presence, the endpoint digest, and a domain-separated bundle digest, never
the values.

The content-addressed installed wheel remains the collector control plane.
For each Harbor child only, the collector sets `PYTHONPATH` to the executable
project's committed `src` directory. The agent therefore resolves its source
root to the Git worktree attested in the run seal.

## Acceptance

Each task has one terminal result. Only an interrupted process may advance to
another attempt. A reward, exception, model-proof failure, source-integrity
failure, or credential-boundary failure is never retried.

A terminal result must prove the frozen model in all of these locations:

- requested invocation;
- saved Harbor config;
- result-embedded config;
- result model info and model-usage key.

Harbor may write both `run/result.json` and
`run/<trial>/result.json`. The job-level aggregate is excluded. Exactly one
trial-level result is required, and live launch and recovery use the same
parser.

After all 61 tasks are terminal, build and verify the artifact graph:

```bash
uv run python scripts/tb21_luna_rq2_v2.py build
uv run python scripts/tb21_luna_rq2_v2.py check
```

The graph is:

```text
raw -> canonical -> readiness -> offline campaign -> Luna RQ2 report
```

The final report requires 61 admitted schema-2 journals.
