#!/usr/bin/env bash
# Run integrity, protocol and fixture checks against the tournament agent.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KIT="${PARTICIPANT_KIT_DIR:-$ROOT/participant_kit}"
if [ ! -d "$KIT" ]; then
  KIT="$(find "$ROOT" -maxdepth 1 -type d -name '*Participant_Kit' | head -n 1)"
fi
AGENT="$ROOT/agent/agent.py"

echo "== kit integrity =="
(cd "$KIT" && sha256sum -c SHA256SUMS.txt)
(cd "$KIT/sdk" && sha256sum -c SHA256SUMS.txt)

echo "== python syntax =="
python3 -m py_compile "$AGENT" "$ROOT/simulator/"*.py "$ROOT/tests/"*.py

echo "== smoke =="
python3 "$KIT/tools/smoke-test.py" python3 "$AGENT"

echo "== practice (general) =="
python3 "$KIT/tools/practice-scenarios.py" python3 "$AGENT"

echo "== practice (strict actions) =="
python3 "$KIT/tools/practice-scenarios.py" --strict-actions python3 "$AGENT"

echo "== local unit/contract tests =="
(cd "$ROOT" && python3 -m unittest discover -s tests)

echo "ALL CHECKS PASSED"
