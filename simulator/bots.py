"""Opponent bots for the approximate simulator.

Every bot consumes an observation dict shaped exactly like the official SDK
wire schema and returns ``(actions, priority)`` wire lists. Simple bots are
hand-written policies; ``starter`` and ``agent`` load the real single-file
programs in isolated module instances so their globals do not collide when
several copies play in one process.
"""
from __future__ import annotations

import importlib.util
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _find_sdk_dir() -> Path:
    """Locate a reference SDK package, falling back to the local SDK copy."""
    candidates = []
    override = os.environ.get("PARTICIPANT_KIT_DIR")
    if override:
        candidates.append(Path(override))
    candidates.append(ROOT / "participant_kit")
    candidates.extend(sorted(ROOT.glob("*Participant_Kit")))
    for kit in candidates:
        sdk = kit / "sdk" / "python3.12"
        if sdk.is_dir():
            return sdk
    return ROOT / "agent"


SDK_DIR = _find_sdk_dir()
DEFAULT_AGENT = ROOT / "agent" / "agent.py"
DEFAULT_STARTER = SDK_DIR / "starter_agent.py"

if str(SDK_DIR) not in sys.path:
    sys.path.insert(0, str(SDK_DIR))

try:
    import agent_sdk  # noqa: E402  (official SDK path injected above)
except Exception:  # pragma: no cover - fallback for unusual layouts
    agent_sdk = None

BOARD = 20
DIRS = ((0, -1, "N"), (1, 0, "E"), (0, 1, "S"), (-1, 0, "W"))
COST = {"MOVE": 0, "ATTACK": 2, "DEFEND": 1, "HARVEST": 0, "CAPTURE": 1, "SCAN": 1, "WAIT": 0}


def _stable_seed(text):
    value = 0
    for ch in text:
        value = (value * 1000003 + ord(ch)) & 0xFFFFFFFFFFFF
    return value


def _unit_point(u):
    return u["x"], u["y"]


def _manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _terrain_blocked(obs):
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


def _unit_cells(obs, exclude_id=None):
    cells = set()
    for u in obs["team"]["units"]:
        if u["hp"] > 0 and u["id"] != exclude_id:
            cells.add(_unit_point(u))
    for u in obs["visible_enemies"]:
        if u["hp"] > 0:
            cells.add(_unit_point(u))
    return cells


def _danger_tiles(obs):
    """Tiles that will deal PULSE damage this tick or the next one."""
    danger = set()
    tick = obs["tick"]
    for warning in obs["warden_telegraphs"]:
        if warning["kind"] == "PULSE" and warning["starts_at_tick"] <= tick + 1 and tick <= warning["ends_after_tick"]:
            danger.update((t["x"], t["y"]) for t in warning["tiles"])
    return danger


def _safe_radius(obs):
    phase = obs.get("phase_four")
    if not phase:
        return None, None
    radius = phase["safe_radius"]
    step = phase.get("next_contraction")
    if step and obs["tick"] >= step["warning_at_tick"]:
        radius = min(radius, step["safe_radius"])
    centers = [(c["x"], c["y"]) for c in phase["safe_zone_center_tiles"]]
    return radius, centers


def _center_distance(point, centers):
    return min(_manhattan(point, c) for c in centers)


def _free_adjacent(targets, blocked, occupied, include_self=None):
    goals = set()
    for tx, ty in targets:
        for dx, dy, _name in DIRS:
            p = (tx + dx, ty + dy)
            if not (0 <= p[0] < BOARD and 0 <= p[1] < BOARD):
                continue
            if p in blocked or (p in occupied and p != include_self):
                continue
            goals.add(p)
    return goals


def _best_move(unit, goals, blocked, occupied, max_steps=1, avoid=None):
    """Greedy straight-line move toward the nearest goal tile."""
    avoid = avoid or set()
    start = _unit_point(unit)
    if not goals:
        return None
    current = min(_manhattan(start, g) for g in goals) if start not in goals else 0
    best = None
    for dx, dy, name in DIRS:
        for steps in range(1, max_steps + 1):
            path = [(start[0] + dx * k, start[1] + dy * k) for k in range(1, steps + 1)]
            if any(not (0 <= p[0] < BOARD and 0 <= p[1] < BOARD) or p in blocked or p in occupied for p in path):
                break
            dest = path[-1]
            if dest in avoid:
                continue
            score = min(_manhattan(dest, g) for g in goals)
            key = (score - current, -steps)
            if score < current and (best is None or key < best[0]):
                best = (key, name, steps)
    return None if best is None else (best[1], best[2])


