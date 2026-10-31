"""Relay Arena two-unit strategy agent.

Single autonomous program controlling a Strider (Scout) and Bulwark (Fighter).
It imports the agent_sdk.py supplied with the reference SDK.

Strategy: early relay land-grab, passive relay income, careful flux economy,
threat/hazard avoidance, then a disciplined late-game retreat into the shrinking
safe zone. Every action is emitted through the official SDK.
"""
from __future__ import annotations

import os
from heapq import heappop, heappush

from agent_sdk import (
    ActionBundle,
    Direction,
    TelegraphKind,
    TerrainKind,
    UnitAction,
    UnitKind,
    run,
)

BOARD = 20
INF = 10**9
DIRS = ((0, -1, Direction.N), (1, 0, Direction.E), (0, 1, Direction.S), (-1, 0, Direction.W))
COST = {"MOVE": 0, "ATTACK": 2, "DEFEND": 1, "HARVEST": 0, "CAPTURE": 1, "SCAN": 1, "WAIT": 0}
MAX_FLUX = 14
RELAY_CUTOFF = 140  # after this, an outside-safe-zone relay is rarely worth a detour


def _pt(obj):
    return (obj.x, obj.y)


def _manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _damage(kind):
    return 2 if kind is UnitKind.BULWARK else 1


def _max_steps(kind):
    return 2 if kind is UnitKind.STRIDER else 1


