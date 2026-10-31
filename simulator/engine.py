"""Approximate local rules engine for Relay Arena.

The official kit ships no full-match simulator. This engine follows the public
RULES.md/RULEBOOK.md resolution order closely enough to compare strategies:
action validation and Flux reservation, simultaneous attacks, simultaneous
movement, hazards, harvesting, captures, relay scoring, revival, Hunters and
the shrinking safe zone. It is NOT the official judge and must not be treated
as authoritative.
"""
from __future__ import annotations

import random

BOARD = 20
MAX_TICK = 160
DIRS = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}
COST = {"MOVE": 0, "ATTACK": 2, "DEFEND": 1, "HARVEST": 0, "CAPTURE": 1, "SCAN": 1, "WAIT": 0}
RELAY_POS = [(4, 4), (15, 4), (4, 15), (15, 15)]
WELL_POS = [(4, 5), (5, 4), (15, 5), (14, 4), (4, 14), (5, 15), (15, 14), (14, 15)]
SPAWN_BASE = {0: (2, 2), 1: (17, 2), 2: (2, 17), 3: (17, 17)}
CENTER = [(9, 9), (10, 9), (9, 10), (10, 10)]
CONTRACTIONS = [(111, 116, 14), (121, 126, 10), (131, 136, 7), (141, 146, 4), (151, 156, 2)]
HUNT_SPAWNS = [(3, 10), (10, 3), (16, 10), (10, 16), (6, 6), (13, 6), (6, 13), (13, 13)]


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def center_distance(p):
    return min(manhattan(p, c) for c in CENTER)


def radius_at(tick):
    radius = 18
    for _warning, start, value in CONTRACTIONS:
        if tick >= start:
            radius = value
    return radius


def next_contraction(tick):
    for warning, start, value in CONTRACTIONS:
        if tick < start:
            return {"warning_at_tick": warning, "starts_at_tick": start, "safe_radius": value}
    return None


def max_steps(kind):
    return 2 if kind == "STRIDER" else 1


def damage_of(kind):
    return 2 if kind == "BULWARK" else 1


class Unit:
    __slots__ = ("id", "team_id", "kind", "x", "y", "hp", "alive", "immune_until",
                 "arrival_tick", "last_defend_tick")

    def __init__(self, uid, team_id, kind, x, y):
        self.id = uid
        self.team_id = team_id
        self.kind = kind
        self.x = x
        self.y = y
        self.hp = 6 if kind == "STRIDER" else 10
        self.alive = True
        self.immune_until = -1
        self.arrival_tick = -1
        self.last_defend_tick = -99

    @property
    def pos(self):
        return (self.x, self.y)

    def immune(self, tick):
        return tick <= self.immune_until

    def obs(self, tick):
        return {
            "id": self.id,
            "team_id": self.team_id,
            "kind": self.kind,
            "x": self.x,
            "y": self.y,
            "hp": self.hp,
            "immune": self.immune(tick),
        }


class Team:
    __slots__ = ("id", "flux", "units", "points", "deaths", "revival_charge",
                 "revival_queue", "eliminated", "elimination_tick", "scan_until")

    def __init__(self, team_id, units):
        self.id = team_id
        self.flux = 6
        self.units = units
        self.points = 0
        self.deaths = 0
        self.revival_charge = True
        self.revival_queue = []
        self.eliminated = False
        self.elimination_tick = None
        self.scan_until = -1

    def alive_units(self):
        return [u for u in self.units if u.alive]

    def hp_sum(self):
        return sum(max(0, u.hp) for u in self.units if u.alive)


class Relay:
    __slots__ = ("id", "x", "y", "owner", "counter")

    def __init__(self, rid, x, y):
        self.id = rid
        self.x = x
        self.y = y
        self.owner = None
        self.counter = 0

    @property
    def pos(self):
        return (self.x, self.y)


class Well:
    __slots__ = ("id", "x", "y", "charged", "available_from_tick")

    def __init__(self, wid, x, y):
        self.id = wid
        self.x = x
        self.y = y
        self.charged = True
        self.available_from_tick = None

    @property
    def pos(self):
        return (self.x, self.y)


