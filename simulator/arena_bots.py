"""Four deterministic bots authored from the arena skill's four agent roles.

The MIT-licensed arena skill (github.com/Jakeschincariol/arena-skill) defines
four sub-agent roles: competitor, attacker, defender and judge. Each role wrote
one policy spec; this module implements those specs as simulator bots. They are
the only bots used by the arena sessions. No code is copied from that project;
the concepts and role definitions are adapted with attribution (see
``third_party/arena-skill``).

Every bot consumes the simulator's SDK-shaped observation dict and returns
``(actions, priority)`` wire lists.
"""
from __future__ import annotations

from collections import deque

BOARD = 20
DIRS = (("N", 0, -1), ("E", 1, 0), ("S", 0, 1), ("W", -1, 0))
COST = {"MOVE": 0, "ATTACK": 2, "DEFEND": 1, "HARVEST": 0, "CAPTURE": 1, "SCAN": 1, "WAIT": 0}
DAMAGE = {"STRIDER": 1, "BULWARK": 2, "HUNTER": 1}
INF = 10**6


def _pt(obj):
    return (obj["x"], obj["y"])


def _manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _max_steps(kind):
    return 2 if kind == "STRIDER" else 1


def _clamp(value, low=0.0, high=10.0):
    return max(low, min(high, value))


def _centre_distance(point, centres):
    return min(_manhattan(point, c) for c in centres)


def _active_tiles(obs, kind, when):
    tiles = set()
    for warning in obs["warden_telegraphs"]:
        if warning["kind"] == kind and warning["starts_at_tick"] <= when <= warning["ends_after_tick"]:
            tiles.update((t["x"], t["y"]) for t in warning["tiles"])
    return tiles


def _next_tiles(obs, kind):
    return _active_tiles(obs, kind, obs["tick"] + 1)


def _static_blocked(obs):
    blocked = set()
    for cell in obs["terrain"]:
        if cell["kind"] in ("WALL", "CONTROL_RELAY"):
            blocked.add((cell["x"], cell["y"]))
    for relay in obs["relays"]:
        blocked.add((relay["x"], relay["y"]))
    tick = obs["tick"]
    for warning in obs["warden_telegraphs"]:
        if warning["kind"] == "SEAL" and warning["starts_at_tick"] <= tick <= warning["ends_after_tick"]:
            blocked.update((t["x"], t["y"]) for t in warning["tiles"])
    return blocked


def _safe_zone(obs):
    phase = obs.get("phase_four")
    if not phase:
        return None, None, None, None
    centres = [(c["x"], c["y"]) for c in phase["safe_zone_center_tiles"]]
    radius = phase["safe_radius"]
    step = phase.get("next_contraction")
    next_radius = step["safe_radius"] if step else radius
    start = step["starts_at_tick"] if step else None
    return radius, next_radius, centres, start


def _incoming(cell, enemies):
    total = 0
    teams = []
    for enemy in enemies:
        if enemy["immune"] or _manhattan(cell, _pt(enemy)) != 1:
            continue
        total += DAMAGE[enemy["kind"]]
        if enemy["team_id"] is not None:
            teams.append(enemy["team_id"])
    for team_id in set(teams):
        if teams.count(team_id) >= 2:
            total += 1
    return total


def _threat(cell, enemies):
    total = 0
    teams = []
    for enemy in enemies:
        if enemy["immune"]:
            continue
        if _manhattan(cell, _pt(enemy)) <= 1 + _max_steps(enemy["kind"]):
            total += DAMAGE[enemy["kind"]]
            if enemy["team_id"] is not None:
                teams.append(enemy["team_id"])
    for team_id in set(teams):
        if teams.count(team_id) >= 2:
            total += 1
    return total


def _hazard(obs, cell, radius, pulse_now):
    value = 0
    if radius is not None and _centre_distance(cell, ((9, 9), (10, 9), (9, 10), (10, 10))) > radius:
        value += 2
    if cell in pulse_now:
        value += 2
    return value


def _bfs(start, blocked):
    dist = {start: 0}
    queue = deque([start])
    while queue:
        point = queue.popleft()
        for _name, dx, dy in DIRS:
            nxt = (point[0] + dx, point[1] + dy)
            if not (0 <= nxt[0] < BOARD and 0 <= nxt[1] < BOARD):
                continue
            if nxt in blocked or nxt in dist:
                continue
            dist[nxt] = dist[point] + 1
            queue.append(nxt)
    return dist


def _bfs_from(goals, blocked):
    dist = {}
    queue = deque()
    for goal in goals:
        if goal not in dist:
            dist[goal] = 0
            queue.append(goal)
    while queue:
        point = queue.popleft()
        for _name, dx, dy in DIRS:
            nxt = (point[0] + dx, point[1] + dy)
            if not (0 <= nxt[0] < BOARD and 0 <= nxt[1] < BOARD):
                continue
            if nxt in blocked or nxt in dist:
                continue
            dist[nxt] = dist[point] + 1
            queue.append(nxt)
    return dist


def _stand_cells(cells, blocked):
    result = set()
    for cell in cells:
        for _name, dx, dy in DIRS:
            point = (cell[0] + dx, cell[1] + dy)
            if 0 <= point[0] < BOARD and 0 <= point[1] < BOARD and point not in blocked:
                result.add(point)
    return result


def _legal_moves(unit, blocked, occupied, planned):
    start = _pt(unit)
    moves = []
    for name, dx, dy in DIRS:
        for steps in range(1, _max_steps(unit["kind"]) + 1):
            path = [(start[0] + dx * k, start[1] + dy * k) for k in range(1, steps + 1)]
            if any(not (0 <= p[0] < BOARD and 0 <= p[1] < BOARD) for p in path):
                break
            if any(p in blocked or p in occupied or p in planned for p in path):
                break
            moves.append((name, steps, path[-1]))
    return moves


