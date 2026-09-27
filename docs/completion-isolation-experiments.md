# Completion isolation experiments

These experiments replay frozen historical candidates and test the new isolation path. They are not fresh full-agent benchmark trials.

The canonical 59/89 result is unchanged.

## Summary

- Command trajectories replayed: 3 / 3
- Mechanical isolation passed: 2 / 3
- Receipt-first semantic review accepted: 0 / 3
- Receipt-first semantic review unavailable: 2 / 3
- Official verifier reward 1.0: 3 / 3
- Internally verified: 0 / 3

## Results

| Task | Replay commands | Mechanical isolation | Semantic review | Internal outcome | External reward |
|---|---:|---|---|---|---:|
| `crack-7z-hash` | 43 | passed | unavailable | model_failure / completion_review_service | 1.0 |
| `fix-ocaml-gc` | 47 | failed | missing | budget_exhausted / completion_repair_budget | 1.0 |
| `write-compressor` | 37 | passed | unavailable | model_failure / completion_review_service | 1.0 |

## Interpretation

A mechanical pass means every frozen check ran in its own mount-free,
network-disabled child; each check passed; the live source diff stayed
unchanged while paused; and all owned Docker resources were removed.

A reviewer result is independent. Missing model credentials produce
`unavailable`, which cannot become verified completion even when the
mechanical checks and the official verifier pass.

Internally verified: 0 / 3. Mechanical isolation passed 2 / 3, and the official verifier awarded reward 1.0 to 3 / 3.

A frozen check failure stops the attempt before semantic review. These
results validate the isolation lifecycle and expose an internal/external
disagreement; they do not establish benchmark score improvement.

## Source bindings

- `evaluation/completion-calibration.json`: `6b725b42a0c9cb22f72f0a27519ca8e8e5b9c7b15cdab420780e5d78503d15df`
- `evaluation/completion-disagreements.json`: `7a9ba0da1cde18f3103bd6f49573bed20fe5e367374a568e6c1db2c462e7c54a`
- `src/evidence_harness/docker_completion_isolation.py`: `1b3712f48685728e53d1334fb4860762219480c45830993fc6497a7794469040`
- `src/evidence_harness/isolation_experiment_agent.py`: `0fff8d46dfca221e8f46cbb59b7faac36c724cba11072e66139834fbc80595e7`
- `pyproject.toml, uv.lock, and src/evidence_harness/**/*.py`: `dca26829d7124daae2795015c6dabd8e861759d4a2535df0e4cc8342d05149ac`
