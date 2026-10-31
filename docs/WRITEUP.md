# Project Writeup — Relay Arena Agent

Maintained narrative for the submission agent. Update the changelog at the
bottom whenever the strategy or measured results change.

## 1. Problem

Four teams share a 20 × 20 grid. Each team controls a Scout (Strider: 6 HP,
moves up to two cells, 1 damage) and a Fighter (Bulwark: 10 HP, moves one cell,
2 damage). Teams spend a shared Flux pool on attacks, defences, captures and
scans; relays generate placement points while owned and uncontested; wells
restore Flux; Warden hazards punish predictable positioning; a safe zone
shrinks to a 3 × 3 diamond late in the match. Ranking prioritises not being
eliminated, then relay points, HP, and deaths.

The agent must emit at most two actions per tick through the NDJSON
SDK within 1,000 ms, with no network access and no third-party dependencies.

## 2. Design goals

1. **Survive first.** Placement points dominate raw aggression, and an
   eliminated team cannot score. Avoid lethal exchanges and late-game hazards.
2. **Own relays, then leave.** Ownership persists and scores passively, so the
   optimal behaviour is a land-grab followed by defence of the most reachable
   holdings, not permanent camping.
3. **Keep Flux liquid.** Income arrives every three completed ticks; captures
   and attacks compete for the same pool. Do not spend the last Flux on a
   capture that cannot be protected.
4. **Be predictable to ourselves, not to opponents.** Deterministic decisions,
   strict action validation, and graceful failure containment.

## 3. Architecture

Single file: `agent/agent.py`.

```
Observation -> World -> memory update -> attractions -> potential maps
            -> per-unit candidates -> joint pair selection -> ActionBundle
```

- **World** parses the observation into slot-based lookups: own units, visible
  hostiles, relays, wells, static blockers, active/imminent hazards, safe-zone
  radius and centres.
- **Memory** tracks the last tick (reset detection), DEFEND cooldown, recently
  seen enemies, living ids, and relay ownership.
- **Attractions** encode goals: capture relays, defend contested owned relays,
  harvest wells, stay inside the effective safe zone, engage adjacent enemies,
  and keep the Bulwark near the Strider.
- **Potential maps** run reverse Dijkstra over the exact legal MOVE graph
  (including two-step intermediate cells, walls, relays, SEALs and occupancy).
- **Candidates** are WAIT, DEFEND, HARVEST, CAPTURE, ATTACK and every legal
  MOVE, each scored with distance-discounted attraction value.
- **Joint selection** evaluates all affordable pairs, rejects shared
  destinations, swaps and duplicate captures, rewards focus fire and combined
  kills, and orders the pair so Flux is reserved for the higher-value action.

## 4. Strategy

### Economy

- Capture unowned relays early; leave after ownership is established.
- Harvest charged wells when Flux is below cap and the visit is safe.
- Avoid a second harvest that would overflow the cap.
- Do not capture with the last Flux when threatened; combat reserves come
  first.

### Relay control

- Route capture value through a discount when a rival already stands next to
  the relay, and a smaller discount for remote contests. This keeps navigation
  consistent with the capture candidate's hard skip and prevents thrashing
  toward guarded relays.
- Defend an owned relay only when it is close and the contest is winnable.
- Late game, abandon relays outside the safe zone once survival dominates.

### Combat

- Attack only adjacent, non-immune enemies; prefer kills and focus fire.
- DEFEND is a hard override when adjacent damage is lethal but absorbable
  (`incoming + hazard >= hp` and `incoming + hazard - 2 < hp`). Attacks resolve
  before movement, so moving cannot dodge a legal attack; defence is the only
  survival play in that state.
- A next-tick **pressure** model counts damage that can reach a cell by the
  next tick, including the allied focus bonus. Wounded units pay a pressure
  penalty for standing still, attacking, harvesting or capturing, and gain a
  disengage bonus for moving to a lower-pressure cell.
- Hunters are low-value targets; they are avoided rather than farmed.