def _state(obs):
    units = [u for u in obs["team"]["units"] if u["hp"] > 0]
    enemies = [e for e in obs["visible_enemies"] if e["hp"] > 0]
    relays = {r["id"]: r for r in obs["relays"]}
    wells = {w["id"]: w for w in obs["wells"]}
    blocked = _static_blocked(obs)
    occupied = {_pt(u) for u in units} | {_pt(e) for e in enemies}
    radius, next_radius, centres, contraction_start = _safe_zone(obs)
    return {
        "units": units,
        "enemies": enemies,
        "relays": relays,
        "wells": wells,
        "blocked": blocked,
        "occupied": occupied,
        "flux": obs["team"]["flux"],
        "tick": obs["tick"],
        "radius": radius,
        "next_radius": next_radius,
        "centres": centres,
        "contraction_start": contraction_start,
        "pulse_now": _active_tiles(obs, "PULSE", obs["tick"]),
        "pulse_next": _next_tiles(obs, "PULSE"),
        "seal_next": _next_tiles(obs, "SEAL"),
    }


def _action_move(uid, move):
    return {"unit_id": uid, "action": "MOVE", "direction": move[0], "steps": move[1]}


def _action_wait(uid):
    return {"unit_id": uid, "action": "WAIT"}


def _direction_order(name):
    for index, direction in enumerate(DIRS):
        if direction[0] == name:
            return index
    return 0


class _Base:
    def __init__(self, match_id, team_id):
        self.match_id = match_id
        self.team_id = team_id
        self.last_defend = {}
        self.home_relay = None
        self._seen_match = None

    def _sync(self, obs):
        if self._seen_match != obs["match_id"] or obs["tick"] == 1:
            self.last_defend = {}
            self.home_relay = None
            self._seen_match = obs["match_id"]
        if self.home_relay is None:
            relays = obs["relays"]
            units = [u for u in obs["team"]["units"] if u["hp"] > 0]
            if relays and units:
                self.home_relay = min(
                    relays,
                    key=lambda r: (
                        min(_manhattan((r["x"], r["y"]), _pt(u)) for u in units),
                        r["id"],
                    ),
                )["id"]

    def _can_defend(self, unit, tick):
        return self.last_defend.get(unit["id"]) != tick - 1


