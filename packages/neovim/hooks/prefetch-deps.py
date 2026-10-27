#!/usr/bin/env python3
"""Prefetch neovim's bundled dependencies for an offline build.

Upstream pins every bundled dependency (URL and sha256) in
cmake.deps/deps.txt. CMake's ExternalProject skips a download when a file
with the right hash is already in .deps/build/downloads/<name>/, so we put
verified copies there; the build then runs offline, and ExternalProject
checks each hash a second time.

    prefetch-deps.py <source-dir>
"""

import hashlib
import re
import sys
import urllib.request
from pathlib import Path

# Not built on Linux with neovim's defaults (Windows/macOS-only, optional, or
# developer tools); everything else in deps.txt is fetched, so dependencies a
# new neovim release adds are picked up without changing this file.
SKIP = {"WIN32YANK_X86_64", "GETTEXT", "LIBICONV", "WASMTIME", "UNCRUSTIFY", "LUA"}


def main(srcdir):
    deps = {}
    for line in (srcdir / "cmake.deps/deps.txt").read_text().splitlines():
        match = re.fullmatch(r"(\w+)_(URL|SHA256)\s+(\S+)", line.strip())
        if match:
            deps.setdefault(match.group(1), {})[match.group(2)] = match.group(3)
    downloads = srcdir / ".deps/build/downloads"
    fetched = 0
    for name, entry in sorted(deps.items()):
        if name in SKIP:
            continue
        if set(entry) != {"URL", "SHA256"} or not re.fullmatch(r"[0-9a-f]{64}", entry["SHA256"]):
            sys.exit(f"deps.txt: {name} has no URL and sha256 pair")
        target = downloads / name.lower() / entry["URL"].rsplit("/", 1)[-1]
        target.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(entry["URL"], headers={"User-Agent": "freshdeb"})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry["SHA256"]:
            sys.exit(f"{name}: {entry['URL']} has sha256 {digest}, deps.txt pins {entry['SHA256']}")
        target.write_bytes(data)
        fetched += 1
        print(f"  {name.lower():22} {entry['URL'].rsplit('/', 1)[-1]:52} sha256 ok")
    print(f"  prefetched {fetched} bundled dependencies")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
