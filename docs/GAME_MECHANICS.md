# Game Mechanics Reference

Condensed from `RULEBOOK.pdf` and `RULES.md`. This is the model the agent and
the local simulator follow; the match judge remains authoritative.

## Board and units

- 20 × 20 orthogonal grid; matches last at most 160 scored ticks.
- Four teams; each controls a Strider (Scout) and a Bulwark (Fighter).
- The Warden is an independent hazard controller, not a fifth team.

| Unit | HP | Move | Attack |
| --- | --- | --- | --- |
| Strider | 6 | up to 2 tiles straight | 1 |
| Bulwark | 10 | 1 tile | 2 |
| Hunter | 4 | 1 tile | 1 |

## Actions and Flux

| Action | Flux | Effect |
| --- | --- | --- |
| MOVE | 0 | Move up to the unit limit in one direction |
| ATTACK | 2 | Hit a visible adjacent non-immune hostile unit |
| DEFEND | 1 | Reduce up to 2 total damage this tick; no consecutive success |
| HARVEST | 0 | +3 Flux while standing on a charged well |
| CAPTURE | 1 | Claim a relay from an adjacent tile |
| SCAN | 1 | Team sight becomes 10 next tick |
| WAIT | 0 | No action |

Shared pool: start 6, cap 14, +1 every 3 completed ticks if the team has a
living unit. Costs are reserved for both units before resolution; same-tick
harvest cannot fund reservations. Invalid or unaffordable actions become WAIT.
Default priority is Scout first; the program may override.

## Resolution order

1. Action checks and Flux reservation.
2. DEFEND shields and simultaneous attacks.
3. Simultaneous movement.
4. Safe-zone damage, then PULSE damage.
5. Harvesting.
6. Captures and relay scoring.
7. Flux regeneration, revivals, elimination checks.

Combat happens **before** movement: moving cannot dodge an attack that was
legal at tick start. Attacks are simultaneous and land even if the attacker
dies.

## Movement

- Orthogonal only; walls, relay tiles and active SEAL cells are impassable;
  wells are walkable.
- A Strider's two-tile MOVE cannot turn.
- Stationary occupants and competing arrivals block a destination; following
  a unit that vacates can succeed; two-unit swaps fail.
- Movement is resolved simultaneously, so openings depend on all intents.

## Combat and vision

- Allied focus fire on the same target adds +1 combined damage.
- DEFEND absorbs up to 2 total damage, including hazards, and cannot be used
  successfully on consecutive ticks. It is legal with no visible threat.
- Immunity (revival) blocks ATTACK and CAPTURE, not DEFEND, MOVE or HARVEST.
- Enemy units are visible within Manhattan 5 of any allied unit; walls do not
  obstruct. SCAN raises the radius to 10 for the next tick.
- Player-involved combat deaths can drop 2 Flux, collected by moving onto the
  tile.

## Flux wells

Eight wells. Harvesting grants 3 Flux (subject to cap); the well recharges at
`harvest_tick + 6`. Entering a well does not harvest.

## Relays

Four relays. CAPTURE from an adjacent tile; relays are not walkable.
Ownership persists after leaving. Competing captures by different teams in the
same tick all fail but still spend Flux. Each owned relay earns 1 point per 2
uncontested, uncorrupted owner ticks. An adjacent living enemy pauses scoring;
Hunters do not contest. CORRUPT pauses scoring. Ownership change resets the
counter. A fully eliminated owner's relays become neutral; earned points stay.

## Revival and elimination

One shared revival charge per team for the first eligible death (Scout takes
priority if both die together). Revival is due at `death_tick + 4`, full HP,
retrying up to 3 ticks if the spawn is blocked. The revived unit is immune on
its arrival tick and the next tick. Full elimination requires no living unit,
no queued revival and no remaining charge. A pending revival keeps the team
active for match-end checks.

## Warden and hazards

| Warning | Timing | Effect |
| --- | --- | --- |
| PULSE | next tick | 3 × 3 area deals 2 damage after movement |
| SEAL | next two ticks | marked cells block movement |
| CORRUPT | next two ticks | marked relay cannot accrue scoring |
| HUNT | arrival in 3 ticks | announces Hunter deployment |

Hunters can move/attack but never capture or contest relays. A newly deployed
Hunter cannot move or attack on its arrival tick. Warden target selection is
not public.

## Shrinking safe zone

Diamond around the four centre tiles `(9,9), (10,9), (9,10), (10,10)`. Each
contraction is announced 5 ticks early. After movement, units outside take
2 damage per tick (before PULSE); immunity and DEFEND apply.

| Warning tick | Start tick | Radius |
| --- | --- | --- |
| 111 | 116 | 14 |
| 121 | 126 | 10 |
| 131 | 136 | 7 |
| 141 | 146 | 4 |
| 151 | 156 | 2 |

## Ranking

1. Non-eliminated teams rank above eliminated teams.
2. Among surviving teams: more relay points, then more surviving HP, then fewer
   player-unit deaths.
3. Among eliminated teams: later elimination tick, then more relay points.
4. Exact equality is a tie.

Match placements award 4 / 3 / 2 / 1 tournament points; ties split the sum of
the occupied positions (two tied first receive 3.5 each).

Round 1: 16 reference + 16 pool matches, equally weighted; top 32 advance.
Onsite stages are scored independently (32→16→8→4→1).

## Practical implications used by the agent

- Relay points dominate; passive ownership while roaming is efficient.
- Do not feed HP: surviving HP and deaths are tiebreakers, and elimination
  forfeits all remaining relay income.
- DEFEND is the only answer to a pre-movement lethal adjacency when cooldown
  allows; otherwise avoid entering that situation at all.
- Budget Flux before both actions; harvesting cannot retroactively pay.
- Retreat into the safe zone before each contraction, not after.
- Hunters are slow and low-value; avoid their overlap and do not trade HP for
  a Hunter kill.
