#!/usr/bin/env python3
"""Runs the test suite against a copy of the repository whose pins have all
moved, as watch.yml moves them: every tag and version one release on, other
commits, checksums and revisions, each Go toolchain one patch release on.

Tests must derive what they expect from the current pins. One that spells out
a pin passes today and fails on the next bump, in a watch pull request that
changed nothing it tests; this fails it when it is written instead.

    python3 tests/pin_drill.py
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEX = str.maketrans("0123456789abcdef", "123456789abcdef0")


def next_release(tag):
    """The tag with its last number one on: v1.3.9e -> v1.3.10e, v0.14rc2 -> v0.14rc3."""
    *_, last = re.finditer(r"\d+", tag)
    return f"{tag[:last.start()]}{int(last.group()) + 1}{tag[last.end():]}"


def move_pins(text, go):
    """package.yaml with its tracks' pins moved; go maps old -> new toolchain versions."""
    start = re.search(r"^tracks:\n", text, re.M)
    if not start:
        return text
    end = re.search(r"^\S", text[start.end():], re.M)
    end = start.end() + end.start() if end else len(text)
    tracks = text[start.end():end]
    tracks = re.sub(r'\b(tag|version): "([^"]+)"', lambda m: f'{m[1]}: "{next_release(m[2])}"', tracks)
    tracks = re.sub(r'\bgo: "([^"]+)"', lambda m: f'go: "{go.setdefault(m[1], next_release(m[1]))}"', tracks)
    tracks = re.sub(r"(?<![0-9a-f])([0-9a-f]{40}|[0-9a-f]{64})(?![0-9a-f])", lambda m: m[1].translate(HEX), tracks)
    tracks = re.sub(r"\brevision: (\d+)", lambda m: f"revision: {int(m[1]) + 1}", tracks)
    return text[:start.end()] + tracks + text[end:]


def add_toolchains(text, go):
    """config.yaml with each moved Go toolchain also listed under its new version."""
    for old, new in go.items():
        block = re.search(rf'^(    )"{re.escape(old)}":\n(?:\1  .*\n)+', text, re.M)
        if not block:
            sys.exit(f"pin drill: Go {old} is not in config.yaml toolchains.go")
        moved = block[0].replace(f'"{old}":', f'"{new}":', 1)
        moved = re.sub(r"[0-9a-f]{64}", lambda m: m[0].translate(HEX), moved)
        text = text[:block.start()] + moved + text[block.start():]
    return text


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pin-drill-"))
    try:
        repo = tmp / "repo"
        shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        go = {}
        for path in sorted(repo.glob("packages/*/package.yaml")):
            path.write_text(move_pins(path.read_text(), go))
        config = repo / "config.yaml"
        config.write_text(add_toolchains(config.read_text(), go))

        valid = subprocess.run([sys.executable, "scripts/freshdeb.py", "validate"], cwd=repo,
                               capture_output=True, text=True)
        if valid.returncode:
            sys.exit(f"pin drill: the moved catalog doesn't validate, fix the drill:\n{valid.stdout}{valid.stderr}")

        print("pin drill: every pin moved, running the tests", flush=True)
        tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=repo)
        if tests.returncode:
            print("pin drill: these tests break when watch.yml moves a pin; "
                  "derive what they expect from the current pins instead", file=sys.stderr)
        return tests.returncode
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    sys.exit(main())