def _adjacent_units(obs, unit, hostile=True):
    result = []
    for other in obs["visible_enemies"] if hostile else obs["team"]["units"]:
        if other["id"] == unit["id"] or other["hp"] <= 0 or other["immune"]:
            continue
        if _manhattan(_unit_point(unit), _unit_point(other)) == 1:
            result.append(other)
    return result


def _adjacent_relays(obs, unit):
    return [r for r in obs["relays"] if _manhattan(_unit_point(unit), (r["x"], r["y"])) == 1]


def _charged_well_here(obs, unit):
    for well in obs["wells"]:
        if well["charged"] and well["position"] == {"x": unit["x"], "y": unit["y"]}:
            return well
    return None


def _alive_own(obs):
    return [u for u in obs["team"]["units"] if u["hp"] > 0]


def _two_step(unit):
    return 2 if unit["kind"] == "STRIDER" else 1


class PassiveBot:
    """Never takes an action."""

    def __init__(self, match_id, team_id):
        self.rng = random.Random(_stable_seed(f"{match_id}:{team_id}:passive"))

    def act(self, obs):
        return [], []


class RandomBot:
    """Uniform-ish random legal policy; a noise floor for benchmarking."""

    def __init__(self, match_id, team_id):
        self.rng = random.Random(_stable_seed(f"{match_id}:{team_id}:random"))
        self.last_defend = {}

    def act(self, obs):
        tick = obs["tick"]
        blocked = _terrain_blocked(obs)
        flux = obs["team"]["flux"]
        actions, priority = [], []
        units = _alive_own(obs)
        self.rng.shuffle(units)
        for unit in units:
            occupied = _unit_cells(obs, exclude_id=unit["id"])
            choices = []
            for dx, dy, name in DIRS:
                for steps in range(1, _two_step(unit) + 1):
                    path = [(unit["x"] + dx * k, unit["y"] + dy * k) for k in range(1, steps + 1)]
                    if any(not (0 <= p[0] < BOARD and 0 <= p[1] < BOARD) or p in blocked or p in occupied for p in path):
                        break
                    choices.append(("MOVE", name, steps, 4))
            enemies = _adjacent_units(obs, unit)
            if enemies and not unit["immune"] and flux >= 2:
                choices.append(("ATTACK", min(enemies, key=lambda e: (e["hp"], e["id"])), None, 5))
            elif not unit["immune"] and flux >= 2 and _adjacent_units(obs, unit, hostile=False) == []:
                # Defend occasionally as a legal sink; random policy only.
                if self.last_defend.get(unit["id"]) != tick - 1 and flux >= 1:
                    choices.append(("DEFEND", None, None, 1))
            relays = [r for r in _adjacent_relays(obs, unit) if r["owner_team_id"] != obs["team"]["id"]]
            if relays and not unit["immune"] and flux >= 1:
                choices.append(("CAPTURE", self.rng.choice(relays), None, 4))
            if _charged_well_here(obs, unit) and flux < 14:
                choices.append(("HARVEST", None, None, 2))
            if not choices:
                continue
            total = sum(c[3] for c in choices)
            pick = self.rng.uniform(0, total)
            for kind, target, steps, weight in choices:
                pick -= weight
                if pick <= 0:
                    break
            cost = COST[kind]
            if cost > flux:
                continue
            if kind == "MOVE":
                action = {"unit_id": unit["id"], "action": "MOVE", "direction": target, "steps": steps}
            elif kind == "ATTACK":
                action = {"unit_id": unit["id"], "action": "ATTACK", "target_unit_id": target["id"]}
            elif kind == "CAPTURE":
                action = {"unit_id": unit["id"], "action": "CAPTURE", "relay_id": target["id"]}
            else:
                action = {"unit_id": unit["id"], "action": kind}
            actions.append(action)
            priority.append(unit["id"])
            flux -= cost
            if kind == "DEFEND":
                self.last_defend[unit["id"]] = tick
        return actions, priority