class Warning:
    __slots__ = ("kind", "starts", "ends", "center", "relay_id", "tiles", "spawned")

    def __init__(self, kind, starts, ends, center, relay_id, tiles):
        self.kind = kind
        self.starts = starts
        self.ends = ends
        self.center = center
        self.relay_id = relay_id
        self.tiles = list(tiles)
        self.spawned = False

    def obs(self):
        return {
            "kind": self.kind,
            "starts_at_tick": self.starts,
            "ends_after_tick": self.ends,
            "center": None if self.center is None else {"x": self.center[0], "y": self.center[1]},
            "relay_id": self.relay_id,
            "tiles": [{"x": x, "y": y} for x, y in self.tiles],
        }


def _spawn_cells(base, walls, relays, wells):
    """Two adjacent free cells near a corner, scout first."""
    reserved = set(walls) | set(relays) | set(wells)
    bx, by = base
    candidates = []
    for radius in range(0, 5):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if abs(dx) + abs(dy) != radius:
                    continue
                p = (bx + dx, by + dy)
                if 0 <= p[0] < BOARD and 0 <= p[1] < BOARD and p not in reserved:
                    candidates.append(p)
    if not candidates:
        return []
    first = candidates[0]
    cells = [first]
    for p in candidates[1:]:
        if manhattan(p, first) == 1:
            cells.append(p)
            break
    return cells


def make_walls(kind, rng):
    walls = set()
    relays = set(RELAY_POS)
    wells = set(WELL_POS)
    spawns = set(SPAWN_BASE.values())
    protected = relays | wells | spawns

    def add(p):
        if 0 <= p[0] < BOARD and 0 <= p[1] < BOARD and p not in protected:
            walls.add(p)

    if kind == "open":
        pass
    elif kind == "pillars":
        blocks = [[(7, 6), (8, 6), (7, 7)], [(11, 6), (12, 6), (12, 7)],
                  [(7, 12), (7, 13), (8, 13)], [(12, 12), (12, 13), (11, 13)],
                  [(9, 4), (10, 4)], [(9, 15), (10, 15)], [(4, 9), (4, 10)], [(15, 9), (15, 10)]]
        for block in blocks:
            for p in block:
                add(p)
    elif kind == "cross":
        for i in range(BOARD):
            if i not in (0, 1, 2, 3, 16, 17, 18, 19, 8, 9, 10, 11):
                add((9, i))
                add((10, i))
                add((i, 9))
                add((i, 10))
    elif kind == "xmap":
        for i in range(BOARD):
            if i % 4 == 0:
                continue
            add((i, i))
            add((i, BOARD - 1 - i))
    else:
        for _ in range(36):
            x = rng.randint(0, BOARD - 1)
            y = rng.randint(0, BOARD - 1)
            add((x, y))
            add((BOARD - 1 - x, BOARD - 1 - y))
    return walls


