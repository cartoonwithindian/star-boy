# Test Report

Date: 2026-10-31
Environment: Python 3.12.3, Linux x86-64, 2 available CPUs.
Code under test: `agent/agent.py` (28,005 bytes, UTF-8, single file).
Artifact under test: `submission/final_agent.py` (identical copy).

All commands below were run from the repository root. Results are copied from
the captured logs; nothing in this report is predicted or estimated.

## 1. SDK package integrity

`sha256sum -c SHA256SUMS.txt` for the reference SDK package and for
`sdk/SHA256SUMS.txt` returned `OK` for every entry, including:

- `RULES.md`, `RULEBOOK.pdf`, `EXECUTION_LIMITS.md`
- `practice/scenarios.json`
- `sdk/python3.12/agent_sdk.py`
- `sdk/cpp20/*` and vendored JSON headers

Command: `./scripts/run_checks.sh` (step 1).

## 2. Protocol smoke test

```
PASS: six synthetic frames, READY handshake, correlated ticks and bounded
protocol replies.
No strategy or full gameplay checks; no score prediction or judge certification.
```

This is the reference `tools/smoke-test.py` against `agent/agent.py`.

## 3. Practice fixtures

Run once in normal mode and once with `--strict-actions`:

| Mode | Fixtures | Result | Predicted WAIT substitutions | Varying sequences |
|---|---|---|---|---|
| protocol + gameplay | 59 | PASS 59/59 | 0 | 0 |
| `--strict-actions` | 59 | PASS 59/59 | 0 | 0 |

Reference tool-reported timing (JSON summary):

| Metric | Normal run | Strict run | Limit |
|---|---|---|---|
| `max_tick_ms_both_units` | 16.544 | 18.768 | 1,000 |
| `max_startup_ms` | 189.353 | 179.513 | 5,000 |
| `max_reply_bytes` | 246 | 246 | 16,384 |
| `deterministic_repeats` | 2 | 2 | n/a |

The practice tool explicitly states that it performs **no full-match
simulation or score prediction**; these fixtures check protocol shape and
rule-aware action selection, not competitive strength.

## 4. Local unit and contract tests

Command: `python3 -m unittest discover -s tests -v`

```
Ran 37 tests in 14.655s
OK
```

Coverage:

- `tests/test_engine.py` — movement (2-step, walls, relays, occupancy,
  following departure, swaps, competing arrivals), simultaneous combat,
  DEFEND pool and cooldown, focus fire, Flux reservation/priority/regen,
  harvest/refill, capture competition, relay scoring/contest/neutralisation,
  safe-zone and PULSE damage, revival scheduling/immunity, elimination,
  ranking, determinism.
- `tests/test_agent_contract.py` — full 160-tick strict match without
  exceptions, empty-team bundle, cross-instance determinism, <1,000 ms decision
  time on a dense late-game observation, Hunter/CORRUPT handling, ≤2 actions,
  legal unit ids/kinds, and reply size.
- `tests/test_arena_bots.py` — the four arena-role bots over five maps:
  ≤2 legal actions per tick, no action for dead or immune units, zero engine
  invalid actions, cross-instance determinism over 40 ticks, and a safe-zone
  regression asserting the attacker retreats (strictly reducing centre
  distance) when the ring is closing.

Contract tests run with `ARENA_STRICT=1`, which makes any internal agent
exception fail the test instead of silently degrading to WAIT.

## 4b. Arena-session runs

Command: `python3 scripts/run_arena_sessions.py --sessions 10 --seed-base 201`

The rotating-slot tournament over seeds 201–210 and five maps produced zero
exceptions, zero >2-action issues, and zero invalid actions across 40
team-sessions. A repeat run produced a byte-identical JSON report. Role
results and the attacker safe-zone fix are documented in
`docs/ARENA_INTEGRATION.md`; they are approximate-engine comparative sessions,
not hosted-match results.

## 5. Submission artifact

Command: `python3 scripts/build_submission.py`

```
wrote submission/final_agent.py (28005 bytes, utf-8)
sha256 37e2e7bface71b10ad7473a4498a8e3eb2d15b099fd5dd42e0e01ac6507c39d6
submission artifact validated; not uploaded
```

Validator actions:

- Source is valid UTF-8 and compiles.
- Size is 28,005 bytes, far below the 1 MiB limit.
- Imports the reference `agent_sdk`.
- Passes the reference smoke test.
- Passes the reference practice fixtures with `--strict-actions`.

`submission/final_agent.py` is byte-identical to `agent/agent.py`. No upload to
any hosted match was performed.

## 6. Known limitations

- The reference SDK package contains no full-match judge; pass/fail fixtures and the
  local simulator cannot certify hosted-match placement.
- The local simulator is an approximation (Warden targeting, map generation,
  spawn geometry, and some hidden details differ). Benchmark numbers are for
  relative comparison only; see `docs/BENCHMARKS.md`.
- Timing was measured under light CPU contention; reference limits are much
  larger than the observed maxima.

## Reproduction

```sh
./scripts/run_checks.sh
ARENA_STRICT=1 python3 -m unittest discover -s tests -v
python3 scripts/build_submission.py
```