### Hazards and Phase Four

- Active SEALs are treated as walls; next-tick SEALs are heavily penalised.
- PULSE tiles are avoided; imminent PULSE and HUNT zones are danger cells.
- The effective safe radius applies the next contraction as soon as its warning
  appears, so the agent pre-positions instead of sprinting at the deadline.
- Outside the effective zone, re-entry gets a large bonus and non-survival
  objectives are suppressed.

## 5. Reliability

- `decide()` never raises in production; any internal error returns an empty
  bundle, which is a legal WAIT. `ARENA_STRICT=1` re-raises for testing.
- Action validation mirrors the SDK: costs, cooldowns, immunity, adjacency,
  and at most two actions with a priority list.
- No randomness, no I/O, no third-party imports. Deterministic across
  instances and repeated calls.
- Observed maxima in reference practice runs: 18.8 ms per decision for both
  units (limit 1,000 ms), 246-byte replies (limit 16 KiB), 179.5 ms startup
  (limit 5 s).

## 6. Verification and measured results

- Kit integrity: all SHA-256 entries verified.
- Reference smoke test: PASS.
- Reference practice fixtures: 59/59 PASS in both normal and `--strict-actions`
  modes, with 0 predicted WAIT substitutions.
- Local suite: 34/34 tests pass, including strict 160-tick matches, determinism
  and timing.
- 32-match paired benchmark (local approximate engine): mean placement 1.25 vs
  starter 1.69; first-place 78.1% vs 56.2%; relay points 139.5 vs 101.8;
  eliminated 12.5% vs 40.6%; zero invalid actions for both.

Full tables, methodology and caveats: `docs/BENCHMARKS.md`. Test details:
`docs/TEST_REPORT.md`.

## 7. Limitations

- The reference SDK package has no full-match judge. The local simulator is an
  approximation; benchmark numbers are comparative, not predictive.
- Warden targeting, map generation, spawn geometry and some hidden constants
  differ from the reference engine.
- The agent assumes the reference observation schema and exact action keys; it
  does not attempt to model opponents' hidden Flux or intentions.
- No hosted-match submission has been made.

## 8. Repository layout

```
agent/agent.py          single submission file
agent/agent_sdk.py      local copy of the reference SDK
simulator/engine.py     approximate rules engine (not the judge)
simulator/bots.py       opponent policies and isolated runners
simulator/tournament.py paired multi-match benchmark harness
tests/                  engine rule tests and agent contract tests
docs/                   mechanics, architecture, reports, this writeup
scripts/                reference checks, benchmark, submission build
submission/             generated final_agent.py (not uploaded)
```

## 9. Changelog

- 2026-10-31 — Initial complete implementation: economy-first relay strategy,
  potential-field navigation, joint action selection, DEFEND survival rule,
  Phase Four retreat, strict-mode failure containment.
- 2026-10-31 — Guarded-relay routing discount; consistent capture/vs-route
  behaviour. Eight-game mean placement 1.25.
- 2026-10-31 — Next-tick pressure model and disengage/stationary penalties;
  eight-game HP 6.0, relay points 168, top-2 100%.
- 2026-10-31 — Fixed silent-WAIT regression (undefined variable) and enforced
  strict propagation in the simulator bot. Final 32-match paired benchmark:
  1.25 mean placement, 78.1% first, 139.5 relay points, 12.5% eliminated.
- 2026-10-31 — Final artifact build: 28,005 bytes, SHA-256
  `37e2e7bface71b10ad7473a4498a8e3eb2d15b099fd5dd42e0e01ac6507c39d6`,
  smoke + strict practice validated, not uploaded.
- 2026-10-31 — Arena-skill adaptation: four deterministic role bots
  (`simulator/arena_bots.py`), rotating-slot session harness, MIT notice, and
  contract tests. Judge/attacker fixes verified over 10 sessions with zero
  invalid actions; see `docs/ARENA_INTEGRATION.md`.