class World:
    """Parsed observation plus fast lookup structures for one decision."""

    __slots__ = (
        "obs", "tick", "team_id", "flux", "units", "unit_by_id", "enemies",
        "enemy_by_id", "relays", "wells", "well_at", "static", "seals",
        "pulse", "danger", "next_seals", "phase", "radius", "current_radius",
        "centers", "enemy_positions", "ally_positions", "origin_positions",
        "seen",
    )

    def __init__(self, obs, seen_enemies):
        self.obs = obs
        self.tick = obs.tick
        self.team_id = obs.team.id
        self.flux = max(0, obs.team.flux)
        self.units = [u for u in obs.team.units if u.hp > 0]
        self.unit_by_id = {u.id: u for u in self.units}
        self.enemies = [e for e in obs.visible_enemies if e.hp > 0 and e.team_id != self.team_id]
        self.enemy_by_id = {e.id: e for e in self.enemies}
        self.relays = {r.id: r for r in obs.relays}
        self.wells = {w.id: w for w in obs.wells}
        self.well_at = {_pt(w.position): w for w in obs.wells}

        blocked = set()
        for t in obs.terrain:
            if t.kind in (TerrainKind.WALL, TerrainKind.CONTROL_RELAY):
                blocked.add((t.x, t.y))
        for r in obs.relays:
            blocked.add((r.x, r.y))
        self.static = blocked

        seals, pulse, danger, next_seals = set(), set(), set(), set()
        for warning in obs.warden_telegraphs:
            tiles = {_pt(tile) for tile in warning.tiles}
            if warning.kind is TelegraphKind.SEAL:
                if warning.starts_at_tick <= self.tick <= warning.ends_after_tick:
                    seals |= tiles
                elif warning.starts_at_tick == self.tick + 1:
                    next_seals |= tiles
            elif warning.kind is TelegraphKind.PULSE:
                if warning.starts_at_tick <= self.tick <= warning.ends_after_tick:
                    pulse |= tiles
                if self.tick <= warning.ends_after_tick and warning.starts_at_tick <= self.tick + 1:
                    danger |= tiles
            elif warning.kind is TelegraphKind.HUNT:
                if self.tick <= warning.ends_after_tick and warning.starts_at_tick <= self.tick + 1:
                    danger |= tiles
        self.seals = seals
        self.pulse = pulse
        self.danger = danger
        self.next_seals = next_seals

        self.phase = obs.phase_four
        if self.phase is None:
            self.radius = 40
            self.current_radius = 40
            self.centers = ()
        else:
            self.current_radius = self.phase.safe_radius
            self.centers = tuple(_pt(c) for c in self.phase.safe_zone_center_tiles)
            radius = self.phase.safe_radius
            step = self.phase.next_contraction
            if step is not None and self.tick >= step.warning_at_tick:
                radius = min(radius, step.safe_radius)
            self.radius = radius

        self.enemy_positions = set()
        for e in self.enemies:
            self.enemy_positions.add((e.x, e.y))
        self.ally_positions = {(u.x, u.y) for u in self.units}
        self.origin_positions = set(self.ally_positions)
        self.seen = seen_enemies

    # -- geometry -------------------------------------------------------
    def in_bounds(self, p):
        return 0 <= p[0] < BOARD and 0 <= p[1] < BOARD

    def center_distance(self, p):
        if not self.centers:
            return 0
        return min(_manhattan(p, c) for c in self.centers)

    def inside_radius(self, p, radius):
        if not self.centers:
            return True
        return self.center_distance(p) <= radius

    def inside_current(self, p):
        return self.inside_radius(p, self.current_radius)

    def inside_effective(self, p):
        return self.inside_radius(p, self.radius)

    def free_for(self, p, unit, include_ally=True):
        if not self.in_bounds(p) or p in self.static or p in self.seals or p in self.enemy_positions:
            return False
        if include_ally and p in self.ally_positions and p != _pt(unit):
            return False
        return True

    # -- threats --------------------------------------------------------
    def adjacent_enemies(self, unit):
        p = _pt(unit)
        return [e for e in self.enemies if not e.immune and _manhattan(p, _pt(e)) == 1]

    def incoming_damage(self, unit):
        if unit.immune:
            return 0
        teams = []
        total = 0
        for e in self.adjacent_enemies(unit):
            total += _damage(e.kind)
            if e.team_id is not None:
                teams.append(e.team_id)
        for t in set(teams):
            if teams.count(t) >= 2:
                total += 1
        return total

    def hazard_at(self, unit, p):
        if unit.immune:
            return 0
        damage = 0
        if self.phase is not None and not self.inside_current(p):
            damage += max(0, self.phase.hazard_damage)
        if p in self.pulse:
            damage += 2
        return damage

    def expected_threat(self, p):
        total = 0
        for e in self.enemies:
            if e.immune:
                continue
            reach = 1 + _max_steps(e.kind)
            if _manhattan(p, _pt(e)) <= reach:
                total += _damage(e.kind)
        return total

    def pressure(self, p):
        """Damage that could reach ``p`` by next tick if the unit stays put."""
        total = 0
        teams = []
        for e in self.enemies:
            if e.immune:
                continue
            if _manhattan(p, _pt(e)) <= 1 + _max_steps(e.kind):
                total += _damage(e.kind)
                if e.team_id is not None:
                    teams.append(e.team_id)
        for team_id in set(teams):
            if teams.count(team_id) >= 2:
                total += 1
        return total

    def enemy_adjacent_to(self, p, include_hunters=False):
        for e in self.enemies:
            if e.team_id is None and not include_hunters:
                continue
            if _manhattan(p, _pt(e)) == 1:
                return True
        return False

    def relay_enemy_adjacent(self, relay):
        p = (relay.x, relay.y)
        for e in self.enemies:
            if e.team_id is not None and _manhattan(p, _pt(e)) == 1:
                return True
        return False

    def friendly_adjacent_to(self, p):
        return any(_manhattan(p, _pt(u)) == 1 for u in self.units)

    def can_defend(self, unit, last_defend):
        return (
            not unit.immune
            and self.flux >= 1
            and last_defend.get(unit.id) != self.tick - 1
        )


# ---------------------------------------------------------------------------
# Movement graph
# ---------------------------------------------------------------------------
def _legal_moves(world, unit):
    """Yield (direction, steps, destination) legal straight-line MOVE actions."""
    x, y = _pt(unit)
    for dx, dy, direction in DIRS:
        for steps in range(1, _max_steps(unit.kind) + 1):
            dest = (x + dx * steps, y + dy * steps)
            if not world.free_for(dest, unit):
                break
            yield direction, steps, dest


