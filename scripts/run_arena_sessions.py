#!/usr/bin/env python3
"""Run arena-session matches with the four agents from the arena skill.

The four roles (competitor, attacker, defender, judge) rotate across team slots
so spawn bias averages out. Every session uses the local approximate engine;
these are comparative sessions, not official results.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.arena_bots import ARENA_NAMES, make_arena_bot  # noqa: E402
from simulator.engine import Game  # noqa: E402

DEFAULT_MAPS = ("open", "pillars", "cross", "xmap", "random")


def run_session(roles, seed, map_name, session):
    match_id = f"arena-{session}-{map_name}-{seed}"
    game = Game(seed=seed, map_name=map_name, match_id=match_id)
    bots = {}
    slot_role = {}
    for slot in range(4):
        role = roles[(slot - session) % len(roles)]
        slot_role[slot] = role
        bots[slot] = make_arena_bot(role, match_id, slot)

    issues = []
    done = False
    while not done and game.tick <= 160:
        game.begin_tick()
        team_actions = {}
        for slot in range(4):
            if game.teams[slot].eliminated:
                continue
            observation = game.observe(slot)
            try:
                actions, priority = bots[slot].act(observation)
            except Exception:
                issues.append(
                    {
                        "tick": game.tick,
                        "team": slot,
                        "role": slot_role[slot],
                        "error": traceback.format_exc(limit=3),
                    }
                )
                actions, priority = [], []
            if len(actions) > 2 or len(priority) > 2:
                issues.append(
                    {
                        "tick": game.tick,
                        "team": slot,
                        "role": slot_role[slot],
                        "error": f"more than two actions: {len(actions)}",
                    }
                )
            team_actions[slot] = (actions, priority)
        done = game.resolve(team_actions)

    result = game.results()
    records = []
    for slot in range(4):
        team = result["teams"][slot]
        records.append(
            {
                "team_id": slot,
                "role": slot_role[slot],
                "placement": team["placement"],
                "placement_points": team["placement_points"],
                "relay_points": team["relay_points"],
                "hp": team["hp"],
                "deaths": team["deaths"],
                "eliminated": team["eliminated"],
                "elimination_tick": team["elimination_tick"],
                "invalid_actions": team["invalid_actions"],
            }
        )
        if team["invalid_actions"]:
            issues.append(
                {
                    "tick": result["end_tick"],
                    "team": slot,
                    "role": slot_role[slot],
                    "error": f"{team['invalid_actions']} invalid actions",
                }
            )
    return {
        "session": session,
        "seed": seed,
        "map": map_name,
        "end_tick": result["end_tick"],
        "teams": records,
        "issues": issues,
    }


def summarise(sessions):
    by_role = {}
    for session in sessions:
        for record in session["teams"]:
            by_role.setdefault(record["role"], []).append(record)
    summary = {}
    for role, records in sorted(by_role.items()):
        summary[role] = {
            "sessions": len(records),
            "mean_placement": statistics.mean(r["placement"] for r in records),
            "first_rate": sum(1 for r in records if r["placement"] == 1) / len(records),
            "top2_rate": sum(1 for r in records if r["placement"] <= 2) / len(records),
            "mean_placement_points": statistics.mean(r["placement_points"] for r in records),
            "mean_relay_points": statistics.mean(r["relay_points"] for r in records),
            "mean_hp": statistics.mean(r["hp"] for r in records),
            "mean_deaths": statistics.mean(r["deaths"] for r in records),
            "eliminated_rate": sum(1 for r in records if r["eliminated"]) / len(records),
            "invalid_actions": sum(r["invalid_actions"] for r in records),
        }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument("--seed-base", type=int, default=201)
    parser.add_argument("--maps", default=",".join(DEFAULT_MAPS))
    parser.add_argument("--json", default="")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    maps = [name.strip() for name in args.maps.split(",") if name.strip()]
    roles = list(ARENA_NAMES)
    sessions = []
    for index in range(args.sessions):
        seed = args.seed_base + index
        map_name = maps[index % len(maps)]
        session = run_session(roles, seed, map_name, index)
        sessions.append(session)
        if not args.quiet:
            places = ", ".join(
                f"{record['role']}={record['placement']:.0f}"
                for record in sorted(session["teams"], key=lambda r: r["placement"])
            )
            print(
                f"[{index + 1}/{args.sessions}] {map_name} seed={seed} "
                f"tick={session['end_tick']} {places} issues={len(session['issues'])}"
            )

    summary = summarise(sessions)
    print("\nrole summary (10 sessions, rotating slots)")
    for role, row in summary.items():
        print(
            f"{role:16s} place={row['mean_placement']:.2f} first={row['first_rate']:.0%} "
            f"top2={row['top2_rate']:.0%} relay={row['mean_relay_points']:.1f} "
            f"hp={row['mean_hp']:.2f} deaths={row['mean_deaths']:.2f} "
            f"elim={row['eliminated_rate']:.0%} invalid={row['invalid_actions']}"
        )
    issues = [issue for session in sessions for issue in session["issues"]]
    print(f"issues: {len(issues)}")
    for issue in issues[:10]:
        print(f"  session tick={issue['tick']} {issue['role']}: {issue['error'].splitlines()[0]}")

    if args.json:
        payload = {
            "roles": roles,
            "sessions": sessions,
            "summary": summary,
            "issues": issues,
        }
        Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
