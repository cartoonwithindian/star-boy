# SDK and Protocol Analysis

Source: reference SDK package v0.4.5 (`participant_kit.zip`, SHA-256
`a01ea36478067e5042caf1f845d3be8f5a4b34ae016cc5cef83ee31574c3f85a`).
All kit checksums verified with `SHA256SUMS.txt`, `sdk/SHA256SUMS.txt` and the
C++ portability manifest.

## Kit inventory

| Path | Purpose |
| --- | --- |
| `RULEBOOK.pdf` | Full participant rules, dates, tournament format |
| `RULES.md` | Public gameplay rules quick reference |
| `EXECUTION_LIMITS.md` | Source, runtime and protocol limits |
| `sdk/python3.12/agent_sdk.py` | Python 3.12 NDJSON SDK |
| `sdk/python3.12/starter_agent.py` | Public baseline policy |
| `sdk/cpp20/` | C++20 SDK, starter, JSON and portability headers |
| `tools/smoke-test.py` | Six synthetic frames, READY/correlation/shape checks |
| `tools/practice-scenarios.py` | 59 opening-snapshot fixtures, strict modes |
| `practice/scenarios.json` | Editable fixture observations |
| `viewer/index.html` | Offline replay viewer |
| `MANIFEST.json`, `SHA256SUMS.txt` | Inventory and integrity |

The kit ships **no full-match simulator and no judge**. The practice tools
validate protocol behaviour and opening-snapshot action legality only.

## Execution limits

| Limit | Value |
| --- | --- |
| Per scored tick, both units combined | 1,000 ms |
| Startup / READY deadline | 5,000 ms |
| Memory / address space | 1 GiB |
| CPU | 1 CPU equivalent |
| Processes / threads | 32 |
| Private writable temporary storage | 128 MiB |
| Submitted source | one UTF-8 file, at most 1 MiB |
| Reply | one NDJSON message, at most 16 KiB |

The process persists for the whole match and is restarted per match/attempt.
Python startup time counts toward the initial deadline.

## Wire protocol

1. The host launches the program with a `*_PROTOCOL_FD` environment variable set to a dedicated
   write descriptor. The SDK duplicates stdout/stderr into the diagnostic
   stream and keeps the protocol descriptor private.
2. The host writes one JSON request line per tick to stdin.
3. `tick == 0` is the warm-up frame (`{"warmup": true}`). The program must
   answer `{"match_id", "tick": 0, "status": "READY", "actions": [],
   "priority": []}`.
4. For every scored tick the program returns
   `{"match_id", "tick", "actions": [...], "priority": [...]}` with matching
   identity and tick. Omitting `actions`/`priority` is decoded as empty lists.
5. `match_id` and `tick` must match the request exactly.

`agent_sdk.run(decide)` implements steps 2-4. `decide(Observation)` returns an
`ActionBundle`. Exceptions inside `decide` are printed to the diagnostic stream
by the SDK; the host applies its failure policy (timeouts/crashes void the
attempt).

### Observation schema (exact keys)

`Observation.from_wire` enforces exact key sets and integer ranges.

| Object | Keys |
| --- | --- |
| observation | `match_id, tick, team, visible_enemies, terrain, wells, relays, warden_telegraphs, phase_four` |
| team | `id, flux, units` |
| unit | `id, team_id, kind, x, y, hp, immune` |
| terrain | `x, y, kind, object_id` |
| well | `id, position{x,y}, charged, available_from_tick` |
| relay | `id, x, y, owner_team_id, contested, corrupted, uncontested_owner_ticks` |
| telegraph | `kind, starts_at_tick, ends_after_tick, center{x,y}\|null, relay_id\|null, tiles[]` |
| phase_four | `safe_zone_center_tiles[4], safe_radius, hazard_damage, next_contraction` |
| contraction | `warning_at_tick, starts_at_tick, safe_radius` |

Enums: `UnitKind` (`STRIDER`, `BULWARK`, `HUNTER`), `TerrainKind` (`WALL`,
`FLUX_WELL`, `CONTROL_RELAY`), `TelegraphKind` (`PULSE`, `SEAL`, `CORRUPT`,
`HUNT`), `Direction` (`N`, `E`, `S`, `W`), `ActionKind` (`MOVE`, `ATTACK`,
`DEFEND`, `HARVEST`, `CAPTURE`, `SCAN`, `WAIT`).

`visible_enemies` may include Hunters with `team_id == null`. Terrain includes
walls, relay tiles and well tiles (wells are walkable). `phase_four` is present
from the first safe-zone warning tick (111); before that it is `null`.

### Action wire format

| Action | Extra fields | Flux |
| --- | --- | --- |
| MOVE | `direction` (`N/E/S/W`), `steps` (uint8, 1-2) | 0 |
| ATTACK | `target_unit_id` (uint16) | 2 |
| DEFEND | - | 1 |
| HARVEST | - | 0 |
| CAPTURE | `relay_id` (uint8) | 1 |
| SCAN | - | 1 |
| WAIT | - | 0 |

At most two actions, at most one per living unit. `priority` is an ordered
list of unit ids used to reserve shared Flux. Default order is Scout first
unless the program supplies otherwise.

## Failure semantics

- Well-formed but illegal/unaffordable individual actions become WAIT
  (counted by the local practice tool as substitutions).
- Blocked MOVE requests are legal requests whose movement fails during
  resolution; this is not a protocol failure.
- Malformed JSON/UTF-8/enums/identity, crashes and timeouts void the attempt.
- A participant-caused fault permits up to five whole-match attempts.

## SDK helpers

- `ActionBundle(actions, priority)` and `UnitAction.move/attack/defend/
  harvest/capture/scan/wait` build replies.
- `agent_rng()` returns a match-scoped SplitMix64 stream seeded from public
  `match_id`/`team_id`, for reproducible tie-breaking.
- `run(decide)` owns all protocol I/O; do not write to the protocol FD.

## Local tools

```sh
# Integrity of the extracted kit
cd participant_kit && sha256sum -c SHA256SUMS.txt

# Protocol/smoke against our agent
python3 participant_kit/tools/smoke-test.py \
  python3 agent/agent.py

# 59 public fixtures + optional strict substitution failure
python3 participant_kit/tools/practice-scenarios.py \
  python3 agent/agent.py
python3 participant_kit/tools/practice-scenarios.py \
  --strict-actions python3 agent/agent.py
```

`--starter` enforces reference starter regressions and is not applicable to a
custom strategy; our agent must pass default (legal strategy) and
`--strict-actions` (no predicted WAIT substitution).