def _potential(world, unit, goals):
    """Reverse Dijkstra: minimum MOVE actions from every cell to any goal."""
    goals = {g for g in goals if world.free_for(g, unit)}
    if not goals:
        return {}
    dist = {g: 0 for g in goals}
    heap = [(0, g[0], g[1]) for g in goals]
    heap.sort()
    steps_max = _max_steps(unit.kind)
    while heap:
        cost, x, y = heappop(heap)
        if cost != dist.get((x, y)):
            continue
        for dx, dy, _direction in DIRS:
            for steps in range(1, steps_max + 1):
                px, py = x - dx * steps, y - dy * steps
                p = (px, py)
                if not world.in_bounds(p):
                    break
                if not world.free_for(p, unit):
                    break
                # Cells travelled by p -> (x,y), excluding the destination.
                ok = True
                for k in range(1, steps):
                    if not world.free_for((px + dx * k, py + dy * k), unit):
                        ok = False
                        break
                if not ok:
                    break
                nxt = cost + 1
                if nxt < dist.get(p, INF):
                    dist[p] = nxt
                    heappush(heap, (nxt, px, py))
    return dist


def _adjacent_free(world, unit, x, y):
    goals = []
    for dx, dy, _direction in DIRS:
        p = (x + dx, y + dy)
        if world.in_bounds(p) and world.free_for(p, unit):
            goals.append(p)
    return goals


# ---------------------------------------------------------------------------
# Persistent team memory
# ---------------------------------------------------------------------------
_MEM = {
    "match": None,
    "last_tick": -1,
    "last_defend": {},
    "prev_ids": set(),
    "seen": {},
    "relay_owner": {},
}


def _reset_memory(identity):
    _MEM["match"] = identity
    _MEM["last_tick"] = -1
    _MEM["last_defend"] = {}
    _MEM["prev_ids"] = set()
    _MEM["seen"] = {}
    _MEM["relay_owner"] = {}


def _update_memory(world):
    tick = world.tick
    for e in world.enemies:
        _MEM["seen"][e.id] = (tick, e.x, e.y, e.kind, e.hp)
    live = {u.id for u in world.units}
    for dead in _MEM["prev_ids"] - live:
        _MEM["last_defend"].pop(dead, None)
    _MEM["prev_ids"] = live
    for r in world.relays.values():
        _MEM["relay_owner"][r.id] = r.owner_team_id


# ---------------------------------------------------------------------------
# Utility scoring
# ---------------------------------------------------------------------------
def _relay_capture_value(world, relay, unit, other):
    if relay.owner_team_id == world.team_id:
        return 0
    if world.tick >= RELAY_CUTOFF and not world.inside_effective((relay.x, relay.y)):
        return 0
    value = 62.0
    if relay.owner_team_id is None:
        value += 22.0
    else:
        value += 12.0 + min(24.0, relay.uncontested_owner_ticks * 2.0)
    if relay.corrupted:
        value -= 14.0
    # Never route into a relay that a visible rival is already guarding: the
    # CAPTURE candidate itself is skipped in that case, so the pull would only
    # drag units into a losing standoff.
    if world.relay_enemy_adjacent(relay):
        value *= 0.15
    elif relay.contested and not world.friendly_adjacent_to((relay.x, relay.y)):
        value *= 0.45
    # Split objectives between the two units when they are not close.
    if other is not None:
        my_d = _manhattan(_pt(unit), (relay.x, relay.y))
        their_d = _manhattan(_pt(other), (relay.x, relay.y))
        if their_d + 2 < my_d:
            value *= 0.55
    # Do not sprint to an outside-zone relay while the zone is closing unless close.
    if world.phase is not None and world.tick >= 121 and not world.inside_effective((relay.x, relay.y)):
        value *= 0.55
    return value


