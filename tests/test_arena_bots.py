"""Contract tests for the four arena-role bots.

Each role is implemented from the MIT-licensed arena skill's policy specs
(see ``third_party/arena-skill``). These tests pin the wire contract and the
safe-zone override that fixed the attacker's late-game deaths.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.arena_bots import ARENA_BOTS, ARENA_NAMES, make_arena_bot  # noqa: E402
from simulator.engine import Game, radius_at, center_distance  # noqa: E402

ALLOWED = {"MOVE", "ATTACK", "DEFEND", "HARVEST", "CAPTURE", "SCAN", "WAIT"}
DIRECTIONS = {"N", "E", "S", "W"}
COST = {"MOVE": 0, "ATTACK": 2, "DEFEND": 1, "HARVEST": 0, "CAPTURE": 1, "SCAN": 1, "WAIT": 0}


def _run_match(seed, map_name, roles):
    match_id = f"test-arena-{seed}-{map_name}"
    game = Game(seed=seed, map_name=map_name, match_id=match_id)
    bots = {slot: make_arena_bot(roles[slot], match_id, slot) for slot in range(4)}
    done = False
    while not done and game.tick <= 160:
        game.begin_tick()
        team_actions = {}
        for slot in range(4):
            if game.teams[slot].eliminated:
                continue
            observation = game.observe(slot)
            actions, priority = bots[slot].act(observation)
            _check_wire(game, slot, observation, actions, priority)
            team_actions[slot] = (actions, priority)
        done = game.resolve(team_actions)
    return game


def _check_wire(game, slot, observation, actions, priority):
    assert len(actions) <= 2, f"slot {slot}: {len(actions)} actions"
    assert len(priority) <= 2, f"slot {slot}: {len(priority)} priority entries"
    living = {u.id: u for u in game.teams[slot].units if u.alive}
    seen = set()
    for action in actions:
        uid = action.get("unit_id")
        assert uid in living, f"slot {slot}: action for non-living unit {uid}"
        assert uid not in seen, f"slot {slot}: duplicate action for unit {uid}"
        seen.add(uid)
        name = action.get("action")
        assert name in ALLOWED, f"slot {slot}: action {name}"
        unit = living[uid]
        if name == "MOVE":
            assert action.get("direction") in DIRECTIONS
            limit = 2 if unit.kind == "STRIDER" else 1
            assert 1 <= action.get("steps", 0) <= limit
        if name in ("ATTACK", "CAPTURE"):
            assert not unit.immune(game.tick), f"slot {slot}: immune unit {uid} used {name}"


class ArenaContractTests(unittest.TestCase):
    def test_full_matches_are_legal_and_quiet(self):
        roles = list(ARENA_NAMES)
        for index, map_name in enumerate(("open", "pillars", "cross", "xmap", "random")):
            game = _run_match(201 + index, map_name, roles)
            self.assertEqual(sum(game.invalid_counts.values()), 0, map_name)
            self.assertGreaterEqual(game.tick, 160)

    def test_bots_are_deterministic(self):
        roles = list(ARENA_NAMES)
        traces = []
        for _ in range(2):
            match_id = "determinism"
            game = Game(seed=210, map_name="random", match_id=match_id)
            bots = {slot: make_arena_bot(roles[slot], match_id, slot) for slot in range(4)}
            trace = []
            for _tick in range(40):
                game.begin_tick()
                team_actions = {}
                for slot in range(4):
                    if game.teams[slot].eliminated:
                        continue
                    actions, priority = bots[slot].act(game.observe(slot))
                    team_actions[slot] = (actions, priority)
                    trace.append((slot, actions, priority))
                game.resolve(team_actions)
            traces.append(trace)
        self.assertEqual(traces[0], traces[1])

    def test_attacker_retreats_into_shrinking_zone(self):
        game = Game(seed=201, map_name="open", match_id="zone-regression")
        game.tick = 140
        for index, unit in enumerate(game.teams[0].units):
            unit.x, unit.y = 0, 9 + 2 * index
        for team_id in range(1, 4):
            for index, unit in enumerate(game.teams[team_id].units):
                unit.x, unit.y = 18, 18 - index
        for hunter in game.hunters:
            hunter.x, hunter.y = 19, 0
        observation = game.observe(0)
        bot = make_arena_bot("arena_attacker", "zone-regression", 0)
        actions, _priority = bot.act(observation)
        self.assertEqual(len(actions), len(game.teams[0].units))
        radius = radius_at(game.tick)
        for action in actions:
            self.assertEqual(action["action"], "MOVE")
            unit = next(u for u in game.teams[0].units if u.id == action["unit_id"])
            delta = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}[action["direction"]]
            dest = (unit.x + delta[0] * action["steps"], unit.y + delta[1] * action["steps"])
            self.assertLess(center_distance(dest), center_distance((unit.x, unit.y)))


if __name__ == "__main__":
    unittest.main()
