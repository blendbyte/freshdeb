#!/usr/bin/env python3
"""Fetch Composer's own dependencies for an offline build of composer.phar.

Upstream's composer.lock pins every runtime dependency ("packages", not
"packages-dev") to a git commit. Each one is fetched from its git repository
by that commit, which git checks by content, and extracted into vendor/<name>
with "git archive", which leaves out what the package's .gitattributes mark
export-ignore, the same files as the dist zips upstream's own build installs.
Nothing is downloaded that isn't verified this way.

Also records the release's commit date (upstream's phar shows it in
"composer --version"), from the tag, checked against the commit in
package.yaml, in freshdeb-release-date.

    vendor-deps.py <source-dir>
"""

import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import yaml


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True).stdout


def fetch(url, ref, commit, workdir):
    """Fetch <ref> (a commit or tag) from <url>; it has to resolve to <commit>."""
    if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+\.git", url):
        sys.exit(f"{url}: only https GitHub repositories are expected here")
    repo = Path(tempfile.mkdtemp(dir=workdir))
    git("init", "-q", cwd=repo)
    git("fetch", "-q", "--depth", "1", url, ref, cwd=repo)
    head = git("rev-parse", "FETCH_HEAD^{commit}", cwd=repo).decode().strip()
    if head != commit:
        sys.exit(f"{url} {ref}: got commit {head}, expected {commit}")
    return repo


def main(srcdir):
    package_dir = Path(__file__).resolve().parent.parent
    pkg = yaml.safe_load((package_dir / "package.yaml").read_text())
    track = pkg["tracks"][os.environ["FRESHDEB_TRACK"]]

    with tempfile.TemporaryDirectory() as workdir:
        # The release date, from the tag freshdeb built (same commit).
        repo = fetch(pkg["upstream"]["git"], f"refs/tags/{track['tag']}", track["commit"], workdir)
        date = git("log", "-1", "--format=%cI", "FETCH_HEAD", cwd=repo).decode().strip()
        (srcdir / "freshdeb-release-date").write_text(date + "\n")
        print(f"  release date {date}")

        lock = json.loads((srcdir / "composer.lock").read_text())
        for package in lock["packages"]:
            name, source = package["name"], package.get("source") or {}
            if source.get("type") != "git" or not re.fullmatch(r"[0-9a-f]{40}", source.get("reference", "")):
                sys.exit(f"composer.lock: {name} has no git source pinned to a commit")
            if not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", name):
                sys.exit(f"composer.lock: unexpected package name {name!r}")
            repo = fetch(source["url"], source["reference"], source["reference"], workdir)
            target = srcdir / "vendor" / name
            target.mkdir(parents=True)
            archive = git("archive", "--format=tar", "FETCH_HEAD", cwd=repo)
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(target, filter="data")
                else:
                    tar.extractall(target)
            print(f"  {name:36} {package['version']:10} {source['reference']} ok")
        print(f"  fetched {len(lock['packages'])} dependencies")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