def _relay_defend_value(world, relay, unit):
    p = (relay.x, relay.y)
    if relay.owner_team_id != world.team_id:
        return 0
    if not (relay.contested or world.relay_enemy_adjacent(relay)):
        return 0
    if world.tick >= RELAY_CUTOFF and not world.inside_effective(p):
        return 0
    distance = _manhattan(_pt(unit), p)
    if distance > 5:
        return 0
    return max(10.0, 46.0 - 4.0 * distance)


def _well_value(world, well, unit, other):
    if not well.charged:
        return 0
    if world.flux >= 13:
        return 0
    p = _pt(well.position)
    if world.tick >= RELAY_CUTOFF and not world.inside_effective(p):
        return 0
    need = world.flux
    value = 34.0 if need < 8 else (22.0 if need < 11 else 10.0)
    if other is not None:
        my_d = _manhattan(_pt(unit), p)
        their_d = _manhattan(_pt(other), p)
        if their_d + 2 < my_d:
            value *= 0.6
    return value


def _safe_attraction(world, unit):
    """Return (value, goal_cells) for safe-zone survival, or (0, [])."""
    if world.phase is None:
        return 0.0, []
    p = _pt(unit)
    goals = []
    limit = 40 if world.radius >= 40 else world.radius
    for y in range(BOARD):
        for x in range(BOARD):
            q = (x, y)
            if world.center_distance(q) <= limit and world.free_for(q, unit):
                goals.append(q)
    if not goals:
        return 0.0, []
    if not world.inside_effective(p):
        return 900.0, goals
    if world.tick >= 151:
        return 260.0, goals
    if world.tick >= 141:
        return 180.0, goals
    if world.tick >= 131:
        return 120.0, goals
    if world.tick >= 126:
        return 70.0, goals
    if world.tick >= 121:
        return 35.0, goals
    return 0.0, []


def _engage_value(world, unit, enemy):
    if enemy.immune:
        return 0
    damage = _damage(unit.kind)
    kill = enemy.hp <= damage
    value = 10.0 * damage + (34.0 if kill else 0.0)
    value += 4.0 * _damage(enemy.kind)
    if enemy.kind is UnitKind.HUNTER:
        value *= 0.5
    if world.tick >= 141:
        value *= 0.7
    return value


def _build_attractions(world, unit, other):
    attractions = []  # (value, goals, kind)
    for relay in world.relays.values():
        value = _relay_capture_value(world, relay, unit, other)
        if value > 0:
            goals = _adjacent_free(world, unit, relay.x, relay.y)
            if goals:
                attractions.append((value, goals, "capture"))
        if relay.owner_team_id == world.team_id:
            value = _relay_defend_value(world, relay, unit)
            if value > 0:
                goals = _adjacent_free(world, unit, relay.x, relay.y)
                if goals:
                    attractions.append((value, goals, "defend"))
    for well in world.wells.values():
        value = _well_value(world, well, unit, other)
        if value > 0:
            p = _pt(well.position)
            if world.free_for(p, unit):
                attractions.append((value, [p], "harvest"))
    safe_value, safe_goals = _safe_attraction(world, unit)
    if safe_value > 0 and safe_goals:
        attractions.append((safe_value, safe_goals, "safe"))
    for enemy in world.enemies:
        value = _engage_value(world, unit, enemy)
        if value > 0:
            goals = _adjacent_free(world, unit, enemy.x, enemy.y)
            if goals:
                attractions.append((value, goals, "engage"))
    if other is not None and unit.kind is UnitKind.BULWARK:
        if _manhattan(_pt(unit), _pt(other)) <= 4:
            goals = _adjacent_free(world, unit, other.x, other.y)
            if goals:
                attractions.append((14.0, goals, "protect"))
    return attractions


def _stay_penalty(world, unit):
    p = _pt(unit)
    penalty = 0.0
    damage = world.hazard_at(unit, p)
    if damage:
        penalty -= damage * 28.0
    if world.phase is not None and not world.inside_effective(p):
        penalty -= 180.0
    if p in world.danger:
        penalty -= 180.0
    if p in world.next_seals:
        penalty -= 40.0
    return penalty