class ArenaCompetitor(_Base):
    """Competitor role: relay-income maximiser, kill-only combat."""

    def act(self, obs):
        self._sync(obs)
        st = _state(obs)
        self._tick = st["tick"]
        flux = st["flux"]
        actions, priority, planned, assigned = [], [], set(), set()
        units = list(st["units"])
        if not units:
            return [], []

        def defend_need(unit):
            damage = _incoming(_pt(unit), st["enemies"]) + _hazard(
                obs, _pt(unit), st["radius"], st["pulse_now"]
            )
            urgent = (
                damage >= 2
                and damage >= unit["hp"] - 2
                and self._can_defend(unit, st["tick"])
            )
            return (0 if urgent else 1, unit["kind"] != "STRIDER", unit["id"])

        for unit in sorted(units, key=defend_need):
            action = self._choose(obs, st, unit, planned, assigned)
            if action is None:
                actions.append(_action_wait(unit["id"]))
                priority.append(unit["id"])
                continue
            cost = COST[action["action"]]
            if cost > flux:
                actions.append(_action_wait(unit["id"]))
                priority.append(unit["id"])
                continue
            if action["action"] == "DEFEND":
                self.last_defend[unit["id"]] = st["tick"]
            if action["action"] == "MOVE":
                dx, dy = dict((n, (x, y)) for n, x, y in DIRS)[action["direction"]]
                planned.add((unit["x"] + dx * action["steps"], unit["y"] + dy * action["steps"]))
            if action["action"] == "CAPTURE":
                assigned.add(action["relay_id"])
            flux -= cost
            actions.append(action)
            priority.append(unit["id"])
        return actions, priority

    def _choose(self, obs, st, unit, planned, assigned):
        tick = st["tick"]
        pos = _pt(unit)
        damage = _incoming(pos, st["enemies"]) + _hazard(obs, pos, st["radius"], st["pulse_now"])
        if (
            damage >= 2
            and damage >= unit["hp"] - 2
            and self._can_defend(unit, tick)
            and st["flux"] >= 1
        ):
            return {"unit_id": unit["id"], "action": "DEFEND"}

        adjacent = [
            e
            for e in st["enemies"]
            if not e["immune"] and _manhattan(pos, _pt(e)) == 1
        ]
        if adjacent:
            kills = []
            for enemy in adjacent:
                allies = [u for u in st["units"] if _manhattan(_pt(u), _pt(enemy)) == 1]
                total = sum(DAMAGE[u["kind"]] for u in allies)
                if len(allies) >= 2:
                    total += 1
                if total >= enemy["hp"]:
                    kills.append(enemy)
            if kills:
                target = min(kills, key=lambda e: (e["hp"], e["id"]))
                return {"unit_id": unit["id"], "action": "ATTACK", "target_unit_id": target["id"]}

        relays = [r for r in st["relays"].values() if _manhattan(pos, (r["x"], r["y"])) == 1]
        foreign = [r for r in relays if r["owner_team_id"] != self.team_id and r["id"] not in assigned]
        if tick <= 127 and unit["kind"] == "STRIDER":
            for relay in sorted(
                foreign,
                key=lambda r: (
                    0 if r["owner_team_id"] is not None else 1,
                    -r["uncontested_owner_ticks"],
                    r["id"],
                ),
            ):
                guarded = any(
                    e["team_id"] is not None and _manhattan(_pt(e), (relay["x"], relay["y"])) == 1
                    for e in st["enemies"]
                )
                if not guarded and st["flux"] >= 1:
                    return {"unit_id": unit["id"], "action": "CAPTURE", "relay_id": relay["id"]}
        elif tick <= 127 and foreign and st["flux"] >= 1:
            relay = min(foreign, key=lambda r: r["id"])
            guarded = any(
                e["team_id"] is not None and _manhattan(_pt(e), (relay["x"], relay["y"])) == 1
                for e in st["enemies"]
            )
            if not guarded and relay["owner_team_id"] is not None:
                return {"unit_id": unit["id"], "action": "CAPTURE", "relay_id": relay["id"]}

        well = next(
            (w for w in st["wells"].values() if w["charged"] and _pt(w["position"]) == pos),
            None,
        )
        if well is not None and st["flux"] <= 9:
            return {"unit_id": unit["id"], "action": "HARVEST"}

        if foreign and tick > 127:
            foreign = []

        goals = self._goals(obs, st, unit, planned)
        if not goals:
            return None
        move = self._move(obs, st, unit, goals, planned)
        return _action_move(unit["id"], move) if move else None

    def _goals(self, obs, st, unit, planned):
        tick = st["tick"]
        pos = _pt(unit)
        if st["radius"] is not None and st["centres"]:
            radius = st["next_radius"] if st["contraction_start"] is not None else st["radius"]
            margin = 2
            safe = {
                (x, y)
                for y in range(BOARD)
                for x in range(BOARD)
                if _centre_distance((x, y), st["centres"]) <= max(0, radius - margin)
                and (x, y) not in st["blocked"]
            }
            if tick >= 156:
                safe = {
                    (x, y)
                    for y in range(BOARD)
                    for x in range(BOARD)
                    if _centre_distance((x, y), st["centres"]) <= 1
                }
            if safe:
                dist = _bfs(pos, st["blocked"])
                nearest = min((dist.get(p, INF) for p in safe), default=INF)
                if st["contraction_start"] is not None:
                    ticks_left = max(0, st["contraction_start"] - tick)
                    if ticks_left <= nearest + 4 or _centre_distance(pos, st["centres"]) > radius:
                        return safe
                if tick >= 146:
                    return safe

        if tick <= 8 and st["units"]:
            if unit["kind"] == "STRIDER" and self.home_relay in st["relays"]:
                relay = st["relays"][self.home_relay]
                return _stand_cells([(relay["x"], relay["y"])], st["blocked"])
            charged = [w for w in st["wells"].values() if w["charged"]]
            if charged:
                dist = _bfs(pos, st["blocked"])
                near = [w for w in charged if dist.get(_pt(w["position"]), INF) <= 6]
                if near:
                    return {_pt(w["position"]) for w in near}

        foreign = [
            r
            for r in st["relays"].values()
            if r["owner_team_id"] != self.team_id
            and not r["corrupted"]
        ]
        if foreign:
            def key(relay):
                guards = sum(
                    1
                    for e in st["enemies"]
                    if e["team_id"] is not None
                    and _manhattan(_pt(e), (relay["x"], relay["y"])) <= 3
                )
                return (guards, _manhattan(pos, (relay["x"], relay["y"])), relay["id"])

            relay = min(foreign, key=key)
            return _stand_cells([(relay["x"], relay["y"])], st["blocked"])

        charged = [w for w in st["wells"].values() if w["charged"]]
        if charged:
            return {_pt(w["position"]) for w in charged}
        return set()

    def _move(self, obs, st, unit, goals, planned):
        pos = _pt(unit)
        dist = _bfs_from(goals, st["blocked"])
        if not dist:
            return None
        occupied = st["occupied"] - {pos}
        best = None
        for move in _legal_moves(unit, st["blocked"], occupied, planned):
            name, steps, dest = move
            dest_distance = dist.get(dest, INF)
            if dest_distance >= INF:
                continue
            damage = _incoming(dest, st["enemies"]) + _hazard(
                obs, dest, st["radius"], st["pulse_now"]
            )
            if damage >= unit["hp"] and not self._can_defend(unit, st["tick"] + 1):
                continue
            adjacent = sum(1 for e in st["enemies"] if _manhattan(dest, _pt(e)) == 1)
            if adjacent and unit["hp"] <= (2 if unit["kind"] == "STRIDER" else 4):
                continue
            score = (
                dest_distance,
                _hazard(obs, dest, st["radius"], st["pulse_now"]) + (2 if dest in st["pulse_next"] else 0),
                adjacent,
                _direction_order(name),
                steps,
            )
            if best is None or score < best[0]:
                best = (score, move)
        return None if best is None else best[1]


