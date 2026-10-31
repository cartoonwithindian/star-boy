"""Rule-level tests for the approximate simulator.

These verify the engine's internal consistency against the public rulebook as
implemented here. They are NOT official judge validation.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulator.engine import Game, Warning, radius_at  # noqa: E402


def move(uid, direction, steps):
    return {"unit_id": uid, "action": "MOVE", "direction": direction, "steps": steps}


def attack(uid, target):
    return {"unit_id": uid, "action": "ATTACK", "target_unit_id": target}


def capture(uid, relay):
    return {"unit_id": uid, "action": "CAPTURE", "relay_id": relay}


def defend(uid):
    return {"unit_id": uid, "action": "DEFEND"}


def harvest(uid):
    return {"unit_id": uid, "action": "HARVEST"}


def wait(uid):
    return {"unit_id": uid, "action": "WAIT"}


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.game = Game(seed=0, map_name="open", match_id="test")
        self.game.hunters = []
        self.game.warnings = []
        # Park every unit on distinct, obstacle-free tiles.
        spots = [(7, 7), (12, 12), (1, 10), (10, 1), (17, 7), (7, 17), (18, 18), (2, 2)]
        index = 0
        for team in self.game.teams.values():
            for unit in team.units:
                unit.x, unit.y = spots[index]
                index += 1

    def resolve(self, actions, tick=None):
        if tick is not None:
            self.game.tick = tick
        return self.game.resolve(actions)

    def place(self, team_id, index, x, y, hp=None):
        unit = self.game.teams[team_id].units[index]
        unit.x, unit.y = x, y
        if hp is not None:
            unit.hp = hp
        return unit


class MovementTests(EngineCase):
    def test_two_step_straight_move(self):
        unit = self.place(0, 0, 5, 5)
        self.resolve({0: ([move(unit.id, "E", 2)], [unit.id])})
        self.assertEqual(unit.pos, (7, 5))

    def test_wall_blocks_move(self):
        unit = self.place(0, 0, 5, 5)
        self.game.walls.add((6, 5))
        self.resolve({0: ([move(unit.id, "E", 2)], [unit.id])})
        self.assertEqual(unit.pos, (5, 5))

    def test_relay_tile_blocks_move(self):
        unit = self.place(0, 0, 4, 5)
        self.resolve({0: ([move(unit.id, "N", 1)], [unit.id])})
        self.assertEqual(unit.pos, (4, 5))

    def test_stationary_occupant_blocks(self):
        unit = self.place(0, 0, 5, 5)
        blocker = self.place(1, 0, 6, 5)
        self.resolve({0: ([move(unit.id, "E", 1)], [unit.id])})
        self.assertEqual(unit.pos, (5, 5))
        self.assertEqual(blocker.pos, (6, 5))

    def test_following_a_departing_unit_succeeds(self):
        follower = self.place(0, 0, 5, 5)
        leader = self.place(1, 0, 6, 5)
        self.resolve({
            0: ([move(follower.id, "E", 1)], [follower.id]),
            1: ([move(leader.id, "E", 1)], [leader.id]),
        })
        self.assertEqual(follower.pos, (6, 5))
        self.assertEqual(leader.pos, (7, 5))

    def test_swap_fails(self):
        a = self.place(0, 0, 5, 5)
        b = self.place(1, 0, 6, 5)
        self.resolve({
            0: ([move(a.id, "E", 1)], [a.id]),
            1: ([move(b.id, "W", 1)], [b.id]),
        })
        self.assertEqual(a.pos, (5, 5))
        self.assertEqual(b.pos, (6, 5))

    def test_competing_arrivals_cancel(self):
        a = self.place(0, 0, 5, 4)
        b = self.place(1, 0, 5, 6)
        self.resolve({
            0: ([move(a.id, "S", 1)], [a.id]),
            1: ([move(b.id, "N", 1)], [b.id]),
        })
        self.assertEqual(a.pos, (5, 4))
        self.assertEqual(b.pos, (5, 6))


class CombatTests(EngineCase):
    def test_simultaneous_attacks_both_land(self):
        a = self.place(0, 0, 5, 5)
        b = self.place(1, 0, 6, 5)
        self.resolve({
            0: ([attack(a.id, b.id)], [a.id]),
            1: ([attack(b.id, a.id)], [b.id]),
        })
        self.assertEqual(a.hp, 5)
        self.assertEqual(b.hp, 5)

    def test_lethal_exchange_kills_both(self):
        a = self.place(0, 0, 5, 5, hp=1)
        b = self.place(1, 0, 6, 5, hp=1)
        self.resolve({
            0: ([attack(a.id, b.id)], [a.id]),
            1: ([attack(b.id, a.id)], [b.id]),
        })
        self.assertFalse(a.alive)
        self.assertFalse(b.alive)

    def test_defend_blocks_two_damage(self):
        a = self.place(0, 0, 5, 5)
        b = self.place(1, 0, 6, 5)
        self.game.teams[0].flux = 1
        self.resolve({
            0: ([defend(a.id)], [a.id]),
            1: ([attack(b.id, a.id)], [b.id]),
        })
        self.assertEqual(a.hp, 6)  # Strider hit for 1, fully absorbed

    def test_defend_pool_caps_at_two(self):
        a = self.place(0, 0, 5, 5, hp=6)
        b1 = self.place(1, 0, 5, 4)
        b2 = self.place(1, 1, 6, 5)
        self.game.teams[0].flux = 1
        self.resolve({
            0: ([defend(a.id)], [a.id]),
            1: ([attack(b1.id, a.id), attack(b2.id, a.id)], [b1.id, b2.id]),
        })
        # Strider + Bulwark + focus bonus = 1 + 2 + 1 = 4; DEFEND absorbs 2.
        self.assertEqual(a.hp, 4)

    def test_allied_focus_fire_bonus(self):
        a = self.place(0, 0, 5, 5, hp=10)
        b1 = self.place(1, 0, 5, 4)
        b2 = self.place(1, 1, 6, 5)
        self.resolve({
            1: ([attack(b1.id, a.id), attack(b2.id, a.id)], [b1.id, b2.id]),
        })
        # 1 (Strider) + 2 (Bulwark) + 1 focus bonus = 4 damage.
        self.assertEqual(a.hp, 6)


class FluxTests(EngineCase):
    def test_attack_costs_two(self):
        a = self.place(0, 0, 5, 5)
        b = self.place(1, 0, 6, 5)
        self.resolve({0: ([attack(a.id, b.id)], [a.id])})
        self.assertEqual(self.game.teams[0].flux, 4)

    def test_priority_controls_affordability(self):
        a = self.place(0, 0, 5, 5)
        b = self.game.teams[0].units[1]
        b.x, b.y = (6, 5)
        enemy = self.place(1, 0, 5, 6)
        self.game.teams[0].flux = 2
        self.resolve({0: ([attack(a.id, enemy.id), attack(b.id, enemy.id)], [b.id, a.id])})
        self.assertGreaterEqual(self.game.invalid_counts[0], 1)

    def test_regen_every_three_ticks(self):
        for tick in (1, 2, 3):
            self.game.tick = tick
            self.game.begin_tick()
            self.game.resolve({})
        self.assertEqual(self.game.teams[0].flux, 7)

    def test_harvest_and_refill(self):
        unit = self.place(0, 0, 4, 5)
        well = next(w for w in self.game.wells.values() if w.pos == (4, 5))
        self.game.teams[0].flux = 6
        self.resolve({0: ([harvest(unit.id)], [unit.id])}, tick=20)
        self.assertEqual(self.game.teams[0].flux, 9)
        self.assertFalse(well.charged)
        self.assertEqual(well.available_from_tick, 26)
        self.game.tick = 26
        self.game.begin_tick()
        self.assertTrue(well.charged)


class RelayTests(EngineCase):
    def test_capture_adjacent_costs_one(self):
        unit = self.place(0, 0, 4, 5)
        relay = self.game.relays[0]
        self.game.teams[0].flux = 3
        self.resolve({0: ([capture(unit.id, relay.id)], [unit.id])})
        self.assertEqual(relay.owner, 0)
        self.assertEqual(self.game.teams[0].flux, 2)

    def test_competing_captures_cancel_but_cost(self):
        a = self.place(0, 0, 4, 5)
        b = self.place(1, 0, 5, 4)
        relay = self.game.relays[0]
        self.game.teams[0].flux = 3
        self.game.teams[1].flux = 3
        self.resolve({
            0: ([capture(a.id, relay.id)], [a.id]),
            1: ([capture(b.id, relay.id)], [b.id]),
        })
        self.assertIsNone(relay.owner)
        self.assertEqual(self.game.teams[0].flux, 2)
        self.assertEqual(self.game.teams[1].flux, 2)

    def test_scoring_one_per_two_clean_ticks(self):
        unit = self.place(0, 0, 4, 5)
        relay = self.game.relays[0]
        self.resolve({0: ([capture(unit.id, relay.id)], [unit.id])}, tick=30)
        for tick in (31, 32, 33):
            self.game.tick = tick
            self.game.begin_tick()
            self.game.resolve({0: ([wait(unit.id)], [unit.id])})
        # Four clean owner ticks (capture tick + 31..33) -> counters 1..4 -> 2 points.
        self.assertEqual(self.game.teams[0].points, 2)

    def test_enemy_adjacency_pauses_scoring(self):
        owner = self.place(0, 0, 4, 5)
        relay = self.game.relays[0]
        self.resolve({0: ([capture(owner.id, relay.id)], [owner.id])}, tick=30)
        self.place(1, 0, 5, 4)
        for tick in (31, 32, 33):
            self.game.tick = tick
            self.game.begin_tick()
            self.game.resolve({})
        self.assertEqual(self.game.teams[0].points, 0)

    def test_eliminated_owner_relay_becomes_neutral(self):
        relay = self.game.relays[0]
        relay.owner = 0
        for unit in self.game.teams[0].units:
            unit.alive = False
        self.game.teams[0].revival_charge = False
        self.resolve({}, tick=40)
        self.assertIsNone(relay.owner)


class HazardTests(EngineCase):
    def test_safe_zone_damage_after_contraction(self):
        unit = self.place(0, 0, 0, 0)
        self.assertEqual(radius_at(116), 14)
        self.resolve({}, tick=116)
        self.assertEqual(unit.hp, 4)

    def test_defend_applies_to_hazards(self):
        unit = self.place(0, 0, 0, 0)
        self.game.teams[0].flux = 1
        self.resolve({0: ([defend(unit.id)], [unit.id])}, tick=116)
        self.assertEqual(unit.hp, 6)

    def test_pulse_damage(self):
        unit = self.place(0, 0, 5, 5)
        self.game.warnings.append(Warning("PULSE", 50, 50, (5, 5), None, [(5, 5)]))
        self.resolve({}, tick=50)
        self.assertEqual(unit.hp, 4)


class RevivalTests(EngineCase):
    def test_revival_schedule_and_immunity(self):
        victim = self.place(0, 0, 5, 5, hp=1)
        killer = self.place(1, 0, 6, 5)
        self.resolve({1: ([attack(killer.id, victim.id)], [killer.id])}, tick=40)
        self.assertFalse(victim.alive)
        self.assertEqual(self.game.teams[0].revival_queue[0]["due"], 44)
        self.assertEqual(self.game.teams[0].revival_queue[0]["unit_id"], victim.id)

        self.game.tick = 44
        self.game.begin_tick()
        self.assertTrue(victim.alive)
        self.assertEqual(victim.hp, 6)
        self.assertTrue(victim.immune(44))
        self.assertTrue(victim.immune(45))
        self.assertFalse(victim.immune(46))

        team = self.game.teams[0]
        self.game.tick = 44
        self.assertFalse(self.game._validate(team, victim, attack(victim.id, killer.id)))
        self.assertFalse(self.game._validate(team, victim, capture(victim.id, 0)))
        self.assertTrue(self.game._validate(team, victim, defend(victim.id)))

    def test_elimination_requires_no_revival(self):
        for unit in self.game.teams[0].units:
            unit.alive = False
        self.game.teams[0].revival_charge = False
        self.resolve({}, tick=50)
        self.assertTrue(self.game.teams[0].eliminated)
        self.assertEqual(self.game.teams[0].elimination_tick, 50)


class RankingTests(unittest.TestCase):
    def test_active_ranks_ahead_of_eliminated(self):
        game = Game(seed=12, map_name="open", match_id="rank")
        for team_id in (1, 2, 3):
            team = game.teams[team_id]
            for unit in team.units:
                unit.alive = False
            team.revival_charge = False
            team.eliminated = True
            team.elimination_tick = 10 + team_id
        game.teams[0].points = 5
        results = game.results()
        self.assertEqual(results["teams"][0]["placement"], 1.0)
        self.assertEqual(results["teams"][0]["placement_points"], 4.0)

    def test_points_break_ties_among_active(self):
        game = Game(seed=13, map_name="open", match_id="rank2")
        game.teams[0].points = 2
        game.teams[1].points = 9
        results = game.results()
        self.assertEqual(results["teams"][1]["placement"], 1.0)
        self.assertEqual(results["teams"][0]["placement"], 2.0)


class DeterminismTests(unittest.TestCase):
    def test_same_seed_same_match(self):
        from simulator.tournament import run_match

        first, _ = run_match("passive", ["passive"], 7, "open")
        second, _ = run_match("passive", ["passive"], 7, "open")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