def _move_score(world, unit, dest, attr_pots):
    p = _pt(unit)
    scores = []
    for value, kind, pot in attr_pots:
        d = pot.get(dest)
        if d is not None and d < INF:
            scores.append(value / (1.0 + 0.55 * d))
    scores.sort(reverse=True)
    score = scores[0] if scores else 0.0
    for extra in scores[1:3]:
        score += 0.2 * extra

    if dest in world.danger:
        score -= 190.0
    if dest in world.pulse:
        score -= 150.0
    if dest in world.next_seals:
        score -= 55.0
    if world.phase is not None:
        if not world.inside_effective(dest):
            score -= 150.0
        if not world.inside_effective(p) and world.inside_effective(dest):
            score += 230.0
    if world.hazard_at(unit, p) > 0 and world.hazard_at(unit, dest) == 0:
        score += 150.0

    # Wounded units should trade space for safety when a heavy threat is close.
    here_pressure = world.pressure(p)
    if unit.hp <= 4 and here_pressure >= 2:
        score += 12.0 * (here_pressure - world.pressure(dest))

    for enemy in world.enemies:
        d = _manhattan(dest, _pt(enemy))
        if d == 1:
            score -= 26.0 + (18.0 if unit.hp <= 3 else 0.0)
            if enemy.kind is UnitKind.BULWARK:
                score -= 8.0
            if enemy.immune:
                score += 10.0
        elif d == 2:
            score -= 7.0
            # A wounded unit should not linger a step away from a heavy hitter.
            if unit.hp <= 3 and enemy.kind is UnitKind.BULWARK:
                score -= 12.0
        elif d == 3 and enemy.kind is UnitKind.STRIDER:
            score -= 2.0

    # Stale-threat memory: avoid loitering where enemies were recently seen.
    for stamp, x, y, kind, hp in world.seen.values():
        if world.tick - stamp > 3:
            continue
        d = _manhattan(dest, (x, y))
        if d == 0:
            score -= 10.0
        elif d == 1:
            score -= 5.0

    if world.flux >= 1:
        for relay in world.relays.values():
            if relay.owner_team_id != world.team_id and _manhattan(dest, (relay.x, relay.y)) == 1:
                score += 12.0
                break
    well = world.well_at.get(dest)
    if well is not None and well.charged and world.flux < 12:
        score += 8.0

    if world.phase is not None and world.tick >= 121:
        pull = 1.8 if world.tick >= 131 else 0.7
        score += max(0.0, 10.0 - world.center_distance(dest)) * pull
    score += 0.001 * (-dest[0] - dest[1])
    return score


def _defend_score(world, unit):
    if unit.immune:
        return -1000.0
    incoming = world.incoming_damage(unit)
    hazard = world.hazard_at(unit, _pt(unit))
    total = incoming + hazard
    if total <= 0:
        return -20.0
    blocked = min(2, total)
    net = total - blocked
    score = blocked * 16.0
    if total == 1 and unit.hp > 3:
        score -= 10.0
    if total >= unit.hp:
        if net < unit.hp:
            return score + 520.0
        score += 70.0
    if unit.hp <= 3:
        score += 18.0
    return score + 0.8 * _stay_penalty(world, unit)


def _harvest_score(world, unit):
    well = world.well_at.get(_pt(unit))
    if well is None or not well.charged:
        return -1000.0
    gain = min(3, MAX_FLUX - world.flux)
    if gain <= 0:
        return -1000.0
    score = gain * (13.0 if world.flux < 8 else 8.0)
    score += _stay_penalty(world, unit)
    pressure = world.pressure(_pt(unit))
    if unit.hp <= 4 and pressure >= 2:
        score -= 12.0 * pressure
    return score


