#!/usr/bin/env bash
# Benchmark the agent against the starter and simple bots in the local engine.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GAMES="${GAMES:-32}"
SEED_BASE="${SEED_BASE:-101}"
MAPS="${MAPS:-open,pillars,cross,xmap,random}"
OPPONENTS="${OPPONENTS:-starter,relay,aggressive,passive}"

echo "== agent =="
ARENA_STRICT=1 python3 -m simulator.tournament \
  --focus agent --games "$GAMES" --seed-base "$SEED_BASE" \
  --maps "$MAPS" --opponents "$OPPONENTS" --json /tmp/bench_agent.json

echo "== starter baseline (same seeds/opponents) =="
python3 -m simulator.tournament \
  --focus starter --games "$GAMES" --seed-base "$SEED_BASE" \
  --maps "$MAPS" --opponents "$OPPONENTS" --json /tmp/bench_starter.json

echo "compare /tmp/bench_agent.json and /tmp/bench_starter.json"
