#!/usr/bin/env python3
"""Build frp's web dashboards for an offline build.

frps and frpc embed their dashboards from web/frps/dist and web/frpc/dist
(without them upstream's Makefile builds with -tags noweb, no dashboards).
They aren't in the git tag; upstream's release workflow builds them with
Node 22 and npm before compiling. This does the same:

- Node from nodejs.org, pinned below by version and sha256 per
  architecture (from https://nodejs.org/dist/v<version>/SHASUMS256.txt).
  To bump: take the newest v22 release and its SHASUMS256 lines for
  linux-x64 and linux-arm64 .tar.xz.
- "npm ci" in web/, which installs exactly upstream's package-lock.json and
  checks every package against the lock's sha512 integrity hash. Packages
  without a hash, or from anywhere but the npm registry, fail here first.
- "npm run build" for each dashboard, as upstream's workflow does.

Only the dist directories stay in the source tree; the build then runs
offline.

    build-web.py <source-dir>
"""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

NODE_VERSION = "22.23.4"
NODE_SHA256 = {
    "x64": "1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce",
    "arm64": "6287aa46b6c375d816e63ae66d024fbbfd70404825aabdfdf898a970871d2f4b",
}
REGISTRY = "https://registry.npmjs.org/"
DASHBOARDS = ("frps", "frpc")


def node_arch():
    machine = platform.machine()
    arch = {"x86_64": "x64", "aarch64": "arm64"}.get(machine)
    if arch is None:
        sys.exit(f"no pinned Node build for {machine}")
    return arch


def fetch_node(dest):
    arch = node_arch()
    name = f"node-v{NODE_VERSION}-linux-{arch}"
    url = f"https://nodejs.org/dist/v{NODE_VERSION}/{name}.tar.xz"
    request = urllib.request.Request(url, headers={"User-Agent": "freshdeb"})
    with urllib.request.urlopen(request, timeout=300) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != NODE_SHA256[arch]:
        sys.exit(f"{url} has sha256 {digest}, pinned {NODE_SHA256[arch]}")
    archive = dest / f"{name}.tar.xz"
    archive.write_bytes(data)
    # tar(1): the archive has relative symlinks (bin/npm) that some Pythons'
    # tarfile filters reject.
    subprocess.run(["tar", "-xJf", str(archive), "-C", str(dest)], check=True)
    print(f"  node {NODE_VERSION} ({arch})  sha256 ok")
    return dest / name / "bin"


def check_lock(web):
    """Every package npm installs comes from the registry with an integrity
    hash; npm ci then verifies each download against it."""
    lock = json.loads((web / "package-lock.json").read_text())
    count = 0
    for path, entry in lock.get("packages", {}).items():
        if not path or entry.get("link") or "resolved" not in entry:
            continue  # the root, workspaces and links to them
        if not entry["resolved"].startswith(REGISTRY) or not entry.get("integrity", "").startswith("sha512-"):
            sys.exit(f"package-lock.json: {path} is not a registry package with a sha512 integrity hash")
        count += 1
    print(f"  package-lock.json: {count} packages, all with sha512 integrity hashes")


def main(srcdir):
    # Our lines in order with npm's output.
    sys.stdout.reconfigure(line_buffering=True)
    web = srcdir / "web"
    check_lock(web)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        node_bin = fetch_node(tmp)
        env = {**os.environ,
               "PATH": f"{node_bin}:{os.environ['PATH']}",
               "npm_config_cache": str(tmp / "npm-cache"),
               "npm_config_registry": REGISTRY,
               "npm_config_audit": "false",
               "npm_config_fund": "false",
               "npm_config_update_notifier": "false"}
        try:
            # No install scripts: the build doesn't need them (native tools
            # like esbuild come as per-platform packages from the lock).
            subprocess.run(["npm", "ci", "--ignore-scripts", "--no-progress"], cwd=web, env=env, check=True)
            for dashboard in DASHBOARDS:
                subprocess.run(["npm", "run", "build", "--workspace", dashboard], cwd=web, env=env, check=True)
                if not (web / dashboard / "dist/index.html").is_file():
                    sys.exit(f"web/{dashboard}/dist/index.html was not built")
        finally:
            # Only the built dashboards stay in the source tree.
            for modules in [web / "node_modules", *web.glob("*/node_modules")]:
                shutil.rmtree(modules, ignore_errors=True)
    for dashboard in DASHBOARDS:
        files = sum(1 for f in (web / dashboard / "dist").rglob("*") if f.is_file())
        print(f"  web/{dashboard}/dist: {files} files")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