def _capture_score(world, unit, relay):
    if unit.immune or world.flux < 1:
        return -1000.0
    if relay.owner_team_id == world.team_id:
        return -1000.0
    if world.tick >= RELAY_CUTOFF and not world.inside_effective((relay.x, relay.y)):
        return -1000.0
    # Keep the last Flux for survival when the unit is fragile or pressured.
    if world.flux == 1 and (unit.hp <= 2 or world.expected_threat(_pt(unit)) > 0):
        return -1000.0
    # A visible rival adjacent to the relay very likely competes for the capture.
    if world.relay_enemy_adjacent(relay):
        return -1000.0
    value = 58.0
    if relay.owner_team_id is None:
        value += 20.0
    else:
        value += 10.0 + min(22.0, relay.uncontested_owner_ticks * 2.0)
    if relay.corrupted:
        value -= 16.0
    if world.relay_enemy_adjacent(relay):
        value -= 30.0
    elif relay.contested and not world.friendly_adjacent_to((relay.x, relay.y)):
        value -= 16.0
    incoming = world.incoming_damage(unit)
    if incoming >= unit.hp:
        if world.can_defend(unit, _MEM["last_defend"]):
            value -= 70.0
        else:
            value *= 0.1  # capture resolves after combat; a dying unit rarely gets it
    if world.flux == 1 and incoming > 0:
        value -= 14.0
    if world.phase is not None and world.tick >= 121 and not world.inside_effective((relay.x, relay.y)):
        value *= 0.5
    pressure = world.pressure(_pt(unit))
    if unit.hp <= 4 and pressure >= 2:
        value -= 10.0 * pressure
    return value + _stay_penalty(world, unit)


def _attack_score(world, unit, enemy):
    if unit.immune or world.flux < 2 or enemy.immune:
        return -1000.0
    p = _pt(unit)
    damage = _damage(unit.kind)
    kill = enemy.hp <= damage
    value = 14.0 * damage + (55.0 if kill else 0.0)
    value += 5.0 * _damage(enemy.kind)
    if enemy.kind is UnitKind.HUNTER:
        value *= 0.55
    if world.tick >= 141:
        value *= 0.75
    incoming = world.incoming_damage(unit)
    if incoming >= unit.hp:
        value -= 110.0
    elif unit.hp - incoming <= 2:
        value -= 22.0
    pressure = world.pressure(p)
    if unit.hp <= 4 and pressure >= 2:
        value -= 12.0 * pressure
        if pressure >= unit.hp:
            value -= 40.0
    return value + _stay_penalty(world, unit)


class Candidate:
    __slots__ = ("action", "cost", "score", "dest", "target", "relay")

    def __init__(self, action, cost, score, dest=None, target=None, relay=None):
        self.action = action
        self.cost = cost
        self.score = score
        self.dest = dest
        self.target = target
        self.relay = relay


def _candidates(world, unit, other, attr_pots):
    result = []
    p = _pt(unit)
    wait_score = _stay_penalty(world, unit)
    pressure = world.pressure(p)
    if unit.hp <= 4 and pressure >= 2:
        wait_score -= 12.0 * pressure
    result.append(Candidate(UnitAction.wait(unit.id), 0, wait_score))

    if world.can_defend(unit, _MEM["last_defend"]):
        result.append(Candidate(UnitAction.defend(unit.id), 1, _defend_score(world, unit)))

    well = world.well_at.get(p)
    if well is not None and well.charged:
        result.append(Candidate(UnitAction.harvest(unit.id), 0, _harvest_score(world, unit)))

    if not unit.immune and world.flux >= 1:
        for relay in world.relays.values():
            if _manhattan(p, (relay.x, relay.y)) == 1 and relay.owner_team_id != world.team_id:
                score = _capture_score(world, unit, relay)
                if score > -900.0:
                    result.append(Candidate(UnitAction.capture(unit.id, relay.id), 1, score, relay=relay.id))

    if not unit.immune and world.flux >= 2:
        for enemy in world.enemies:
            if _manhattan(p, _pt(enemy)) == 1:
                score = _attack_score(world, unit, enemy)
                if score > -900.0:
                    result.append(Candidate(UnitAction.attack(unit.id, enemy.id), 2, score, target=enemy.id))

    for direction, steps, dest in _legal_moves(world, unit):
        score = _move_score(world, unit, dest, attr_pots)
        result.append(Candidate(UnitAction.move(unit.id, direction, steps), 0, score, dest=dest))
    return result