class ArenaAttacker(_Base):
    """Attacker role: denial ladder, execute-only attacks, relay flipping."""

    def act(self, obs):
        self._sync(obs)
        st = _state(obs)
        self._tick = st["tick"]
        units = st["units"]
        if not units:
            return [], []
        tick = st["tick"]
        actions, priority, planned, assigned = [], [], set(), set()
        flux = st["flux"]

        pair = self._pair_kill(st, flux)
        if pair is not None:
            for unit, target in pair:
                actions.append(
                    {"unit_id": unit["id"], "action": "ATTACK", "target_unit_id": target["id"]}
                )
                priority.append(unit["id"])
            return actions, priority

        for unit in units:
            action = self._choose(obs, st, unit, planned, assigned)
            if action is None:
                actions.append(_action_wait(unit["id"]))
                priority.append(unit["id"])
                continue
            cost = COST[action["action"]]
            if cost > flux:
                actions.append(_action_wait(unit["id"]))
                priority.append(unit["id"])
                continue
            if action["action"] == "DEFEND":
                self.last_defend[unit["id"]] = tick
            if action["action"] == "MOVE":
                dx, dy = dict((n, (x, y)) for n, x, y in DIRS)[action["direction"]]
                planned.add((unit["x"] + dx * action["steps"], unit["y"] + dy * action["steps"]))
            if action["action"] == "CAPTURE":
                assigned.add(action["relay_id"])
            flux -= cost
            actions.append(action)
            priority.append(unit["id"])
        return actions, priority

    def _pair_kill(self, st, flux):
        if flux < 4 or len(st["units"]) < 2:
            return None
        for target in st["enemies"]:
            if target["hp"] > 4 or target["immune"] or target["team_id"] is None:
                continue
            attackers = [
                u
                for u in st["units"]
                if not u["immune"] and _manhattan(_pt(u), _pt(target)) == 1
            ]
            if len(attackers) == 2:
                total = sum(DAMAGE[u["kind"]] for u in attackers) + 1
                if total >= target["hp"]:
                    return [(u, target) for u in attackers]
        return None

    def _choose(self, obs, st, unit, planned, assigned):
        tick = st["tick"]
        pos = _pt(unit)
        damage = _incoming(pos, st["enemies"]) + _hazard(obs, pos, st["radius"], st["pulse_now"])
        legal_defend = self._can_defend(unit, tick)

        if tick < 156 and st["flux"] >= 2:
            executes = [
                e
                for e in st["enemies"]
                if _manhattan(pos, _pt(e)) == 1 and not e["immune"] and e["hp"] <= DAMAGE[unit["kind"]]
            ]
            if executes:
                target = min(executes, key=lambda e: (e["hp"], -DAMAGE[e["kind"]], e["id"]))
                return {"unit_id": unit["id"], "action": "ATTACK", "target_unit_id": target["id"]}

        if damage >= unit["hp"] - 1 and legal_defend and st["flux"] >= 1:
            return {"unit_id": unit["id"], "action": "DEFEND"}

        if damage >= unit["hp"] - 1:
            moves = _legal_moves(unit, st["blocked"], st["occupied"] - {pos}, planned)
            if moves:
                safe = [
                    m
                    for m in moves
                    if _threat(m[2], st["enemies"])
                    + _hazard(obs, m[2], st["radius"], st["pulse_now"])
                    < unit["hp"] - 1
                ]
                if not safe:
                    moves_sorted = sorted(
                        moves,
                        key=lambda m: (
                            _threat(m[2], st["enemies"]) + _hazard(obs, m[2], st["radius"], st["pulse_now"]),
                            _manhattan(m[2], pos),
                            m[0],
                            m[1],
                        ),
                    )
                    return _action_move(unit["id"], moves_sorted[0])

        if st["radius"] is not None and st["centres"]:
            target = st["radius"]
            if st["contraction_start"] is not None and st["contraction_start"] - tick <= 6:
                target = st["next_radius"]
            cd = _centre_distance(pos, st["centres"])
            outside = cd > st["radius"]
            tightening = (
                st["contraction_start"] is not None
                and st["contraction_start"] - tick <= 6
                and cd > max(0, target - 1)
            )
            if outside or tightening:
                moves = _legal_moves(unit, st["blocked"], st["occupied"] - {pos}, planned)
                for strict in (True, False):
                    candidates = []
                    for move in moves:
                        dest = move[2]
                        if dest in st["pulse_now"]:
                            continue
                        dest_cd = _centre_distance(dest, st["centres"])
                        if dest_cd > cd or (strict and dest_cd == cd):
                            continue
                        risk = _threat(dest, st["enemies"]) + _hazard(
                            obs, dest, st["radius"], st["pulse_now"]
                        )
                        candidates.append(((risk, dest_cd, _direction_order(move[0]), move[1]), move))
                    if candidates:
                        return _action_move(unit["id"], min(candidates)[1])

        if tick < 136 and st["flux"] >= 1 and not unit["immune"]:
            for relay in st["relays"].values():
                if (
                    _manhattan(pos, (relay["x"], relay["y"])) == 1
                    and relay["owner_team_id"] != self.team_id
                    and not relay["corrupted"]
                    and relay["id"] not in assigned
                ):
                    if relay["owner_team_id"] is None:
                        guarded = any(
                            e["team_id"] is not None
                            and _manhattan(_pt(e), (relay["x"], relay["y"])) <= 2
                            for e in st["enemies"]
                        )
                        if not guarded:
                            return {
                                "unit_id": unit["id"],
                                "action": "CAPTURE",
                                "relay_id": relay["id"],
                            }
                    else:
                        guarded = any(
                            e["team_id"] is not None
                            and _manhattan(_pt(e), (relay["x"], relay["y"])) <= 3
                            for e in st["enemies"]
                        )
                        if st["flux"] >= 3 or not guarded:
                            return {
                                "unit_id": unit["id"],
                                "action": "CAPTURE",
                                "relay_id": relay["id"],
                            }

        if tick < 136:
            contest = self._contest_goal(obs, st, unit, planned)
            if contest is not None:
                return _action_move(unit["id"], contest)

        well = next(
            (w for w in st["wells"].values() if w["charged"] and _pt(w["position"]) == pos),
            None,
        )
        if well is not None and st["flux"] <= 10:
            return {"unit_id": unit["id"], "action": "HARVEST"}

        goals = self._advance_goals(obs, st, unit)
        if goals:
            move = self._move(obs, st, unit, goals, planned)
            return _action_move(unit["id"], move) if move else None
        return None

    def _contest_goal(self, obs, st, unit, planned):
        pos = _pt(unit)
        reach = 3 if unit["kind"] == "STRIDER" else 2
        candidates = []
        for relay in st["relays"].values():
            if (
                relay["owner_team_id"] is None
                or relay["owner_team_id"] == self.team_id
                or relay["uncontested_owner_ticks"] < 1
                or relay["corrupted"]
            ):
                continue
            cell = (relay["x"], relay["y"])
            if any(
                e["team_id"] is not None and _manhattan(_pt(e), cell) == 1
                for e in st["enemies"]
            ):
                continue
            if _manhattan(pos, cell) > reach:
                continue
            stands = [
                p
                for p in _stand_cells([cell], st["blocked"])
                if p not in st["occupied"] - {pos} and p not in planned
                and _threat(p, st["enemies"]) <= 1
                and p not in st["pulse_now"]
            ]
            if not stands:
                continue
            wells = {_pt(w["position"]) for w in st["wells"].values() if w["charged"]}
            stand = min(stands, key=lambda p: (0 if p in wells else 1, _manhattan(pos, p), p))
            candidates.append((_manhattan(pos, cell), relay["id"], stand))
        if not candidates:
            return None
        _distance, _rid, stand = min(candidates)
        dist = _bfs_from({stand}, st["blocked"])
        best = None
        for move in _legal_moves(unit, st["blocked"], st["occupied"] - {pos}, planned):
            if move[2] not in dist:
                continue
            score = (dist[move[2]], _threat(move[2], st["enemies"]), move[0], move[1])
            if best is None or score < best[0]:
                best = (score, move)
        return None if best is None else best[1]

    def _advance_goals(self, obs, st, unit):
        pos = _pt(unit)
        foreign = [
            r for r in st["relays"].values() if r["owner_team_id"] != self.team_id
        ]
        if not foreign:
            foreign = [r for r in st["relays"].values() if r["uncontested_owner_ticks"] >= 1]
        if not foreign:
            return set()
        relay = min(
            foreign,
            key=lambda r: (_manhattan(pos, (r["x"], r["y"])), r["id"]),
        )
        return _stand_cells([(relay["x"], relay["y"])], st["blocked"])

    def _move(self, obs, st, unit, goals, planned):
        pos = _pt(unit)
        dist = _bfs_from(goals, st["blocked"])
        occupied = st["occupied"] - {pos}
        best = None
        for move in _legal_moves(unit, st["blocked"], occupied, planned):
            name, steps, dest = move
            if dest not in dist:
                continue
            cell_risk = _threat(dest, st["enemies"]) + _hazard(
                obs, dest, st["radius"], st["pulse_now"]
            )
            if dest in st["pulse_now"]:
                continue
            score = (
                cell_risk,
                -sum(1 for e in st["enemies"] if _manhattan(dest, _pt(e)) == 1),
                dist[dest],
                dest[0],
                dest[1],
            )
            if best is None or score < best[0]:
                best = (score, move)
        return None if best is None else best[1]