class AggressiveBot:
    """Hunts visible enemies; attacks whenever adjacent."""

    def __init__(self, match_id, team_id):
        self.rng = random.Random(_stable_seed(f"{match_id}:{team_id}:aggressive"))

    def act(self, obs):
        blocked = _terrain_blocked(obs)
        danger = _danger_tiles(obs)
        flux = obs["team"]["flux"]
        actions, priority = [], []
        for unit in _alive_own(obs):
            occupied = _unit_cells(obs, exclude_id=unit["id"])
            if unit["immune"]:
                continue
            enemies = _adjacent_units(obs, unit)
            if enemies and flux >= 2:
                target = min(enemies, key=lambda e: (e["hp"], e["id"]))
                actions.append({"unit_id": unit["id"], "action": "ATTACK", "target_unit_id": target["id"]})
                priority.append(unit["id"])
                flux -= 2
                continue
            if _charged_well_here(obs, unit) and flux <= 6:
                actions.append({"unit_id": unit["id"], "action": "HARVEST"})
                priority.append(unit["id"])
                continue
            visible = [e for e in obs["visible_enemies"] if e["hp"] > 0]
            if visible:
                targets = [_unit_point(e) for e in visible]
                goals = _free_adjacent(targets, blocked, occupied, include_self=_unit_point(unit))
                move = _best_move(unit, goals, blocked, occupied, _two_step(unit), avoid=danger)
                if move:
                    actions.append({"unit_id": unit["id"], "action": "MOVE", "direction": move[0], "steps": move[1]})
                    priority.append(unit["id"])
                    continue
            actions.append({"unit_id": unit["id"], "action": "WAIT"})
            priority.append(unit["id"])
        return actions, priority


class RelayBot:
    """Economy-first: grab and hold relays, harvest to fund captures."""

    def __init__(self, match_id, team_id):
        self.rng = random.Random(_stable_seed(f"{match_id}:{team_id}:relay"))

    def act(self, obs):
        blocked = _terrain_blocked(obs)
        danger = _danger_tiles(obs)
        flux = obs["team"]["flux"]
        actions, priority = [], []
        for unit in _alive_own(obs):
            occupied = _unit_cells(obs, exclude_id=unit["id"])
            relays = _adjacent_relays(obs, unit)
            claimable = [r for r in relays if r["owner_team_id"] != obs["team"]["id"]]
            if claimable and not unit["immune"] and flux >= 1:
                relay = min(claimable, key=lambda r: (r["corrupted"], r["id"]))
                actions.append({"unit_id": unit["id"], "action": "CAPTURE", "relay_id": relay["id"]})
                priority.append(unit["id"])
                flux -= 1
                continue
            enemies = _adjacent_units(obs, unit)
            if enemies and not unit["immune"] and flux >= 2 and unit["hp"] > 3:
                target = min(enemies, key=lambda e: (e["hp"], e["id"]))
                actions.append({"unit_id": unit["id"], "action": "ATTACK", "target_unit_id": target["id"]})
                priority.append(unit["id"])
                flux -= 2
                continue
            if _charged_well_here(obs, unit) and flux <= 8:
                actions.append({"unit_id": unit["id"], "action": "HARVEST"})
                priority.append(unit["id"])
                continue
            wanted = [r for r in obs["relays"] if r["owner_team_id"] != obs["team"]["id"]]
            if not wanted:
                wanted = obs["relays"]
            targets = [(r["x"], r["y"]) for r in wanted]
            goals = _free_adjacent(targets, blocked, occupied, include_self=_unit_point(unit))
            move = _best_move(unit, goals, blocked, occupied, _two_step(unit), avoid=danger)
            if move:
                actions.append({"unit_id": unit["id"], "action": "MOVE", "direction": move[0], "steps": move[1]})
                priority.append(unit["id"])
                continue
            actions.append({"unit_id": unit["id"], "action": "WAIT"})
            priority.append(unit["id"])
        return actions, priority


