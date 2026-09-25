# Terminal-Bench 2.0 Evaluation

- Dataset: `terminal-bench@2.0`
- Scoring complete: `no`
- All tasks attempted: `yes`
- Executed: `20/20`
- Scored: `19/20`
- Passed / failed / errored: `18 / 1 / 1`
- Pass rate over attempted tasks: `90.0%`
- Pass rate over scored tasks: `94.7%`
- Execution coverage: `100.0%`
- Scored coverage: `95.0%`

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
| prove-plus-comm | easy | software-engineering | openai/modelhub/gpt-5.6-terra | live | passed | 1 | budget_exhausted |
| regex-log | medium | data-processing | openai/modelhub/gpt-5.6-terra | live | passed | 1 | verified |
| nginx-request-logging | medium | system-administration | openai/modelhub/gpt-5.6-terra | live | passed | 1 | budget_exhausted |
| extract-elf | medium | file-operations | openai/modelhub/gpt-5.6-terra | live | passed | 1 | budget_exhausted |
| query-optimize | medium | data-science | openai/modelhub/gpt-5.6-terra | live | passed | 1 | verified |
| vulnerable-secret | medium | security | openai/modelhub/gpt-5.6-terra | live | failed | 0 | model_failure |
| multi-source-data-merger | medium | data-processing | openai/modelhub/gpt-5.6-terra | live | passed | 1 | verified |
| fix-code-vulnerability | hard | security | openai/modelhub/gpt-5.6-terra | live | passed | 1 | verified |
| password-recovery | hard | security | openai/modelhub/gpt-5.6-terra | live | passed | 1 | verified |
| dna-assembly | hard | scientific-computing | openai/modelhub/gpt-5.6-terra | live | passed | 1 | verified |

> `error` tasks count in the attempted pass rate but not the scored pass rate. `not_run` tasks are excluded from both.

> `replay` runs execute previously recorded agent decisions without a new model call.