class ArenaDefender(_Base):
    """Defender role: survival-first, hold one relay, DEFEND lethal bursts."""

    def act(self, obs):
        self._sync(obs)
        st = _state(obs)
        self._tick = st["tick"]
        units = st["units"]
        if not units:
            return [], []
        tick = st["tick"]
        actions, priority, planned, assigned = [], [], set(), set()
        flux = st["flux"]
        floor = 3 if tick >= 146 else (2 if self._near_enemy(st) else 1)

        def urgency(unit):
            damage = self._incoming_total(obs, st, unit)
            lethal = unit["hp"] <= damage <= unit["hp"] + 1 and self._can_defend(unit, tick)
            return (0 if lethal else 1, unit["hp"], unit["id"])

        for unit in sorted(units, key=urgency):
            action = self._choose(obs, st, unit, planned, assigned, floor)
            if action is None:
                actions.append(_action_wait(unit["id"]))
                priority.append(unit["id"])
                continue
            cost = COST[action["action"]]
            reserve = floor if action["action"] not in ("DEFEND", "ATTACK") else 0
            if cost > flux - reserve and action["action"] != "DEFEND":
                actions.append(_action_wait(unit["id"]))
                priority.append(unit["id"])
                continue
            if cost > flux:
                actions.append(_action_wait(unit["id"]))
                priority.append(unit["id"])
                continue
            if action["action"] == "DEFEND":
                self.last_defend[unit["id"]] = tick
            if action["action"] == "MOVE":
                dx, dy = dict((n, (x, y)) for n, x, y in DIRS)[action["direction"]]
                planned.add((unit["x"] + dx * action["steps"], unit["y"] + dy * action["steps"]))
            if action["action"] == "CAPTURE":
                assigned.add(action["relay_id"])
            flux -= cost
            actions.append(action)
            priority.append(unit["id"])
        return actions, priority

    def _near_enemy(self, st):
        for unit in st["units"]:
            if any(_manhattan(_pt(unit), _pt(e)) <= 4 for e in st["enemies"]):
                return True
        return False

    def _incoming_total(self, obs, st, unit):
        return _incoming(_pt(unit), st["enemies"]) + _hazard(
            obs, _pt(unit), st["radius"], st["pulse_now"]
        )

    def _choose(self, obs, st, unit, planned, assigned, floor):
        tick = st["tick"]
        pos = _pt(unit)
        damage = self._incoming_total(obs, st, unit)
        legal_defend = self._can_defend(unit, tick)
        attrition = 3 if unit["kind"] == "STRIDER" else 5

        if tick >= 156 and damage >= 1 and legal_defend and st["flux"] >= 1:
            return {"unit_id": unit["id"], "action": "DEFEND"}
        if legal_defend and st["flux"] >= 1:
            if unit["hp"] <= damage <= unit["hp"] + 1:
                return {"unit_id": unit["id"], "action": "DEFEND"}
            if damage >= attrition and damage < unit["hp"] + 2:
                return {"unit_id": unit["id"], "action": "DEFEND"}

        kill = self._guaranteed_kill(obs, st, unit)
        if kill is not None and st["flux"] >= 2 + floor:
            return {"unit_id": unit["id"], "action": "ATTACK", "target_unit_id": kill["id"]}

        if not unit["immune"] and st["flux"] >= 1 + floor:
            for relay in st["relays"].values():
                if (
                    _manhattan(pos, (relay["x"], relay["y"])) == 1
                    and relay["owner_team_id"] != self.team_id
                    and relay["id"] not in assigned
                ):
                    home = relay["id"] == self.home_relay
                    if home or st["flux"] >= 1 + floor:
                        return {"unit_id": unit["id"], "action": "CAPTURE", "relay_id": relay["id"]}

        well = next(
            (w for w in st["wells"].values() if w["charged"] and _pt(w["position"]) == pos),
            None,
        )
        if (
            well is not None
            and st["flux"] <= 11
            and damage == 0
            and not any(_manhattan(pos, _pt(e)) <= 5 for e in st["enemies"])
        ):
            return {"unit_id": unit["id"], "action": "HARVEST"}

        goals = self._goals(obs, st, unit)
        if goals:
            move = self._move(obs, st, unit, goals, planned)
            if move:
                return _action_move(unit["id"], move)
        return None

    def _guaranteed_kill(self, obs, st, unit):
        pos = _pt(unit)
        adjacent = [
            e
            for e in st["enemies"]
            if _manhattan(pos, _pt(e)) == 1 and not e["immune"]
        ]
        if not adjacent:
            return None
        allies = [u for u in st["units"] if u["id"] != unit["id"]]
        ordered = sorted(
            adjacent,
            key=lambda e: (
                0 if any(_manhattan(_pt(u), _pt(e)) == 1 for u in allies) else 1,
                e["hp"],
                e["id"],
            ),
        )
        for target in ordered:
            total = DAMAGE[unit["kind"]] + sum(
                DAMAGE[u["kind"]] for u in allies if _manhattan(_pt(u), _pt(target)) == 1
            )
            if total < target["hp"]:
                continue
            retaliation = sum(
                DAMAGE[e["kind"]]
                for e in st["enemies"]
                if e["id"] != target["id"] and _manhattan(pos, _pt(e)) == 1
            )
            remain = unit["hp"] - retaliation
            if remain >= (3 if unit["kind"] == "STRIDER" else 4):
                return target
        return None

    def _goals(self, obs, st, unit):
        pos = _pt(unit)
        tick = st["tick"]
        if st["radius"] is not None and st["centres"]:
            target_radius = st["radius"]
            if st["contraction_start"] is not None:
                target_radius = st["next_radius"]
                ticks_left = st["contraction_start"] - tick
                if ticks_left <= 10:
                    safe = {
                        (x, y)
                        for y in range(BOARD)
                        for x in range(BOARD)
                        if _centre_distance((x, y), st["centres"]) <= max(0, target_radius - 1)
                        and (x, y) not in st["blocked"]
                    }
                    if safe:
                        return safe
            if tick >= 146:
                safe = {
                    (x, y)
                    for y in range(BOARD)
                    for x in range(BOARD)
                    if _centre_distance((x, y), st["centres"]) <= max(0, st["radius"] - 1)
                }
                if safe:
                    return safe

        if self.home_relay in st["relays"]:
            relay = st["relays"][self.home_relay]
            stands = _stand_cells([(relay["x"], relay["y"])], st["blocked"])
            if st["radius"] is None:
                return stands
            inside = {
                p
                for p in stands
                if _centre_distance(p, st["centres"]) <= max(0, st["radius"] - 1)
            }
            if inside:
                return inside

        charged = [w for w in st["wells"].values() if w["charged"]]
        if charged:
            return {_pt(w["position"]) for w in charged}
        if st["centres"]:
            return {
                (x, y)
                for y in range(BOARD)
                for x in range(BOARD)
                if _centre_distance((x, y), st["centres"]) <= 2
            }
        return set()

    def _move(self, obs, st, unit, goals, planned):
        pos = _pt(unit)
        dist = _bfs_from(goals, st["blocked"])
        occupied = st["occupied"] - {pos}
        best = None
        for move in _legal_moves(unit, st["blocked"], occupied, planned):
            name, steps, dest = move
            if dest not in dist:
                continue
            goal_distance = dist[dest]
            risk = 0
            if st["radius"] is not None and _centre_distance(dest, st["centres"]) > st["radius"]:
                risk += 1000
            if any(
                e["team_id"] is not None
                and not e["immune"]
                and _manhattan(dest, _pt(e)) == 1
                for e in st["enemies"]
            ):
                risk += 100
            if any(
                e["kind"] == "HUNTER"
                and not e["immune"]
                and _manhattan(dest, _pt(e)) == 1
                for e in st["enemies"]
            ):
                risk += 20
            if dest in st["pulse_now"] or dest in st["pulse_next"] or dest in st["seal_next"]:
                risk += 10
            score = (risk, goal_distance, name, steps)
            if best is None or score < best[0]:
                best = (score, move)
        return None if best is None else best[1]


