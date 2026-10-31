#!/usr/bin/env python3
"""Build and validate the single-file submission artifact.

Copies agent/agent.py to submission/final_agent.py, then checks UTF-8, size,
syntax, protocol smoke test and a fixture run with the reference SDK on
PYTHONPATH. Does not upload anything.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _find_kit() -> Path:
    override = os.environ.get("PARTICIPANT_KIT_DIR")
    candidates = [Path(override)] if override else []
    candidates.append(ROOT / "participant_kit")
    candidates.extend(sorted(ROOT.glob("*Participant_Kit")))
    for kit in candidates:
        if (kit / "tools" / "smoke-test.py").is_file():
            return kit
    raise SystemExit("reference SDK package not found; set PARTICIPANT_KIT_DIR")


SOURCE = ROOT / "agent" / "agent.py"
TARGET = ROOT / "submission" / "final_agent.py"
KIT = _find_kit()
SDK = KIT / "sdk" / "python3.12"
LIMIT = 1024 * 1024


def main():
    data = SOURCE.read_bytes()
    text = data.decode("utf-8")  # raises if not valid UTF-8
    if len(data) > LIMIT:
        raise SystemExit(f"source is {len(data)} bytes, over the 1 MiB limit")
    compile(text, str(SOURCE), "exec")
    if "agent_sdk" not in text:
        raise SystemExit("source does not import the reference SDK")

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, TARGET)
    digest = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(data)} bytes, utf-8)")
    print(f"sha256 {digest}")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SDK) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    commands = [
        [sys.executable, str(KIT / "tools" / "smoke-test.py"), sys.executable, str(TARGET)],
        [sys.executable, str(KIT / "tools" / "practice-scenarios.py"), "--strict-actions", sys.executable, str(TARGET)],
    ]
    for command in commands:
        print("+", " ".join(str(part) for part in command))
        result = subprocess.run(command, cwd=ROOT, env=env)
        if result.returncode != 0:
            raise SystemExit(f"validation failed: {command[-1]}")
    print("submission artifact validated; not uploaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