def _pick(world, units, candidates):
    """Choose the highest-scoring affordable combination for both units."""
    flux = world.flux
    if not units:
        return []
    if len(units) == 1:
        unit = units[0]
        options = [c for c in candidates[unit.id] if c.cost <= flux]
        if not options:
            options = [Candidate(UnitAction.wait(unit.id), 0, 0.0)]
        return [max(options, key=lambda c: c.score)]

    first, second = units
    best_score = -INF
    best_pair = None
    for a in candidates[first.id]:
        for b in candidates[second.id]:
            if a.cost + b.cost > flux:
                continue
            if a.dest is not None and b.dest is not None:
                if a.dest == b.dest:
                    continue
                if a.dest == _pt(second) and b.dest == _pt(first):
                    continue
            if a.relay is not None and a.relay == b.relay:
                continue
            score = a.score + b.score
            if a.target is not None and a.target == b.target:
                score += 20.0
                damage = _damage(first.kind) + _damage(second.kind) + 1
                target = world.enemy_by_id.get(a.target)
                if target is not None and target.hp <= damage:
                    if _damage(first.kind) < target.hp and _damage(second.kind) < target.hp:
                        score += 46.0
            kinds = (a.action.kind.value, b.action.kind.value)
            if ("DEFEND" in kinds and "ATTACK" in kinds) or ("DEFEND" in kinds and "CAPTURE" in kinds):
                score += 6.0
            if kinds.count("HARVEST") == 2:
                first_gain = min(3, MAX_FLUX - flux)
                second_gain = min(3, MAX_FLUX - flux - first_gain)
                if second_gain <= 1:
                    score -= 18.0
            if flux - (a.cost + b.cost) == 0 and not ({"ATTACK", "DEFEND"} & set(kinds)):
                if any(world.incoming_damage(u) > 0 for u in units):
                    score -= 10.0
            if score > best_score:
                best_score = score
                best_pair = (a, b)
    if best_pair is None:
        return [Candidate(UnitAction.wait(u.id), 0, 0.0) for u in units]
    return list(best_pair)


def _decide(state):
    identity = (state.match_id, state.team.id)
    if _MEM["match"] != identity or state.tick < _MEM["last_tick"]:
        _reset_memory(identity)
    _MEM["last_tick"] = state.tick

    world = World(state, _MEM["seen"])
    _update_memory(world)
    units = sorted(world.units, key=lambda u: (u.kind is UnitKind.BULWARK, u.id))[:2]
    if not units:
        return ActionBundle()

    other = units[1] if len(units) == 2 else None
    candidates = {}
    for unit in units:
        partner = next((u for u in units if u.id != unit.id), None)
        attractions = _build_attractions(world, unit, partner)
        attr_pots = []
        for value, goals, kind in attractions:
            pot = _potential(world, unit, goals)
            if pot:
                attr_pots.append((value, kind, pot))
        candidates[unit.id] = _candidates(world, unit, partner, attr_pots)

    chosen = _pick(world, units, candidates)
    actions = tuple(c.action for c in chosen)
    priority = tuple(c.action.unit_id for c in sorted(chosen, key=lambda c: c.score, reverse=True))
    for c in chosen:
        if c.action.kind.value == "DEFEND":
            _MEM["last_defend"][c.action.unit_id] = state.tick
    return ActionBundle(actions, priority)


def decide(state):
    """SDK entry point. Never raise: an empty bundle is a legal WAIT."""
    try:
        return _decide(state)
    except Exception:
        if os.environ.get("ARENA_STRICT"):
            raise
        return ActionBundle()


if __name__ == "__main__":
    run(decide)
