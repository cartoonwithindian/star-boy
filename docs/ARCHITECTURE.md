# Agent Architecture

The submission is a single Python 3.12 file (`agent/agent.py`) that imports the
reference `agent_sdk`. It is deterministic, has no third-party dependencies, and
fits comfortably within the 1 MiB source and 1,000 ms per-tick limits.

## Decision pipeline

```
Observation (wire) -> SDK Observation -> World -> memory update
  -> attraction goals -> reverse-Dijkstra potential maps
  -> per-unit candidates -> joint pair selection -> ActionBundle
```

### 1. World

`World` parses one observation into slot-based lookup structures and derives:

- Own living units, visible hostile units (Hunters included, `team_id=None`).
- Static blockers: walls, relay tiles; relay and well maps.
- Active SEALs (block movement now), active PULSE tiles, imminent danger
  (PULSE/HUNT next tick), and SEALs that start next tick.
- Safe-zone phase: current radius, effective radius (future contraction applied
  once its warning arrives), centres, and `inside_effective`.
- Helper threat models:
  - `incoming_damage(unit)`: adjacent attackers plus allied focus bonus.
  - `hazard_at(unit, p)`: safe-zone damage at the actual current radius plus
    active PULSE.
  - `expected_threat(p)`: damage that can be adjacent next tick.
  - `pressure(p)`: damage reachable by next tick if the unit stays, used to
    decide when a wounded unit should trade space for safety.
- `free_for(p, unit)`: bounds, static terrain, active SEALs, visible enemies,
  and allied occupancy (excluding self).

### 2. Memory (`_MEM`)

Per match/team: `last_tick` for reset detection, `last_defend` cooldown,
recently seen enemies (3-tick stale threat), previous living ids, and relay
owners. Memory resets on a new identity or a tick regression.

### 3. Attractions

Each attraction is `(value, goal_cells, kind)`:

- **Capture**: relays not owned by us. Deferred or discounted if a visible rival
  is adjacent to the relay (routing must agree with the CAPTURE candidate's
  hard skip), if remote-contested, if corrupted, late-game outside the zone, or
  if the partner is closer.
- **Defend**: our contested relay within 5 tiles.
- **Harvest**: charged wells when Flux is below the cap; scaled by need and
  partner distance.
- **Safe zone**: inside-zone tiles with escalating weight as contractions
  approach; near-total priority when currently outside.
- **Engage**: adjacent-goal cells around visible enemies; Hunters discounted.
- **Protect**: Bulwark shadowing the Strider within 4 tiles.

### 4. Potential maps

`_potential` runs a reverse Dijkstra over the exact legal MOVE graph (straight
1-2 steps for Striders, 1 for Bulwarks), respecting static and SEAL blockers,
unit occupancy and intermediate cells of two-step moves. It yields the minimum
number of MOVE actions from any cell to any goal, capping search at board size.

### 5. Candidates and scores

Per unit:

- WAIT (subject to hazard/stay penalties).
- DEFEND when off cooldown and affordable.
- HARVEST on a charged well.
- CAPTURE for adjacent unowned relays, with hard skips for immunity, last-Flux
  fragility, a rival adjacent to the relay, or late-game outside the zone.
- ATTACK for adjacent non-immune hostiles.
- Every legal MOVE.

MOVE score uses `value / (1 + 0.55 * distance)` for each attraction, adds 20% of
the next two best attractions, and applies:

- −190 for imminent danger, −150 for standing PULSE, −55 for next-tick SEAL.
- Outside-zone penalty and a large bonus for re-entering the safe zone.
- +150 for escaping current hazard.
- Adjacency penalties, extra for a wounded unit next to a Bulwark.
- A disengage bonus when `hp <= 4` and next-tick pressure is at least 2.
- Stale-threat memory, relay-adjacency and well pickups.
- Late-game centre pull.

ATTACK/HARVEST/CAPTURE/WAIT also pay a pressure penalty when wounded, so the
agent does not stand still while a fresh heavy unit closes in.

DEFEND gives a hard +520 when adjacent damage is lethal but absorbable
(`incoming + hazard >= hp` and `incoming + hazard - 2 < hp`). Movement cannot
dodge attacks already legal at tick start, so defending is the only survival
play in that state.

### 6. Joint selection

`_pick` evaluates every affordable action pair:

- Reject shared destinations, mutual swaps, and duplicate relay captures.
- Reward allied focus fire on the same target (+20, plus +46 when the combined
  damage kills and neither hit alone would).
- Reward DEFEND paired with ATTACK/CAPTURE (+6).
- Penalise a second harvest that would waste Flux at the cap (−18).
- Penalise spending the last Flux on non-combat actions while threatened
  (−10).

Priority is the chosen units ordered by candidate score, which reserves shared
Flux for the more valuable action first.

## Failure containment and determinism

`decide` never raises in production: any internal error returns an empty
bundle (legal WAIT). Setting `ARENA_STRICT=1` re-raises instead, which the
local simulator/tests use to catch silent WAIT fallbacks. The agent uses no
randomness, so identical observations produce identical bundles.

## Local simulator (comparative tool)

`simulator/engine.py` is an approximate rules engine (explicitly not the
match judge):

- Exact SDK-shaped observations, action validation, priority-based Flux
  reservation.
- Simultaneous attacks with focus bonus and DEFEND pool; simultaneous movement
  with static blockers, SEALs, stationary occupants, competing arrivals and
  swaps; safe-zone then PULSE damage; harvest/capture/scoring; Flux regen;
  revivals, immunity, eliminations; Hunters and warnings.
- Deterministic map generation (`open`, `pillars`, `cross`, `xmap`, `random`)
  and seeded RNG.

`simulator/bots.py` provides passive, random, aggressive, relay, and turtle
policies, plus isolated module instances of the reference starter and the
submission agent. `simulator/tournament.py` rotates the focus program across
team slots against sampled opponents and reports placement, relay points, HP,
deaths, elimination rate, invalid actions and match length.

Limitations: Warden targeting, map generation, spawn geometry and several
hidden details are approximations. Results are used only for relative
comparison and regression detection.

## Tuning workflow

1. Run reference smoke/practice/strict checks.
2. Run local unit and contract tests with `ARENA_STRICT=1`.
3. Benchmark the focus agent over many seeds/maps versus the starter and simple
   bots.
4. Keep a change only if it improves measured placement/survival without
   regressing protocol checks.
5. Re-run the full suite and record results in `docs/TEST_REPORT.md` and
   `docs/BENCHMARKS.md`.
