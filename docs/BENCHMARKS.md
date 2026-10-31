# Benchmark Report

Date: 2026-10-31

## Harness

`simulator/tournament.py` runs the focus program on a rotating team slot
(`seed % 4`) against three opponents sampled per seed from
`starter, relay, aggressive, passive`. Maps cycle through
`open, pillars, cross, xmap, random`. Both focus programs use the same 32 seeds
(101–131) and therefore the same opponent lineups and slot rotations, giving a
paired comparison.

The simulator is an approximate rules engine, **not the match judge**.
Numbers below measure relative performance in this engine only.

Final runs:

```sh
ARENA_STRICT=1 python3 -m simulator.tournament \
  --focus agent --games 32 --seed-base 101 --jobs 2 \
  --json /tmp/bench_agent_final.json
ARENA_STRICT=1 python3 -m simulator.tournament \
  --focus starter --games 32 --seed-base 101 --jobs 2 \
  --json /tmp/bench_starter_final.json
```

`ARENA_STRICT=1` makes internal agent errors fatal, so a silent WAIT
fallback cannot inflate results. Both runs reported `invalid actions 0.00`.

## Headline results (32 matches each)

| Metric | Agent | Starter | Delta |
|---|---:|---:|---:|
| Mean placement (lower better) | **1.25** | 1.69 | −0.44 |
| First-place rate | **78.1%** | 56.2% | +21.9 pts |
| Top-2 rate | **96.9%** | 81.2% | +15.7 pts |
| Mean placement points (max 4) | **3.75** | 3.31 | +0.44 |
| Mean relay points | **139.5** | 101.8 | +37.7 |
| Mean surviving HP | **6.09** | 4.03 | +2.06 |
| Mean deaths | **1.22** | 2.09 | −0.87 |
| Eliminated rate | **12.5%** | 40.6% | −28.1 pts |
| Mean end tick | 147.8 | 135.8 | +12.0 |
| Invalid actions | 0 | 0 | 0 |

Placement distribution:

| Place | Agent | Starter |
|---|---:|---:|
| 1st | 25 | 18 |
| 2nd | 6 | 8 |
| 3rd | 1 | 4 |
| 4th | 0 | 2 |

## Per-map breakdown

Agent:

| Map | n | Mean place | 1st rate | Relay pts | Mean HP |
|---|---:|---:|---:|---:|---:|
| open | 7 | 1.00 | 100% | 113.0 | 9.29 |
| pillars | 7 | 1.29 | 86% | 172.9 | 6.29 |
| cross | 6 | 1.33 | 67% | 132.5 | 5.50 |
| xmap | 6 | 1.33 | 67% | 144.8 | 3.67 |
| random | 6 | 1.33 | 67% | 133.2 | 5.17 |

Starter:

| Map | n | Mean place | 1st rate | Relay pts | Mean HP |
|---|---:|---:|---:|---:|---:|
| open | 7 | 1.43 | 71% | 88.0 | 3.86 |
| pillars | 7 | 1.14 | 86% | 137.4 | 7.86 |
| cross | 6 | 1.67 | 50% | 91.8 | 2.00 |
| xmap | 6 | 1.83 | 50% | 91.0 | 5.00 |
| random | 6 | 2.50 | 17% | 97.3 | 0.83 |

The largest gains are on `random` (unseen obstacle layouts) and `cross`, and in
survival (HP, deaths, elimination rate). `pillars` is close because both
programs convert relay income well there; the agent still leads on relay points.

## Iteration log

Measured on earlier 8-game sweeps during tuning; small samples are noisy, so
they are recorded as directional evidence, not final claims.

| Change | Mean place | 1st rate | Notes |
|---|---:|---:|---|
| Early baseline | 1.50 | — | Starter comparison: 1.75 |
| Guarded-relay routing discount | 1.25 | 75% | Capture routing now agrees with the guarded-capture skip |
| Next-tick pressure model | 1.25 | 75% | Top-2 100%, relay 168, HP 6.0, deaths 1.38 in 8 games |

A regression during development (an undefined variable in `_attack_score`
caused silent WAIT bundles) was caught by the strict-mode benchmark and fixed;
strict propagation was also added to the simulator's SDK bot wrapper so this
class of failure cannot hide again.

## Interpretation

- The agent's advantage is primarily economic and survival-based: more relay
  points, fewer deaths, and far fewer eliminations, not merely more fighting.
- Higher end tick means the agent survives deeper into Phase Four and keeps
  scoring, while the starter is eliminated earlier.
- These results do not predict hosted-match placement. The hosted environment
  uses a different judge, map generation, and Warden targeting.

## Raw data

- `/tmp/bench_agent_final.json`
- `/tmp/bench_starter_final.json`

Each file contains all 32 per-match records plus the aggregate summary.
