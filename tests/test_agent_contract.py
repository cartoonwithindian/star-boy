"""Contract and robustness tests for the tournament agent.

The agent is executed in-process against simulator observations with
ARENA_STRICT enabled, so any internal exception fails the test instead of
silently degrading to WAIT.
"""
import importlib.util
import json
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.engine import Game, Warning  # noqa: E402
from simulator.bots import SDK_DIR, DEFAULT_AGENT  # noqa: E402

if str(SDK_DIR) not in sys.path:
    sys.path.insert(0, str(SDK_DIR))

import agent_sdk  # noqa: E402

ALLOWED = {"MOVE", "ATTACK", "DEFEND", "HARVEST", "CAPTURE", "SCAN", "WAIT"}
COST = {"MOVE": 0, "ATTACK": 2, "DEFEND": 1, "HARVEST": 0, "CAPTURE": 1, "SCAN": 1, "WAIT": 0}


def load_agent(name="agent_under_test"):
    spec = importlib.util.spec_from_file_location(name, str(DEFAULT_AGENT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentContractTests(unittest.TestCase):
    def setUp(self):
        os.environ["ARENA_STRICT"] = "1"

    def tearDown(self):
        os.environ.pop("ARENA_STRICT", None)

    def _check_bundle(self, bundle, observation):
        self.assertIsInstance(bundle, agent_sdk.ActionBundle)
        wire = bundle.to_wire()
        self.assertLessEqual(len(wire["actions"]), 2)
        self.assertLessEqual(len(wire["priority"]), 2)
        own = {u.id: u for u in observation.team.units if u.hp > 0}
        for action in wire["actions"]:
            self.assertIn(action["action"], ALLOWED)
            self.assertIn(action["unit_id"], own)
            if action["action"] == "MOVE":
                self.assertIn(action["direction"], {"N", "E", "S", "W"})
                self.assertTrue(1 <= action["steps"] <= (2 if own[action["unit_id"]].kind is agent_sdk.UnitKind.STRIDER else 1))
            if action["action"] == "ATTACK":
                self.assertFalse(own[action["unit_id"]].immune)
            if action["action"] == "CAPTURE":
                self.assertFalse(own[action["unit_id"]].immune)
        self.assertLess(len(json.dumps(wire, separators=(",", ":")).encode()), 16 * 1024)

    def test_full_match_never_raises(self):
        module = load_agent("agent_full_match")
        game = Game(seed=21, map_name="pillars", match_id="contract-1")
        done = False
        ticks = 0
        while not done and ticks < 160:
            game.begin_tick()
            observation = agent_sdk.Observation.from_wire(game.observe(0))
            bundle = module.decide(observation)
            self._check_bundle(bundle, observation)
            done = game.resolve({0: (bundle.to_wire()["actions"], bundle.to_wire()["priority"])})
            ticks += 1
        self.assertGreater(ticks, 0)

    def test_empty_team_returns_empty_bundle(self):
        module = load_agent("agent_empty")
        game = Game(seed=22, map_name="open", match_id="contract-2")
        for unit in game.teams[0].units:
            unit.alive = False
        observation = agent_sdk.Observation.from_wire(game.observe(0))
        bundle = module.decide(observation)
        self.assertEqual(bundle.to_wire()["actions"], [])

    def test_deterministic_across_instances(self):
        first = load_agent("agent_det_a")
        second = load_agent("agent_det_b")
        game = Game(seed=23, map_name="cross", match_id="contract-3")
        for _ in range(20):
            game.begin_tick()
            observation = agent_sdk.Observation.from_wire(game.observe(0))
            wire_a = first.decide(observation).to_wire()
            wire_b = second.decide(observation).to_wire()
            self.assertEqual(wire_a, wire_b)
            game.resolve({0: (wire_a["actions"], wire_a["priority"])})

    def test_decision_time_under_limit(self):
        module = load_agent("agent_timing")
        game = Game(seed=24, map_name="open", match_id="contract-4")
        game.tick = 141
        game.warnings = [
            Warning("PULSE", 142, 142, (9, 9), None, [(x, y) for x in range(8, 12) for y in range(8, 12)]),
            Warning("SEAL", 142, 143, (9, 9), None, [(8, 9), (9, 9)]),
        ]
        for team_id in range(4):
            for index, unit in enumerate(game.teams[team_id].units):
                unit.x, unit.y = 9 + index, 8 + team_id
        observation = agent_sdk.Observation.from_wire(game.observe(0))
        samples = []
        for _ in range(30):
            started = time.perf_counter()
            module.decide(observation)
            samples.append((time.perf_counter() - started) * 1000)
        self.assertLess(max(samples), 1000.0, f"max decision {max(samples):.1f}ms")
        self.assertGreater(len(observation.visible_enemies), 0)

    def test_handles_hunters_and_corruption(self):
        module = load_agent("agent_hunter")
        game = Game(seed=25, map_name="open", match_id="contract-5")
        game.tick = 60
        game.relays[0].owner = 0
        game.warnings.append(Warning("CORRUPT", 60, 61, game.relays[0].pos, 0, [game.relays[0].pos]))
        hunter = game.hunters[0] if game.hunters else None
        if hunter is None:
            from simulator.engine import Unit

            hunter = Unit(999, None, "HUNTER", 7, 7)
            hunter.hp = 4
            game.hunters.append(hunter)
        for index, unit in enumerate(game.teams[0].units):
            unit.x, unit.y = 6, 6 + index
        for unit in game.teams[1].units:
            unit.x, unit.y = 7, 8
        observation = agent_sdk.Observation.from_wire(game.observe(0))
        bundle = module.decide(observation)
        self._check_bundle(bundle, observation)


if __name__ == "__main__":
    unittest.main()