class TurtleBot:
    """Survival-first: retreats to the safe zone and defends."""

    def __init__(self, match_id, team_id):
        self.rng = random.Random(_stable_seed(f"{match_id}:{team_id}:turtle"))
        self.last_defend = {}

    def act(self, obs):
        blocked = _terrain_blocked(obs)
        danger = _danger_tiles(obs)
        flux = obs["team"]["flux"]
        tick = obs["tick"]
        radius, centers = _safe_radius(obs)
        actions, priority = [], []
        for unit in _alive_own(obs):
            occupied = _unit_cells(obs, exclude_id=unit["id"])
            here = _unit_point(unit)
            if here in danger and flux >= 1 and not unit["immune"] and self.last_defend.get(unit["id"]) != tick - 1:
                # A one-point DEFEND blunts the 2-damage pulse if escape fails.
                actions.append({"unit_id": unit["id"], "action": "DEFEND"})
                priority.append(unit["id"])
                flux -= 1
                self.last_defend[unit["id"]] = tick
                continue
            enemies = _adjacent_units(obs, unit)
            if enemies and not unit["immune"] and flux >= 2 and (unit["hp"] > 4 or len(enemies) == 1):
                target = min(enemies, key=lambda e: (e["hp"], e["id"]))
                actions.append({"unit_id": unit["id"], "action": "ATTACK", "target_unit_id": target["id"]})
                priority.append(unit["id"])
                flux -= 2
                continue
            if radius is not None:
                goals = {(x, y) for y in range(BOARD) for x in range(BOARD)
                         if _center_distance((x, y), centers) <= radius and (x, y) not in blocked and (x, y) not in occupied}
                if _center_distance(here, centers) > radius:
                    move = _best_move(unit, goals, blocked, occupied, _two_step(unit), avoid=danger)
                    if move:
                        actions.append({"unit_id": unit["id"], "action": "MOVE", "direction": move[0], "steps": move[1]})
                        priority.append(unit["id"])
                        continue
            if _charged_well_here(obs, unit) and flux < 10:
                actions.append({"unit_id": unit["id"], "action": "HARVEST"})
                priority.append(unit["id"])
                continue
            centers_set = set(centers or [(9, 9), (10, 9), (9, 10), (10, 10)])
            if here not in centers_set and _center_distance(here, centers_set) > 0:
                move = _best_move(unit, centers_set, blocked, occupied, _two_step(unit), avoid=danger)
                if move:
                    actions.append({"unit_id": unit["id"], "action": "MOVE", "direction": move[0], "steps": move[1]})
                    priority.append(unit["id"])
                    continue
            actions.append({"unit_id": unit["id"], "action": "WAIT"})
            priority.append(unit["id"])
        return actions, priority


class SdkBot:
    """Runs a real single-file SDK program (starter or tournament agent)."""

    _counter = 0

    def __init__(self, path, label):
        SdkBot._counter += 1
        name = f"sim_{label}_{SdkBot._counter}"
        spec = importlib.util.spec_from_file_location(name, str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.module = module
        if agent_sdk is None:
            raise RuntimeError("official agent_sdk is unavailable")

    def act(self, obs):
        try:
            bundle = self.module.decide(agent_sdk.Observation.from_wire(obs))
            wire = bundle.to_wire()
            return wire["actions"], wire["priority"]
        except Exception:
            if os.environ.get("ARENA_STRICT"):
                raise
            return [], []


SIMPLE = {
    "passive": PassiveBot,
    "random": RandomBot,
    "aggressive": AggressiveBot,
    "relay": RelayBot,
    "turtle": TurtleBot,
}

BOT_NAMES = tuple(SIMPLE) + ("starter", "agent")


def make_bot(kind, match_id, team_id, agent_path=None):
    if kind == "starter":
        return SdkBot(DEFAULT_STARTER, "starter")
    if kind == "agent":
        return SdkBot(agent_path or DEFAULT_AGENT, "agent")
    if kind in SIMPLE:
        return SIMPLE[kind](match_id, team_id)
    raise ValueError(f"unknown bot kind: {kind}")
