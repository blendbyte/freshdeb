#!/usr/bin/env python3
"""Merge CI build results into the APT repository and validate the result.

    release.py <repo> <artifacts> --key <gpg-key-id> [--bootstrap] [--pool-source <src>]

<repo> holds the state restored from production (db/ and dists/; pool/ is
not downloaded). <artifacts> holds build directories from CI, each with a
build-info.json and its .debs. Steps:

  1. Write conf/ from config.yaml and every package's tracks and aliases.
  2. Verify the restored indexes (signature, checksums) before trusting them.
  3. Include each publishable build into its track component.
  4. Make every alias component (valkey-9) identical to its target track
     (valkey-9.1), fetching pool files from --pool-source when they are not
     part of this release.
  5. Export, then verify that only the expected index entries changed.

Nothing is uploaded here; scripts/sync-repo.sh does that afterwards.
Requires reprepro, gpg, dpkg-deb, python3-yaml and python3-debian.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from debian.deb822 import Packages, Release

sys.path.insert(0, str(Path(__file__).resolve().parent))
import freshdeb  # noqa: E402
from freshdeb import FreshdebError, require  # noqa: E402

FIELDS = ("Package", "Version", "Architecture", "SHA256", "Size", "Filename")


def run(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if result.returncode != 0:
        raise FreshdebError(f"{' '.join(map(str, args))} failed:\n{result.stdout}{result.stderr}")
    return result.stdout


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Catalog:
    def __init__(self, root):
        self.config = freshdeb.load_config(root)
        self.packages = {pkg.name: pkg for pkg in freshdeb.load_packages(root)}
        self.codenames = list(self.config.distributions)
        self.archs = list(self.config.architectures)
        self.components = [c for pkg in self.packages.values() for c in pkg.components()]
        # alias component -> target track component
        self.aliases = {pkg.component(alias): pkg.component(target)
                        for pkg in self.packages.values() for alias, target in pkg.aliases.items()}


def write_conf(repo, catalog, key):
    conf = repo / "conf"
    conf.mkdir(parents=True, exist_ok=True)
    archive = catalog.config.archive
    stanzas = []
    for codename, dist in catalog.config.distributions.items():
        stanzas.append("\n".join([
            f"Origin: {archive['origin']}",
            f"Label: {archive['label']}",
            f"Codename: {codename}",
            f"Suite: {codename}",
            f"Architectures: {' '.join(catalog.archs)}",
            f"Components: {' '.join(catalog.components)}",
            f"Description: Current upstream releases for {dist.title}",
            f"SignWith: {key}",
        ]))
    (conf / "distributions").write_text("\n\n".join(stanzas) + "\n")
    (conf / "options").write_text(f"basedir {repo}\n")


def reprepro(repo, *args):
    return run("reprepro", "--basedir", str(repo), *args)


# ─── indexes ────────────────────────────────────────────────────────────────

def read_indexes(repo, catalog):
    """{(codename, component, arch, package): record} from signed indexes."""
    records = {}
    for codename in catalog.codenames:
        dist = repo / "dists" / codename
        if not (dist / "Release").exists():
            continue
        run("gpg", "--batch", "--verify", str(dist / "Release.gpg"), str(dist / "Release"))
        release = Release((dist / "Release").read_text())
        checksums = {entry["name"]: entry for entry in release.get("SHA256", [])}
        listed = set(release.get("Components", "").split())
        unknown = listed - set(catalog.components)
        require(not unknown, f"{codename}: published components {sorted(unknown)} are no longer in the catalog; "
                             "components must never be removed (mark the track end-of-life instead)")
        for component in sorted(listed):
            for arch in catalog.archs:
                name = f"{component}/binary-{arch}/Packages"
                path = dist / name
                require(path.is_file() and name in checksums, f"missing published index {codename}/{name}")
                entry = checksums[name]
                require(sha256(path) == entry["sha256"] and path.stat().st_size == int(entry["size"]),
                        f"index checksum mismatch: {codename}/{name}")
                for record in Packages.iter_paragraphs(path.read_text(), use_apt_pkg=False):
                    key = (codename, component, arch, record["Package"])
                    require(key not in records, f"duplicate entry in index: {key}")
                    records[key] = {field: record.get(field) for field in FIELDS}
    return records


# ─── artifacts ──────────────────────────────────────────────────────────────

def collect(artifacts, catalog):
    """Validate build results against the catalog at this commit.

    Returns [(info, component, [(deb_path, fields)])] for builds to publish.
    """
    builds = []
    seen = set()
    for info_path in sorted(artifacts.rglob("build-info.json")):
        info = json.loads(info_path.read_text())
        label = f"{info['package']} {info['track']} {info['codename']}/{info['arch']}"
        if not info.get("publish"):
            print(f"  ignore   {label}: not a publishable build")
            continue
        if info.get("skipped"):
            print(f"  skip     {label}: {info.get('skip_reason')}")
            continue
        pkg = catalog.packages.get(info["package"])
        require(pkg, f"{label}: unknown package")
        track = pkg.tracks.get(info["track"])
        require(track and track.active(), f"{label}: unknown or end-of-life track")
        require(info["codename"] in pkg.codenames(catalog.config, track), f"{label}: codename not built")
        require(info["arch"] in catalog.archs, f"{label}: unknown architecture")
        expected_version = pkg.version(catalog.config, info["track"], info["codename"])
        require(info["version"] == expected_version,
                f"{label}: built {info['version']}, but package.yaml now says {expected_version}")
        require((info["package"], info["track"], info["codename"], info["arch"]) not in seen,
                f"{label}: duplicate build")
        seen.add((info["package"], info["track"], info["codename"], info["arch"]))

        primary = info["arch"] == catalog.config.primary_arch
        expected = {name for name, arch in info["binaries"].items() if arch != "all" or primary}
        debs = []
        for name in info["debs"]:
            path = info_path.parent / name
            fields = dict(line.split(": ", 1) for line in
                          run("dpkg-deb", "--field", str(path), "Package", "Version", "Architecture").splitlines())
            require(fields["Package"] in info["binaries"], f"{label}: unexpected binary {fields['Package']}")
            require(fields["Version"] == info["version"], f"{label}: {name} has version {fields['Version']}")
            require(fields["Architecture"] in (info["arch"], "all"),
                    f"{label}: {name} has architecture {fields['Architecture']}")
            fields["SHA256"] = sha256(path)
            debs.append((path, fields))
        built = {fields["Package"] for _, fields in debs}
        require(built == expected, f"{label}: built {sorted(built)}, expected {sorted(expected)}")
        builds.append((info, pkg.component(info["track"]), debs))
    return builds


def include(repo, catalog, before, builds):
    """Add builds to their track components. Returns the expected index
    entries: {(codename, component, arch, package): (version, sha256)}."""
    expected = {}
    for info, component, debs in builds:
        codename = info["codename"]
        for path, fields in debs:
            archs = catalog.archs if fields["Architecture"] == "all" else [fields["Architecture"]]
            keys = [(codename, component, arch, fields["Package"]) for arch in archs]
            for key in keys:
                require(key not in expected, f"two builds provide {key}")
            current = before.get(keys[0])
            label = f"{component} {codename} {fields['Package']} {fields['Version']} ({fields['Architecture']})"
            if current:
                order = freshdeb.compare_versions(fields["Version"], current["Version"])
                require(order >= 0, f"refusing downgrade of {label}: {current['Version']} is published")
                if order == 0:
                    # Same version, possibly different bytes (a rebuild).
                    # The published file stays; versions are immutable.
                    print(f"  exists   {label}")
                    for key in keys:
                        expected[key] = (current["Version"], before[key]["SHA256"])
                    continue
            print(f"  include  {label}")
            reprepro(repo, "--export=never", "-C", component, "includedeb", codename, str(path))
            for key in keys:
                expected[key] = (fields["Version"], fields["SHA256"])
    return expected


# ─── aliases ────────────────────────────────────────────────────────────────

LIST_FORMAT = "${$architecture}\t${package}\t${version}\t${$filekey}\n"


def listing(repo, codename, component):
    out = reprepro(repo, f"--list-format={LIST_FORMAT}", "-C", component, "list", codename)
    entries = {}
    for line in out.splitlines():
        arch, package, version, filekey = line.split("\t")
        entries[(arch, package)] = (version, filekey)
    return entries


def fetch_pool_file(repo, filekey, source):
    target = repo / filekey
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    require(source, f"{filekey} is not in this release and no --pool-source was given")
    if source.startswith("s3://"):
        run("aws", "s3", "cp", "--only-show-errors", f"{source.rstrip('/')}/{filekey}", str(target))
    else:
        shutil.copy2(Path(source) / filekey, target)
    return target


def sync_aliases(repo, catalog, pool_source):
    for alias, target in catalog.aliases.items():
        for codename in catalog.codenames:
            want = listing(repo, codename, target)
            have = listing(repo, codename, alias)
            for (arch, package), (version, filekey) in sorted(want.items()):
                if have.get((arch, package), (None,))[0] == version:
                    continue
                print(f"  alias    {alias} {codename} {package} {version} ({arch}) from {target}")
                path = fetch_pool_file(repo, filekey, pool_source)
                current = have.get((arch, package))
                if current and freshdeb.compare_versions(version, current[0]) < 0:
                    # Alias moved to an older line; reprepro won't downgrade in place.
                    reprepro(repo, "--export=never", "-C", alias, "-A", arch, "remove", codename, package)
                reprepro(repo, "--export=never", "-C", alias, "-A", arch, "includedeb", codename, str(path))
            for (arch, package) in sorted(set(have) - set(want)):
                print(f"  alias    {alias} {codename} remove {package} ({arch})")
                reprepro(repo, "--export=never", "-C", alias, "-A", arch, "remove", codename, package)


# ─── validation ─────────────────────────────────────────────────────────────

def check_result(repo, catalog, before, after, expected):
    aliases = set(catalog.aliases)
    for key in sorted(set(before) | set(after)):
        component = key[1]
        if component in aliases:
            continue
        if key in expected:
            version, digest = expected[key]
            require(key in after, f"{key} is missing after the release")
            require((after[key]["Version"], after[key]["SHA256"]) == (version, digest),
                    f"{key}: published {after[key]['Version']}, expected {version}")
        else:
            require(before.get(key) == after.get(key), f"unrelated entry changed: {key}")
    for alias, target in catalog.aliases.items():
        for codename in catalog.codenames:
            for arch in catalog.archs:
                pick = lambda records, comp: {k[3]: (r["Version"], r["SHA256"]) for k, r in records.items()
                                              if k[:3] == (codename, comp, arch)}
                require(pick(after, alias) == pick(after, target),
                        f"{codename}/{alias}/{arch} does not match {target}")
    for key, (version, digest) in expected.items():
        filename = after[key]["Filename"]
        path = repo / filename
        if path.exists():
            require(sha256(path) == digest, f"pool file checksum mismatch: {filename}")


def write_status(repo, catalog, after, commit=None):
    # "commit" lets CI plan everything since the last release, even when
    # runs in between were cancelled.
    status = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "commit": commit, "components": {}}
    for (codename, component, arch, package), record in sorted(after.items()):
        status["components"].setdefault(component, {}).setdefault(codename, {})[package] = record["Version"]
    (repo / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")


def release(root, repo, artifacts, key, bootstrap=False, pool_source=None, commit=None):
    catalog = Catalog(root)
    errors = freshdeb.validate_all(root)
    require(not errors, "catalog does not validate:\n  " + "\n  ".join(errors))
    database = repo / "db" / "packages.db"
    if bootstrap:
        require(not database.exists(), "--bootstrap given, but a repository database already exists")
    else:
        # A missing database is never treated as a new repository: a partial
        # build would otherwise erase every unrelated package from the indexes.
        require(database.is_file() and database.stat().st_size > 0,
                "missing repository database; refusing to start an empty repository without --bootstrap")
    write_conf(repo, catalog, key)
    before = read_indexes(repo, catalog)
    print(f"Restored state: {len(before)} index entries")

    builds = collect(artifacts, catalog)
    expected = include(repo, catalog, before, builds)
    sync_aliases(repo, catalog, pool_source)
    reprepro(repo, "export")

    after = read_indexes(repo, catalog)
    check_result(repo, catalog, before, after, expected)
    write_status(repo, catalog, after, commit)
    changed = sum(1 for k in set(before) | set(after) if before.get(k) != after.get(k))
    print(f"Release prepared: {changed} index entries changed, {len(after)} total")
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", type=Path)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--key", required=True, help="GPG key id to sign with")
    parser.add_argument("--root", type=Path, default=freshdeb.ROOT)
    parser.add_argument("--bootstrap", action="store_true", help="create a new, empty repository")
    parser.add_argument("--pool-source", help="s3://bucket[/prefix] or a local directory holding pool/")
    parser.add_argument("--commit", help="freshdeb commit being released (recorded in status.json)")
    args = parser.parse_args()
    try:
        release(args.root.resolve(), args.repo.resolve(), args.artifacts.resolve(), args.key,
                args.bootstrap, args.pool_source, args.commit)
    except FreshdebError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
