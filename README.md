# Relay Arena Agent

An autonomous two-unit strategy program for a four-team, 20 × 20 grid game.
One process controls a Scout (Strider) and a Fighter (Bulwark): it captures and
holds relays, harvests Flux, avoids hazards, manages a shrinking safe zone, and
fights only when the trade is worth it. The agent is a single Python 3.12 file
builds on the reference `agent_sdk` NDJSON protocol.

> Local development repository. The bundled simulator is approximate; it is
> not a match judge and its numbers are for relative comparison only.

## Results

- Reference checks: smoke PASS; practice fixtures 59/59 PASS in normal and
  `--strict-actions` modes; 0 predicted WAIT substitutions; max decision
  18.8 ms (limit 1,000 ms).
- Local suite: 37/37 tests pass, including strict 160-tick matches and
  arena-role contract tests.
- 32-match paired benchmark (approximate engine): mean placement **1.25** vs
  starter 1.69; first-place **78.1%** vs 56.2%; relay points **139.5** vs
  101.8; eliminated **12.5%** vs 40.6%; zero invalid actions.
- Arena roles (MIT arena-skill adaptation): 10 rotating-slot sessions on
  seeds 201–210 produced zero invalid actions and zero exceptions. Best
  economies: judge 2.10 mean placement / 93.4 relay points, competitor 2.20 /
  83.9; attacker 2.90 after a safe-zone fix that cut eliminations from 90% to
  40%.

Details: [writeup](docs/WRITEUP.md) · [tests](docs/TEST_REPORT.md) ·
[benchmarks](docs/BENCHMARKS.md) · [architecture](docs/ARCHITECTURE.md) ·
[arena integration](docs/ARENA_INTEGRATION.md).

## Layout

```
agent/agent.py          tournament agent (single submission file)
agent/agent_sdk.py      copy of the reference SDK for local runs
simulator/engine.py     approximate rules engine (NOT a match judge)
simulator/bots.py       opponent policies + isolated starter/agent runners
simulator/arena_bots.py four arena-skill role bots (competitor/attacker/defender/judge)
simulator/tournament.py multi-match benchmark harness
tests/                  engine rule tests + agent/arena contract tests
docs/                   SDK analysis, mechanics, architecture, reports
scripts/                reference checks, benchmarks, arena sessions, submission build
third_party/arena-skill MIT notice for the adapted arena-skill role concepts
submission/             generated final_agent.py
participant_kit/        unmodified reference SDK package (local only, not committed)
```

The reference SDK package is not redistributed. Place it at `participant_kit/`
locally to run the smoke and fixture checks. The simulator, tests, and
benchmark harness can also be used without the kit because `agent/agent_sdk.py`
is a local SDK copy.

## Quick start

```sh
# Protocol and fixture checks against the agent
./scripts/run_checks.sh

# Local unit and contract tests
python3 -m unittest discover -s tests -v

# 32 matches vs starter/relay/aggressive/passive, rotating team slots
GAMES=32 ./scripts/benchmark.sh

# 10 arena-role sessions with rotating slots (writes a JSON report)
python3 scripts/run_arena_sessions.py --sessions 10 --seed-base 201

# Build and validate submission/final_agent.py (no upload)
python3 scripts/build_submission.py
```

## Strategy summary

- Early: claim unguarded relays, harvest to keep capturable Flux, take
  ownership and leave (ownership persists and scores passively).
- Mid: defend contested owned relays within reach, avoid guarded captures,
  heal the economy with wells, avoid pointless chip trades.
- Late: contract into the diamond before each warning deadline; outside-zone
  relays are abandoned once staying alive dominates.
- Safety: DEFEND is selected when adjacent damage is lethal but absorbable,
  because attacks resolve before movement. A next-tick pressure model makes
  wounded units trade space for safety instead of standing in a closing
  Bulwark's reach.
- Combat: focus fire is rewarded; Hunters are low-value targets.
- Protocol: deterministic, ≤2 actions, Flux reserved in score order, all I/O
  through the SDK. Internal errors fall back to WAIT; `ARENA_STRICT=1`
  makes them fatal for local testing.

## Honest limitations

- The reference package contains no full-match simulator; `simulator/` is an
  approximation for relative comparisons and regressions.
- Warden targeting, spawn geometry and map generation differ from the engine
  used in hosted matches.
- Benchmark results are not predictions of hosted-match scores.
