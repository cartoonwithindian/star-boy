"""Run approximate multi-match benchmarks in the local rules engine.

This is a comparative tuning harness, not the official judge. Every match
places the focus program on a rotating team slot against three sampled
opponents so spawn bias averages out.

Examples:
    python3 -m simulator.tournament --games 24
    python3 -m simulator.tournament --focus starter --games 24
    python3 -m simulator.tournament --focus agent --agent agent/agent.py \
        --opponents starter,relay,aggressive,passive --maps open,pillars,cross,xmap,random
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

try:
    from .engine import Game
    from .bots import BOT_NAMES, DEFAULT_AGENT, make_bot
except ImportError:  # direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from simulator.engine import Game
    from simulator.bots import BOT_NAMES, DEFAULT_AGENT, make_bot

DEFAULT_MAPS = ("open", "pillars", "cross", "xmap", "random")
DEFAULT_OPPONENTS = ("starter", "relay", "aggressive", "passive")


def run_match(focus_kind, opponents, seed, map_name, agent_path=None):
    game = Game(seed=seed, map_name=map_name, match_id=f"sim-{map_name}-{seed}")
    focus_team = seed % 4
    rng = random.Random(seed * 7919 + 13)
    bots = [None, None, None, None]
    for team_id in range(4):
        kind = focus_kind if team_id == focus_team else rng.choice(opponents)
        bots[team_id] = make_bot(kind, game.match_id, team_id, agent_path)

    done = False
    guard = 0
    while not done and guard < 500:
        guard += 1
        game.begin_tick()
        bundle = {}
        for team_id, bot in enumerate(bots):
            observation = game.observe(team_id)
            try:
                actions, priority = bot.act(observation)
            except Exception:
                actions, priority = [], []
            bundle[team_id] = (actions, priority)
        done = game.resolve(bundle)

    return game.results(), focus_team


def _match_job(payload):
    focus_kind, opponents, seed, map_name, agent_path = payload
    result, focus_team = run_match(focus_kind, opponents, seed, map_name, agent_path)
    record = dict(result["teams"][focus_team])
    record.update(seed=seed, map=map_name, team_id=focus_team, end_tick=result["end_tick"])
    return record


def _summarise(records):
    def mean(key):
        return statistics.fmean(record[key] for record in records)

    placements = [record["placement"] for record in records]
    return {
        "games": len(records),
        "mean_placement": mean("placement"),
        "best_placement": min(placements),
        "worst_placement": max(placements),
        "first_rate": sum(1 for p in placements if p <= 1.0001) / len(records),
        "top2_rate": sum(1 for p in placements if p <= 2.0001) / len(records),
        "mean_points": mean("placement_points"),
        "mean_relay_points": mean("relay_points"),
        "mean_hp": mean("hp"),
        "mean_deaths": mean("deaths"),
        "eliminated_rate": sum(1 for r in records if r["eliminated"]) / len(records),
        "mean_invalid_actions": mean("invalid_actions"),
        "mean_end_tick": mean("end_tick"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Approximate multi-match benchmark harness")
    parser.add_argument("--focus", default="agent", choices=BOT_NAMES, help="program under test")
    parser.add_argument("--agent", default=str(DEFAULT_AGENT), help="path to the focus agent source")
    parser.add_argument("--games", type=int, default=24)
    parser.add_argument("--seed-base", type=int, default=1)
    parser.add_argument("--maps", default=",".join(DEFAULT_MAPS))
    parser.add_argument("--opponents", default=",".join(DEFAULT_OPPONENTS))
    parser.add_argument("--json", default="", help="write per-match records to this path")
    parser.add_argument("--jobs", type=int, default=1, help="parallel match processes")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    maps = [name.strip() for name in args.maps.split(",") if name.strip()]
    opponents = [name for name in (n.strip() for n in args.opponents.split(",")) if name]
    for name in opponents:
        if name not in BOT_NAMES:
            parser.error(f"unknown opponent: {name}")

    payloads = [
        (args.focus, opponents, args.seed_base + index, maps[index % len(maps)], args.agent)
        for index in range(args.games)
    ]
    records = []
    started = time.perf_counter()
    if args.jobs <= 1:
        for index, payload in enumerate(payloads):
            records.append(_match_job(payload))
            if not args.quiet and (index + 1) % 5 == 0:
                elapsed = time.perf_counter() - started
                last = records[-1]
                print(f"[{index + 1}/{args.games}] {elapsed:6.1f}s last: {last['map']} seed={last['seed']} "
                      f"place={last['placement']:.2f} relay_pts={last['relay_points']} hp={last['hp']}")
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(_match_job, payload): payload for payload in payloads}
            for done, future in enumerate(as_completed(futures), 1):
                records.append(future.result())
                if not args.quiet and done % 5 == 0:
                    elapsed = time.perf_counter() - started
                    last = records[-1]
                    print(f"[{done}/{args.games}] {elapsed:6.1f}s last: {last['map']} seed={last['seed']} "
                          f"place={last['placement']:.2f} relay_pts={last['relay_points']} hp={last['hp']}")
    records.sort(key=lambda record: record["seed"])

    summary = _summarise(records)
    elapsed = time.perf_counter() - started
    print()
    print(f"focus={args.focus} agent={args.agent}")
    print(f"opponents={','.join(opponents)} maps={','.join(maps)}")
    print(f"games={summary['games']} in {elapsed:.1f}s")
    print(f"mean placement {summary['mean_placement']:.2f} "
          f"(best {summary['best_placement']:.2f}, worst {summary['worst_placement']:.2f})")
    print(f"first {summary['first_rate'] * 100:.1f}% | top-2 {summary['top2_rate'] * 100:.1f}% "
          f"| placement pts {summary['mean_points']:.2f}")
    print(f"relay pts {summary['mean_relay_points']:.2f} | HP {summary['mean_hp']:.2f} "
          f"| deaths {summary['mean_deaths']:.2f} | eliminated {summary['eliminated_rate'] * 100:.1f}%")
    print(f"invalid actions {summary['mean_invalid_actions']:.2f} | end tick {summary['mean_end_tick']:.1f}")

    if args.json:
        payload = {"summary": summary, "records": records}
        Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
