# Terminal-Bench 2.0 Evaluation

- Dataset: `terminal-bench@2.0`
- Scoring complete: `no`
- All tasks attempted: `yes`
- Executed: `10/10`
- Scored: `9/10`
- Passed / failed / errored: `9 / 0 / 1`
- Pass rate over attempted tasks: `90.0%`
- Pass rate over scored tasks: `100.0%`
- Execution coverage: `100.0%`
- Scored coverage: `90.0%`

| Task | Difficulty | Category | Model | Mode | Status | Reward | Stop reason |
|---|---|---|---|---|---|---:|---|
| overfull-hbox | easy | debugging | openai/modelhub/gpt-5.6-terra | live | passed | 1 | budget_exhausted |
| fix-git | easy | software-engineering | openai/glm-5.3 | live | passed | 1 | verified |
| cobol-modernization | easy | software-engineering | openai/glm-5.3 | live | passed | 1 | budget_exhausted |
| log-summary-date-ranges | medium | data-processing | openai/glm-5.3 | live | passed | 1 | verified |
| openssl-selfsigned-cert | medium | security | openai/glm-5.3 | live | passed | 1 | verified |
| modernize-scientific-stack | medium | scientific-computing | N/A (journal replay) | replay | passed | 1 |  |
| qemu-startup | medium | system-administration | openai/glm-5.3 | live | error | 0 |  |
| cancel-async-tasks | hard | software-engineering | openai/modelhub/gpt-5.6-terra | live | passed | 1 | verified |
| configure-git-webserver | hard | system-administration | openai/glm-5.3 | live | passed | 1 | budget_exhausted |
| model-extraction-relu-logits | hard | mathematics | openai/modelhub/gpt-5.6-terra | live | passed | 1 | budget_exhausted |

> `error` tasks count in the attempted pass rate but not the scored pass rate. `not_run` tasks are excluded from both.

> `replay` runs execute previously recorded agent decisions without a new model call.