class Game:
    def __init__(self, seed=0, map_name="open", match_id="sim"):
        self.seed = seed
        self.map_name = map_name
        self.match_id = match_id
        map_salt = sum((i + 1) * ord(ch) for i, ch in enumerate(map_name)) % 100000
        self.rng = random.Random(seed * 1000003 + map_salt)
        self.tick = 1
        self.walls = make_walls(map_name, self.rng)
        self.relays = {i: Relay(i, x, y) for i, (x, y) in enumerate(RELAY_POS)}
        self.wells = {i: Well(i, x, y) for i, (x, y) in enumerate(WELL_POS)}
        self.teams = {}
        self.hunters = []
        self.next_hunter_id = 101
        self.warnings = []
        self.drops = {}
        self.invalid_counts = {i: 0 for i in range(4)}
        self.end_tick = None
        self._make_teams()
        self._warning_schedule = list(range(12, MAX_TICK, 9))

    def _make_teams(self):
        for team_id in range(4):
            cells = _spawn_cells(SPAWN_BASE[team_id], self.walls, RELAY_POS, WELL_POS)
            while len(cells) < 2:
                cells.append((SPAWN_BASE[team_id][0], SPAWN_BASE[team_id][1]))
            u1 = Unit(team_id * 10 + 1, team_id, "STRIDER", *cells[0])
            u2 = Unit(team_id * 10 + 2, team_id, "BULWARK", *cells[1])
            self.teams[team_id] = Team(team_id, [u1, u2])

    # -- helpers --------------------------------------------------------
    @property
    def occupied(self):
        cells = set()
        for team in self.teams.values():
            for u in team.alive_units():
                cells.add(u.pos)
        for h in self.hunters:
            if h.alive:
                cells.add(h.pos)
        return cells

    def blockers(self):
        return set(self.walls) | {r.pos for r in self.relays.values()} | self.occupied

    def active_seals(self):
        cells = set()
        for w in self.warnings:
            if w.kind == "SEAL" and w.starts <= self.tick <= w.ends:
                cells.update(w.tiles)
        return cells

    def active_pulse(self):
        cells = set()
        for w in self.warnings:
            if w.kind == "PULSE" and w.starts <= self.tick <= w.ends:
                cells.update(w.tiles)
        return cells

    def active_corrupt_relays(self):
        ids = set()
        for w in self.warnings:
            if w.kind == "CORRUPT" and w.starts <= self.tick <= w.ends and w.relay_id is not None:
                ids.add(w.relay_id)
        return ids

    def relay_contested_by(self, relay):
        if relay.owner is None:
            for team in self.teams.values():
                for u in team.alive_units():
                    if manhattan(u.pos, relay.pos) == 1:
                        return True
            return False
        for team in self.teams.values():
            if team.id == relay.owner:
                continue
            for u in team.alive_units():
                if manhattan(u.pos, relay.pos) == 1:
                    return True
        return False

    def relay_contested_for_obs(self, relay):
        return self.relay_contested_by(relay)

    def _relay_scoring_contested(self, relay):
        if relay.owner is None:
            return True
        for team in self.teams.values():
            if team.id == relay.owner:
                continue
            for u in team.alive_units():
                if manhattan(u.pos, relay.pos) == 1:
                    return True
        return False

    # -- tick setup -----------------------------------------------------
    def begin_tick(self):
        for well in self.wells.values():
            if not well.charged and well.available_from_tick is not None and self.tick >= well.available_from_tick:
                well.charged = True
                well.available_from_tick = None
        self._process_revivals()
        self._generate_warnings()
        self._spawn_hunters()

    def _generate_warnings(self):
        self.warnings = [w for w in self.warnings if w.ends >= self.tick]
        if len(self.warnings) >= 3:
            return
        if self.tick not in self._warning_schedule:
            return
        alive = [(team.id, u) for team in self.teams.values() for u in team.alive_units()]
        if not alive:
            return
        index = self._warning_schedule.index(self.tick)
        kind = ("PULSE", "SEAL", "CORRUPT", "HUNT")[index % 4]
        if kind == "PULSE":
            _tid, target = self.rng.choice(alive)
            cx, cy = target.pos
            tiles = [(x, y) for y in range(cy - 1, cy + 2) for x in range(cx - 1, cx + 2)
                     if 0 <= x < BOARD and 0 <= y < BOARD]
            self.warnings.append(Warning("PULSE", self.tick + 1, self.tick + 1, (cx, cy), None, tiles))
        elif kind == "SEAL":
            relay = self.relays[self.rng.randrange(len(self.relays))]
            horizontal = self.rng.random() < 0.5
            tiles = []
            for k in range(-2, 3):
                x = relay.x + (k if horizontal else 0)
                y = relay.y + (0 if horizontal else k)
                if 0 <= x < BOARD and 0 <= y < BOARD:
                    tiles.append((x, y))
            self.warnings.append(Warning("SEAL", self.tick + 1, self.tick + 2, relay.pos, None, tiles))
        elif kind == "CORRUPT":
            relay = self.relays[self.rng.randrange(len(self.relays))]
            self.warnings.append(Warning("CORRUPT", self.tick + 1, self.tick + 2, relay.pos, relay.id, [relay.pos]))
        else:
            spawn = HUNT_SPAWNS[self.rng.randrange(len(HUNT_SPAWNS))]
            tiles = [(spawn[0] + dx, spawn[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                     if 0 <= spawn[0] + dx < BOARD and 0 <= spawn[1] + dy < BOARD]
            self.warnings.append(Warning("HUNT", self.tick + 3, self.tick + 3, spawn, None, tiles))

    def _spawn_hunters(self):
        for warning in self.warnings:
            if warning.kind != "HUNT" or warning.spawned or warning.starts != self.tick:
                continue
            warning.spawned = True
            if len([h for h in self.hunters if h.alive]) >= 4:
                continue
            blocked = self.blockers()
            cell = None
            if warning.center is not None and warning.center not in blocked:
                cell = warning.center
            else:
                for p in warning.tiles:
                    if p not in blocked:
                        cell = p
                        break
            if cell is None:
                continue
            hunter = Unit(self.next_hunter_id, None, "HUNTER", *cell)
            hunter.hp = 4
            hunter.arrival_tick = self.tick
            self.next_hunter_id += 1
            self.hunters.append(hunter)

    def _process_revivals(self):
        for team in self.teams.values():
            if not team.revival_queue:
                continue
            entry = team.revival_queue[0]
            if self.tick < entry["due"]:
                continue
            cell = self._free_spawn(team.id)
            if cell is None:
                entry["retries"] += 1
                entry["due"] += 1
                if entry["retries"] > 3:
                    team.revival_queue.pop(0)
                continue
            unit = next((u for u in team.units if u.id == entry["unit_id"]), None)
            if unit is None:
                team.revival_queue.pop(0)
                continue
            unit.alive = True
            unit.hp = 6 if unit.kind == "STRIDER" else 10
            unit.x, unit.y = cell
            unit.immune_until = self.tick + 1
            unit.arrival_tick = self.tick
            unit.last_defend_tick = -99
            team.revival_queue.pop(0)

    def _free_spawn(self, team_id):
        base = SPAWN_BASE[team_id]
        blocked = self.blockers() | self.active_seals()
        for radius in range(0, 5):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if abs(dx) + abs(dy) != radius:
                        continue
                    p = (base[0] + dx, base[1] + dy)
                    if 0 <= p[0] < BOARD and 0 <= p[1] < BOARD and p not in blocked:
                        return p
        return None

    # -- observations ---------------------------------------------------
    def observe(self, team_id):
        team = self.teams[team_id]
        own = team.alive_units()
        vision = 10 if team.scan_until >= self.tick else 5
        visible = []
        for other in self.teams.values():
            if other.id == team_id:
                continue
            for u in other.alive_units():
                if any(manhattan(u.pos, o.pos) <= vision for o in own):
                    visible.append(u)
        for h in self.hunters:
            if h.alive and any(manhattan(h.pos, o.pos) <= vision for o in own):
                visible.append(h)
        visible.sort(key=lambda u: u.id)
        terrain = []
        for x, y in sorted(self.walls):
            terrain.append({"x": x, "y": y, "kind": "WALL", "object_id": None})
        for relay in self.relays.values():
            terrain.append({"x": relay.x, "y": relay.y, "kind": "CONTROL_RELAY", "object_id": relay.id})
        for well in self.wells.values():
            terrain.append({"x": well.x, "y": well.y, "kind": "FLUX_WELL", "object_id": well.id})
        corrupted = self.active_corrupt_relays()
        relays = []
        for relay in self.relays.values():
            relays.append({
                "id": relay.id,
                "x": relay.x,
                "y": relay.y,
                "owner_team_id": relay.owner,
                "contested": self.relay_contested_for_obs(relay),
                "corrupted": relay.id in corrupted,
                "uncontested_owner_ticks": relay.counter if relay.owner is not None else 0,
            })
        wells = []
        for well in self.wells.values():
            wells.append({
                "id": well.id,
                "position": {"x": well.x, "y": well.y},
                "charged": well.charged,
                "available_from_tick": well.available_from_tick,
            })
        warnings = [w.obs() for w in self.warnings if w.ends >= self.tick]
        phase = None
        if self.tick >= 111:
            phase = {
                "safe_zone_center_tiles": [{"x": x, "y": y} for x, y in CENTER],
                "safe_radius": radius_at(self.tick),
                "hazard_damage": 2,
                "next_contraction": next_contraction(self.tick),
            }
        return {
            "match_id": self.match_id,
            "tick": self.tick,
            "team": {"id": team_id, "flux": team.flux, "units": [u.obs(self.tick) for u in own]},
            "visible_enemies": [u.obs(self.tick) for u in visible],
            "terrain": terrain,
            "wells": wells,
            "relays": relays,
            "warden_telegraphs": warnings,
            "phase_four": phase,
        }

    # -- action validation ---------------------------------------------
    def _ordered_actions(self, team, actions, priority):
        by_unit = {}
        duplicates = set()
        for action in actions:
            if not isinstance(action, dict):
                continue
            uid = action.get("unit_id")
            if type(uid) is not int:
                continue
            if uid in by_unit:
                duplicates.add(uid)
            by_unit[uid] = action
        for uid in duplicates:
            by_unit.pop(uid, None)
        order = []
        for uid in priority:
            if uid in by_unit:
                order.append(uid)
        for unit in sorted(team.units, key=lambda u: (u.kind != "STRIDER", u.id)):
            if unit.id in by_unit and unit.id not in order:
                order.append(unit.id)
        for uid in sorted(by_unit):
            if uid not in order and uid in {u.id for u in team.units}:
                order.append(uid)
        return [by_unit[uid] for uid in order]

    def _validate(self, team, unit, action):
        name = action.get("action")
        if name not in COST or unit is None or not unit.alive:
            return False
        if name == "MOVE":
            direction = action.get("direction")
            steps = action.get("steps")
            if direction not in DIRS or type(steps) is not int or not 1 <= steps <= max_steps(unit.kind):
                return False
        elif name == "ATTACK":
            if unit.immune(self.tick):
                return False
            target = self._unit_by_id(action.get("target_unit_id"))
            if target is None or not target.alive or target.immune(self.tick):
                return False
            if target.team_id == unit.team_id:
                return False
            if manhattan(unit.pos, target.pos) != 1:
                return False
        elif name == "CAPTURE":
            if unit.immune(self.tick):
                return False
            relay = self.relays.get(action.get("relay_id"))
            if relay is None or manhattan(unit.pos, relay.pos) != 1:
                return False
        elif name == "HARVEST":
            well = next((w for w in self.wells.values() if w.pos == unit.pos), None)
            if well is None or not well.charged:
                return False
        elif name == "DEFEND":
            # Immunity blocks ATTACK/CAPTURE only; DEFEND and HARVEST stay legal.
            if unit.last_defend_tick == self.tick - 1:
                return False
        return True

    def _unit_by_id(self, uid):
        for team in self.teams.values():
            for unit in team.units:
                if unit.id == uid:
                    return unit
        for hunter in self.hunters:
            if hunter.id == uid:
                return hunter
        return None

    def reserve_actions(self, team_id, actions, priority):
        """Return accepted actions by unit id and spend Flux in priority order."""
        team = self.teams[team_id]
        accepted = {}
        for action in self._ordered_actions(team, actions, priority):
            uid = action.get("unit_id")
            unit = next((u for u in team.units if u.id == uid), None)
            name = action.get("action")
            if unit is None or not unit.alive or not self._validate(team, unit, action):
                self.invalid_counts[team_id] += 1
                continue
            cost = COST[name]
            if cost > team.flux:
                self.invalid_counts[team_id] += 1
                continue
            team.flux -= cost
            accepted[uid] = dict(action)
            if name == "DEFEND":
                unit.last_defend_tick = self.tick
            elif name == "SCAN":
                team.scan_until = self.tick + 1
        return accepted

    # -- resolution -----------------------------------------------------
    def resolve(self, team_actions):
        """team_actions: {team_id: (actions, priority)}"""
        tick = self.tick
        accepted = {}
        for team_id in range(4):
            actions, priority = team_actions.get(team_id, ([], []))
            accepted[team_id] = self.reserve_actions(team_id, actions, priority)

        defend_pool = {}
        for team in self.teams.values():
            for unit in team.alive_units():
                action = accepted[team.id].get(unit.id)
                if action and action.get("action") == "DEFEND":
                    defend_pool[unit.id] = 2

        # 1. Attacks (players and Hunters) are simultaneous.
        attacks = []
        for team in self.teams.values():
            for unit in team.alive_units():
                action = accepted[team.id].get(unit.id)
                if action and action.get("action") == "ATTACK":
                    attacks.append((unit, self._unit_by_id(action.get("target_unit_id"))))
        for hunter in self.hunters:
            if not hunter.alive or hunter.arrival_tick == tick:
                continue
            targets = [u for team in self.teams.values() for u in team.alive_units()]
            if not targets:
                continue
            target = min(targets, key=lambda u: (manhattan(hunter.pos, u.pos), u.hp, u.id))
            if manhattan(hunter.pos, target.pos) == 1 and not target.immune(tick):
                attacks.append((hunter, target))

        damage = {}
        for attacker, target in attacks:
            if attacker is None or target is None or not attacker.alive or not target.alive:
                continue
            if target.immune(tick):
                continue
            damage[target.id] = damage.get(target.id, 0) + damage_of(attacker.kind)
        # Allied focus-fire bonus: +1 for each target hit by 2+ units of one team.
        by_target_team = {}
        for attacker, target in attacks:
            if attacker.team_id is None or target is None or not target.alive:
                continue
            by_target_team.setdefault(target.id, {}).setdefault(attacker.team_id, 0)
            by_target_team[target.id][attacker.team_id] += 1
        for target_id, team_counts in by_target_team.items():
            for count in team_counts.values():
                if count >= 2:
                    damage[target_id] = damage.get(target_id, 0) + 1
        for uid, incoming in damage.items():
            unit = self._unit_by_id(uid)
            if unit is None or not unit.alive:
                continue
            pool = defend_pool.get(uid, 0)
            used = min(pool, incoming)
            defend_pool[uid] = pool - used
            remaining = incoming - used
            unit.hp -= remaining
            if unit.hp <= 0:
                unit.alive = False
                if unit.team_id is not None:
                    self.teams[unit.team_id].deaths += 1
                    if any(a.team_id is not None for a, t in attacks if t is unit):
                        self.drops[unit.pos] = self.drops.get(unit.pos, 0) + 2

        # 2. Simultaneous movement (living units only).
        intents = []
        for team in self.teams.values():
            for unit in team.alive_units():
                action = accepted[team.id].get(unit.id)
                if action and action.get("action") == "MOVE":
                    intents.append(self._player_move_intent(unit, action))
        for hunter in self.hunters:
            if hunter.alive and hunter.arrival_tick != tick:
                intent = self._hunter_move_intent(hunter)
                if intent is not None:
                    intents.append(intent)
        self._resolve_movement(intents)

        # Collect Flux drops.
        for team in self.teams.values():
            for unit in team.alive_units():
                if unit.pos in self.drops:
                    team.flux = min(14, team.flux + self.drops.pop(unit.pos))

        # 3. Safe-zone then PULSE damage.
        radius = radius_at(tick)
        pulse = self.active_pulse()
        for team in self.teams.values():
            for unit in team.alive_units():
                if unit.immune(tick):
                    continue
                hazard = 0
                if center_distance(unit.pos) > radius:
                    hazard += 2
                if unit.pos in pulse:
                    hazard += 2
                if hazard:
                    pool = defend_pool.get(unit.id, 0)
                    used = min(pool, hazard)
                    defend_pool[unit.id] = pool - used
                    unit.hp -= hazard - used
                    if unit.hp <= 0:
                        unit.alive = False
                        team.deaths += 1

        # 4. Harvesting.
        for team in self.teams.values():
            for unit in team.alive_units():
                action = accepted[team.id].get(unit.id)
                if action and action.get("action") == "HARVEST":
                    well = next((w for w in self.wells.values() if w.pos == unit.pos), None)
                    if well is not None and well.charged:
                        team.flux = min(14, team.flux + 3)
                        well.charged = False
                        well.available_from_tick = tick + 6

        # 5. Captures (competing teams cancel).
        captures = {}
        for team in self.teams.values():
            for unit in team.alive_units():
                action = accepted[team.id].get(unit.id)
                if action and action.get("action") == "CAPTURE":
                    captures.setdefault(action.get("relay_id"), []).append(team.id)
        for relay_id, teams in captures.items():
            relay = self.relays.get(relay_id)
            if relay is None:
                continue
            unique = set(teams)
            if len(unique) != 1:
                continue
            owner = next(iter(unique))
            if relay.owner != owner:
                relay.owner = owner
                relay.counter = 0

        # 6. Relay scoring (eliminated owners are neutralised first).
        self._neutralise_eliminated_relays()
        corrupted = self.active_corrupt_relays()
        for relay in self.relays.values():
            if relay.owner is None:
                continue
            if relay.id in corrupted or self._relay_scoring_contested(relay):
                continue
            relay.counter += 1
            if relay.counter % 2 == 0:
                self.teams[relay.owner].points += 1

        # 7. Flux regeneration (after three completed ticks, living unit).
        if tick % 3 == 0:
            for team in self.teams.values():
                if team.alive_units():
                    team.flux = min(14, team.flux + 1)

        # 8. Queue revivals for deaths this tick, then elimination checks.
        self._queue_revivals()
        self._check_eliminations()

        self.end_tick = tick
        active = [t for t in self.teams.values() if not t.eliminated]
        if tick >= MAX_TICK or len(active) <= 1:
            self.tick += 1
            return True
        self.tick += 1
        return False

    def _player_move_intent(self, unit, action):
        direction = action.get("direction")
        steps = action.get("steps")
        dx, dy = DIRS[direction]
        path = []
        for k in range(1, steps + 1):
            path.append((unit.x + dx * k, unit.y + dy * k))
        return {"unit": unit, "path": path, "dest": path[-1]}

    def _hunter_move_intent(self, hunter):
        targets = [u for team in self.teams.values() for u in team.alive_units()]
        if not targets:
            return None
        target = min(targets, key=lambda u: (manhattan(hunter.pos, u.pos), u.id))
        blocked = set(self.walls) | {r.pos for r in self.relays.values()} | self.active_seals() | self.occupied
        best = None
        for direction, (dx, dy) in DIRS.items():
            p = (hunter.x + dx, hunter.y + dy)
            if not (0 <= p[0] < BOARD and 0 <= p[1] < BOARD) or p in blocked:
                continue
            score = manhattan(p, target.pos)
            if score < manhattan(hunter.pos, target.pos) and (best is None or score < best[0]):
                best = (score, p)
        if best is None:
            return None
        return {"unit": hunter, "path": [best[1]], "dest": best[1]}

    def _resolve_movement(self, intents):
        if not intents:
            return
        static = set(self.walls) | {r.pos for r in self.relays.values()} | self.active_seals()
        starts = {intent["unit"].id: intent["unit"].pos for intent in intents}
        alive_units = {u.id: u for team in self.teams.values() for u in team.alive_units()}
        for h in self.hunters:
            if h.alive:
                alive_units[h.id] = h
        valid = {}
        for intent in intents:
            unit = intent["unit"]
            path = intent["path"]
            ok = True
            for p in path:
                if not (0 <= p[0] < BOARD and 0 <= p[1] < BOARD) or p in static:
                    ok = False
                    break
            # Intermediate occupancy is treated conservatively.
            if ok:
                for p in path[:-1]:
                    if p in {u.pos for u in alive_units.values() if u.id != unit.id}:
                        ok = False
                        break
            valid[unit.id] = ok
        changed = True
        while changed:
            changed = False
            movable = {uid for uid, ok in valid.items() if ok}
            # Stationary occupants block.
            for intent in intents:
                uid = intent["unit"].id
                if not valid.get(uid):
                    continue
                dest = intent["dest"]
                for other_id, other in alive_units.items():
                    if other_id == uid:
                        continue
                    if other.pos == dest and other_id not in movable:
                        valid[uid] = False
                        changed = True
                        break
            # Competing arrivals cancel.
            dest_groups = {}
            for intent in intents:
                uid = intent["unit"].id
                if valid.get(uid):
                    dest_groups.setdefault(intent["dest"], []).append(uid)
            for ids in dest_groups.values():
                if len(ids) > 1:
                    for uid in ids:
                        if valid.get(uid):
                            valid[uid] = False
                            changed = True
            # Opposing swaps cancel.
            for intent in intents:
                uid = intent["unit"].id
                if not valid.get(uid):
                    continue
                for other_id, other_start in starts.items():
                    if other_id == uid:
                        continue
                    other_intent = next((i for i in intents if i["unit"].id == other_id), None)
                    if other_intent is None:
                        continue
                    if intent["dest"] == other_start and other_intent["dest"] == starts.get(uid):
                        valid[uid] = False
                        valid[other_id] = False
                        changed = True
        for intent in intents:
            if valid.get(intent["unit"].id):
                x, y = intent["dest"]
                intent["unit"].x = x
                intent["unit"].y = y

    def _queue_revivals(self):
        for team in self.teams.values():
            dead = [u for u in team.units if not u.alive]
            if not dead or not team.revival_charge or team.revival_queue:
                continue
            died_now = [u for u in dead if u.id == team.units[0].id or u.id == team.units[1].id]
            if not died_now:
                continue
            # Only queue if at least one death happened this tick.
            # Units do not carry a death tick, so compare against a short window is
            # avoided: queue immediately on the tick the team first has a dead unit.
            chosen = sorted(dead, key=lambda u: (u.kind != "STRIDER", u.id))[0]
            team.revival_charge = False
            team.revival_queue.append({
                "unit_id": chosen.id,
                "due": self.tick + 4,
                "retries": 0,
            })

    def _neutralise_eliminated_relays(self):
        for relay in self.relays.values():
            if relay.owner is not None and self.teams[relay.owner].eliminated:
                relay.owner = None
                relay.counter = 0

    def _check_eliminations(self):
        for team in self.teams.values():
            if team.eliminated:
                continue
            if not team.alive_units() and not team.revival_queue and not team.revival_charge:
                team.eliminated = True
                team.elimination_tick = self.tick
        self._neutralise_eliminated_relays()

    # -- results --------------------------------------------------------
    def results(self):
        self._check_eliminations()
        active = [t for t in self.teams.values() if not t.eliminated]
        eliminated = [t for t in self.teams.values() if t.eliminated]
        active.sort(key=lambda t: (-t.points, -t.hp_sum(), t.deaths, t.id))
        eliminated.sort(key=lambda t: (-(t.elimination_tick or 0), -t.points, t.id))
        ordered = active + eliminated
        keys = []
        for team in ordered:
            if not team.eliminated:
                keys.append(("active", -team.points, -team.hp_sum(), team.deaths))
            else:
                keys.append(("out", -(team.elimination_tick or 0), -team.points))
        placements = {}
        index = 0
        while index < len(ordered):
            end = index + 1
            while end < len(ordered) and keys[end] == keys[index]:
                end += 1
            positions = list(range(index + 1, end + 1))
            points = sum(5 - p for p in positions) / len(positions)
            for k in range(index, end):
                placements[ordered[k].id] = {"placement": sum(positions) / len(positions), "points": points}
            index = end
        return {
            "match_id": self.match_id,
            "seed": self.seed,
            "map": self.map_name,
            "end_tick": self.end_tick,
            "teams": {
                team.id: {
                    "placement": placements[team.id]["placement"],
                    "placement_points": placements[team.id]["points"],
                    "relay_points": team.points,
                    "hp": team.hp_sum(),
                    "deaths": team.deaths,
                    "eliminated": team.eliminated,
                    "elimination_tick": team.elimination_tick,
                    "flux": team.flux,
                    "invalid_actions": self.invalid_counts[team.id],
                }
                for team in ordered
            },
        }
