# Arena-Skill Integration

How the MIT-licensed [arena-skill](https://github.com/Jakeschincariol/arena-skill)
was decoded and used to drive four simulator bots, what broke, and what the
ten-session tournaments measured. These are local, approximate-engine sessions,
not hosted-match results.

## 1. What the repository actually contains

`arena-skill` is a *meta-tournament harness for AI sub-agents*, not a set of
game bots:

- `skills/arena/SKILL.md`, `strategies.json`, `rubric.md`: role prompts,
  15 reasoning modes, 12 workflows, 12 strategies, and a five-criterion rubric
  (correctness 30, completeness 25, specificity 15, robustness 20,
  clarity 10).
- `bracket.py` (+ `tests/test_bracket.py`): a stdlib single-elimination bracket
  that scores submissions; all 27 tests pass in the clone.
- No network calls, no execution beyond the test subprocess.

The four "AI agents" in the repository are role definitions:
**competitor**, **attacker**, **defender**, and **judge**. The requested task
was to use only those four agents, so each role authored a deterministic
policy spec, and each spec was implemented as one simulator bot in
`simulator/arena_bots.py`. No arena-skill code is copied; the MIT notice is
reproduced in `third_party/arena-skill/` because the role concepts and the
policy-spec format are adapted from it.

## 2. Role mapping

| Role | Implemented identity | Core loop |
| --- | --- | --- |
| `arena_competitor` | Relay-income maximiser | DEFEND only when `E ≥ hp−2`, kill-only attacks, capture adjacent foreign relays before tick 128, harvest at flux ≤ 9, BFS objective movement, retreat before each contraction. |
| `arena_attacker` | Denial ladder | Pair-kill → execute → DEFEND → escape → safe-zone retreat → flip/contest relays → harvest → advance; never chips, never trades HP for a contest. |
| `arena_defender` | Hold, deny, withdraw | Survival-reserved Flux, lethal/attrition DEFEND windows, one relay held by the Bulwark, guaranteed kills only, zone retreat at 116/126/136/146/156. |
| `arena_judge` | Rubric pair selection | Scores every legal action on the rubric, filters `Dnet ≥ hp`, enumerates ordered pairs, adds an 8-point focus-fire bonus, prioritises the higher-scoring unit, with a low-HP escape override. |

Every bot consumes the simulator's SDK-shaped observation dict and returns
`(actions, priority)` wire lists; they are deterministic and use no SCAN.

## 3. Session harness

`scripts/run_arena_sessions.py`:

- 10 sessions, seeds 201–210, maps rotated `open, pillars, cross, xmap, random`.
- Roles rotate across the four team slots (`(slot − session) mod 4`) so spawn
  and turn-order bias averages out.
- Each match runs the local approximate engine (`simulator/engine.py`) to
  tick 160; the harness records placement, relay points, HP, deaths,
  elimination tick, invalid actions, and any exception or >2-action issue.

Command:

```bash
python3 scripts/run_arena_sessions.py --sessions 10 --seed-base 201 \
  --json /tmp/arena_sessions_final.json
```

## 4. Issues found and fixed

1. **Judge emitted invalid DEFEND actions (7 in the first run).** The
   single-surviving-unit fast path never recorded `last_defend`, so the judge
   defended on consecutive ticks and the engine rejected the action. Fixed by
   updating the cooldown in that path; invalid actions dropped to 0.
2. **Judge was eliminated in all ten first-run sessions.** It stood on a relay
   and pre-emptively DEFENDed while the safe zone closed instead of escaping.
   Added a low-HP (`hp ≤ 4`, `Dnet ≥ 2`) escape override and a safe-zone
   objective that pre-empts relay goals during a contraction.
3. **Judge and competitor could target immune enemies.** Immunity filters were
   added to target enumeration.
4. **Attacker never captured neutral relays**, donating uncontested income.
   Added neutral-relay capture when no enemy is within 2.
5. **Attacker ignored the shrinking safe zone.** A damage trace of seed 201
   showed both attacker units taking 2 safe-zone damage per tick from tick 126
   onward while chasing outer relays, then dying at ticks 129/140/150/157; the
   attacker class had no zone logic at all. Added a spec-faithful retreat:
   during a contraction warning (or once outside the ring) the unit must pick a
   legal MOVE that strictly reduces centre distance, then one that does not
   increase it, otherwise it falls back to relay play. This fixed the
   consecutive-DEFEND death at tick 157 (outside radius 2 with DEFEND on
   cooldown) and raised attacker survival.
6. **Defender attack condition had ambiguous operator precedence**
   (`A and B or C`); rewritten as an explicit guaranteed-kill gate.

Trace artefact (seed 201, attacker slot, before the zone fix):

```
t=126 S hp 6->4  hazard 2  chasing relay
t=127 S hp 4->2  hazard 2  move blocked
t=129 S hp 2->0  hazard 2  eliminated
t=157 S hp 1->0  hazard 2  DEFEND on cooldown
```

## 5. Ten-session results (v3, final)

Rotating slots, seeds 201–210, local approximate engine, 160 ticks:
**zero issues, zero invalid actions** across all 40 team-sessions.

| Role | Mean place | Firsts | Top-2 | Relay pts | Mean HP | Deaths | Eliminated |
| --- | --- | --- | --- | --- | --- | --- | --- |
| arena_attacker | 2.90 | 10% | 30% | 48.6 | 3.50 | 1.80 | 40% |
| arena_competitor | 2.20 | 50% | 60% | 83.9 | 5.10 | 1.20 | 20% |
| arena_defender | 2.80 | 0% | 40% | 39.5 | 6.80 | 1.40 | 10% |
| arena_judge | 2.10 | 40% | 70% | 93.4 | 4.60 | 2.20 | 30% |

For comparison, the pre-fix ten-session run had the judge eliminated in 10/10
sessions with 7 invalid actions, and the attacker at 3.70 mean placement, 90%
eliminated, 1.0 mean HP. The zone fix alone moved the attacker to 2.90, 40%
eliminated, 3.5 HP.

Interpretation:

- The judge and competitor are the strongest economies (93.4 and 83.9 relay
  points); the judge wins most often when it survives the late ring.
- The attacker is the weakest role by design: it is a *denial* bot that
  abandons chip damage and camps contested relays, so it accepts 2v1 pins and
  bleeds HP. It still wins seed 210 and reaches top-2 in 3/10.
- Late revivals spawn at the team corner. Once the ring is 4 or 2 tiles wide,
  a revived Bulwark cannot walk in before hazard damage kills it; this is a
  fixed consequence of the rules, not a decision bug.

## 6. Limitations

- The engine is an approximation of the match judge; movement conflicts and
  simultaneous occupancy are modelled conservatively.
- Ten sessions is a small sample, and four-player matches make per-role
  placements noisy; the rotation removes spawn bias but not interaction
  variance.
- The judge's one-tick-deep pair search and the attacker's no-chip rule are
  deliberate trade-offs documented in the role specs.
- Nothing here was submitted to the hosted match environment.

## 7. Attribution

Role definitions and policy-spec concepts are adapted from
`github.com/Jakeschincariol/arena-skill` (MIT, Copyright © 2026 Jake
Schincariol). The full license is reproduced in
`third_party/arena-skill/LICENSE`. The four policy specs themselves were
authored for this adaptation and are not redistributed from the upstream repo.