class ArenaJudge(_Base):
    """Judge role: rubric-weighted scoring over all legal actions and pairs."""

    def act(self, obs):
        self._sync(obs)
        st = _state(obs)
        self._tick = st["tick"]
        units = st["units"]
        if not units:
            return [], []
        tick = st["tick"]
        keep = 2 if self._near_enemy(st) or any(u["hp"] <= 3 for u in units) else 1
        candidates = {unit["id"]: self._candidates(obs, st, unit) for unit in units}
        for uid, options in candidates.items():
            filtered = [c for c in options if not c["fatal"]]
            if not filtered:
                filtered = sorted(options, key=lambda c: (c["hp_after"], c["score"]), reverse=True)
            candidates[uid] = filtered

        for unit in units:
            pos = _pt(unit)
            danger = _threat(pos, st["enemies"]) + _hazard(
                obs, pos, st["radius"], st["pulse_now"]
            )
            if unit["hp"] > 4 or danger < 2:
                continue
            escapes = []
            for candidate in candidates[unit["id"]]:
                if candidate["action"]["action"] != "MOVE":
                    continue
                risk = _threat(candidate["dest"], st["enemies"]) + _hazard(
                    obs, candidate["dest"], st["radius"], st["pulse_now"]
                )
                if candidate["dest"] in st["pulse_next"]:
                    risk += 2
                if risk < danger:
                    escapes.append((risk, candidate))
            if escapes:
                escapes.sort(key=lambda item: (item[0], -item[1]["score"]))
                candidates[unit["id"]] = [candidate for _risk, candidate in escapes[:3]]

        if len(units) == 1:
            unit = units[0]
            pick = max(candidates[unit["id"]], key=lambda c: c["score"])
            if pick["action"]["action"] == "DEFEND":
                self.last_defend[unit["id"]] = tick
            return [pick["action"]], [unit["id"]]

        first, second = units[0], units[1]
        best = None
        for a in candidates[first["id"]]:
            for b in candidates[second["id"]]:
                if a["cost"] + b["cost"] > st["flux"] - keep:
                    continue
                if a["dest"] == b["dest"]:
                    continue
                if a["dest"] == _pt(second) or b["dest"] == _pt(first):
                    continue
                if a["relay"] is not None and a["relay"] == b["relay"]:
                    continue
                focus = 8.0 if (
                    a["target"] is not None and a["target"] == b["target"]
                ) else 0.0
                total = a["score"] + b["score"] + focus
                damage = a["damage"] + b["damage"]
                min_hp = min(a["hp_after"], b["hp_after"])
                relays = int(a["action"]["action"] == "CAPTURE") + int(
                    b["action"]["action"] == "CAPTURE"
                )
                spent = a["cost"] + b["cost"]
                key = (total, damage, min_hp, relays, -spent)
                if best is None or key > best[0]:
                    best = (key, a, b)
        if best is None:
            return [], []
        _key, a, b = best
        for candidate in (a, b):
            if candidate["action"]["action"] == "DEFEND":
                self.last_defend[candidate["action"]["unit_id"]] = tick
        ordered = sorted((a, b), key=lambda c: (-c["score"], c["action"]["unit_id"]))
        return [c["action"] for c in ordered], [c["action"]["unit_id"] for c in ordered]

    def _near_enemy(self, st):
        return any(
            _manhattan(_pt(u), _pt(e)) <= 4 for u in st["units"] for e in st["enemies"]
        )

    def _objective_goals(self, obs, st, unit):
        pos = _pt(unit)
        tick = st["tick"]
        if st["radius"] is not None and st["centres"]:
            outside = _centre_distance(pos, st["centres"]) > st["radius"]
            urgent = (
                st["contraction_start"] is not None
                and st["contraction_start"] - tick <= 3
            )
            if outside or urgent:
                safe = {
                    (x, y)
                    for y in range(BOARD)
                    for x in range(BOARD)
                    if _centre_distance((x, y), st["centres"])
                    <= max(0, st["next_radius"] - 1)
                    and (x, y) not in st["blocked"]
                }
                if safe:
                    return safe
        owned_threatened = [
            r
            for r in st["relays"].values()
            if r["owner_team_id"] == self.team_id
            and any(
                e["team_id"] is not None
                and _manhattan(_pt(e), (r["x"], r["y"])) == 1
                for e in st["enemies"]
            )
        ]
        if owned_threatened:
            return _stand_cells(
                [(r["x"], r["y"]) for r in owned_threatened], st["blocked"]
            )
        foreign = [r for r in st["relays"].values() if r["owner_team_id"] != self.team_id]
        if foreign:
            relay = min(
                foreign,
                key=lambda r: (_manhattan(pos, (r["x"], r["y"])), r["id"]),
            )
            return _stand_cells([(relay["x"], relay["y"])], st["blocked"])
        if st["flux"] <= 5:
            charged = [w for w in st["wells"].values() if w["charged"]]
            if charged:
                return {_pt(w["position"]) for w in charged}
        if st["centres"]:
            return set(st["centres"])
        return set()

    def _candidates(self, obs, st, unit):
        tick = st["tick"]
        pos = _pt(unit)
        immune = unit["immune"]
        flux = st["flux"]
        goals = self._objective_goals(obs, st, unit)
        goal_dist = _bfs_from(goals, st["blocked"]) if goals else {}
        start_g = goal_dist.get(pos, INF)
        threat_start = _threat(pos, st["enemies"])
        pulse_now = st["pulse_now"]
        pulse_next = st["pulse_next"]
        radius = st["radius"]
        options = []

        def add(action, des, target=None, relay=None, damage=0.0):
            name = action["action"]
            if name != "MOVE":
                des = pos
            shield = 2 if (
                name == "DEFEND" and self._can_defend(unit, tick)
            ) else 0
            dest_hazard = _hazard(obs, des, radius, pulse_now)
            dnet = max(0, threat_start + dest_hazard - shield)
            hp_after = unit["hp"] - dnet
            fatal = dnet >= unit["hp"]
            if (
                name == "ATTACK"
                and target is not None
                and target["hp"] <= DAMAGE[unit["kind"]]
                and DAMAGE[target["kind"]] >= unit["hp"]
            ):
                fatal = False
            k = 10.0 if immune else _clamp(10 - 2.5 * dnet)
            if name == "CAPTURE":
                adjacent_enemy = any(
                    e["team_id"] is not None
                    and _manhattan(_pt(e), des) == 1
                    for e in st["enemies"]
                )
                p = 10.0 if (relay is not None and relay["owner_team_id"] != self.team_id and not adjacent_enemy) else 8.0
            elif name == "ATTACK":
                touches_relay = any(
                    _manhattan(_pt(target), (r["x"], r["y"])) == 1
                    for r in st["relays"].values()
                ) if target is not None else False
                p = 8.0 if touches_relay else 4.0
            elif name == "HARVEST":
                p = 5.0
            elif name == "DEFEND":
                p = 3.0
            elif name in ("MOVE", "WAIT"):
                if start_g >= INF or des not in goal_dist:
                    p = 0.0
                else:
                    p = _clamp(3.0 * (start_g - goal_dist[des]))
            else:
                p = 0.0
            if name == "ATTACK" and target is not None:
                v = 3.0 * DAMAGE[unit["kind"]]
                if target["hp"] <= DAMAGE[unit["kind"]]:
                    v += 4.0
                v = min(10.0, v)
            elif name == "CAPTURE":
                v = 4.0 + (2.0 if relay is not None and relay["owner_team_id"] is not None else 0.0)
            elif name == "HARVEST":
                v = 2.0 * min(3, 14 - flux)
            elif name == "DEFEND":
                v = 3.0 * min(2, threat_start + _hazard(obs, pos, radius, pulse_now))
            elif name == "MOVE":
                v = 2.0 if des in goals else 0.0
                if any(
                    w["charged"] and _pt(w["position"]) == des for w in st["wells"].values()
                ) and flux <= 11:
                    v += 1.0
            else:
                v = 0.0
            r = 5.0
            if radius is not None:
                if _centre_distance(des, st["centres"]) <= radius and des not in pulse_now and des not in pulse_next:
                    r += 3.0
                if des in pulse_now:
                    r -= 6.0
                if des in pulse_next:
                    r -= 4.0
                if _centre_distance(des, st["centres"]) > radius:
                    r -= 6.0
            adjacent_count = sum(
                1 for e in st["enemies"] if not e["immune"] and _manhattan(des, _pt(e)) == 1
            )
            if adjacent_count:
                r -= 3.0 * min(2, adjacent_count)
            if name == "ATTACK" and target is not None and target["hp"] <= DAMAGE[unit["kind"]]:
                r += 3.0
            if name == "DEFEND" and threat_start:
                r += 4.0
            r = _clamp(r)
            s = {"WAIT": 10, "HARVEST": 10, "DEFEND": 9, "CAPTURE": 9, "ATTACK": 8}.get(name)
            if s is None:
                s = 7 if action.get("steps", 0) == 1 else 5
            score = 3.0 * k + 2.5 * p + 1.5 * v + 2.0 * r + s
            options.append(
                {
                    "action": action,
                    "score": score,
                    "cost": COST[name],
                    "dest": des,
                    "target": None if target is None else target["id"],
                    "relay": None if relay is None else relay["id"],
                    "damage": DAMAGE[unit["kind"]] if name == "ATTACK" else 0,
                    "hp_after": hp_after,
                    "fatal": fatal,
                }
            )

        add(_action_wait(unit["id"]), pos)
        if self._can_defend(unit, tick) and flux >= 1:
            add({"unit_id": unit["id"], "action": "DEFEND"}, pos)
        well = next(
            (w for w in st["wells"].values() if w["charged"] and _pt(w["position"]) == pos),
            None,
        )
        if well is not None and flux <= 11:
            add({"unit_id": unit["id"], "action": "HARVEST"}, pos)
        if not immune and flux >= 1:
            for relay in st["relays"].values():
                if (
                    _manhattan(pos, (relay["x"], relay["y"])) == 1
                    and relay["owner_team_id"] != self.team_id
                    and not relay["corrupted"]
                ):
                    add(
                        {"unit_id": unit["id"], "action": "CAPTURE", "relay_id": relay["id"]},
                        pos,
                        relay=relay,
                    )
        if not immune and flux >= 2:
            for enemy in st["enemies"]:
                if not enemy["immune"] and _manhattan(pos, _pt(enemy)) == 1:
                    add(
                        {"unit_id": unit["id"], "action": "ATTACK", "target_unit_id": enemy["id"]},
                        pos,
                        target=enemy,
                    )
        occupied = st["occupied"] - {pos}
        for move in _legal_moves(unit, st["blocked"], occupied, set()):
            add(_action_move(unit["id"], move), move[2])
        return options


ARENA_BOTS = {
    "arena_competitor": ArenaCompetitor,
    "arena_attacker": ArenaAttacker,
    "arena_defender": ArenaDefender,
    "arena_judge": ArenaJudge,
}
ARENA_NAMES = tuple(ARENA_BOTS)


def make_arena_bot(kind, match_id, team_id):
    return ARENA_BOTS[kind](match_id, team_id)
