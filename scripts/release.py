#!/usr/bin/env python3
"""Publish CI builds to the APT repositories: one repository per project.

    release.py --artifacts DIR --remote s3://bucket|DIR --key KEYID [--work DIR]
               [--commit SHA] [--all-builds] [--bootstrap] [--dry-run]

Layout at the remote (deb.freshdeb.com):

    freshdeb-archive-keyring.gpg[.asc]   one signing key for every project
    status.json                          registry: published projects and the
                                         commit each was last released from
    <project>/dists/ pool/ db/           a reprepro repository per project,
    <project>/status.json                components = tracks and aliases

    deb [signed-by=...] https://deb.freshdeb.com/valkey trixie v9

For every project with builds in this run (or already published, so alias
changes apply), independently of the others:

  1. Restore its db/ and dists/ from the remote and verify the signed
     indexes before trusting them. A registered project without a database
     is refused; an unregistered one starts empty.
  2. Include each build into its track component, make every alias
     component (v9) identical to its target track (v9.1), export, and verify
     that only the expected index entries changed.
  3. Upload it (pool first, signed Release files last; never deleting).

A failing project doesn't stop the others; the run fails at the end and the
registry keeps the failed project's previous commit, so CI plans its
changes again. --bootstrap is only for an empty bucket (no registry yet).

--reset-project NAME starts a registered project's repository from scratch,
e.g. after its components were renamed. It only works once the project's
remote directory has been deleted by hand: a typo can't wipe a live
repository, and a missing database without --reset-project stays an error.
Requires reprepro, gpg, dpkg-deb, python3-yaml, python3-debian, and the aws
CLI for s3:// remotes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from debian.deb822 import Packages, Release

sys.path.insert(0, str(Path(__file__).resolve().parent))
import freshdeb  # noqa: E402
from freshdeb import FreshdebError, require  # noqa: E402

FIELDS = ("Package", "Version", "Architecture", "SHA256", "Size", "Filename")
SYNC = Path(__file__).resolve().parent / "sync-repo.sh"


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


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Project:
    """One package's repository: its codenames, components and aliases."""

    def __init__(self, config, pkg):
        self.config = config
        self.pkg = pkg
        self.name = pkg.name
        self.archs = list(config.architectures)
        built = {c for track in pkg.tracks.values() for c in pkg.codenames(config, track)}
        self.codenames = [c for c in config.distributions if c in built]
        self.components = pkg.components()
        # alias component -> track component, e.g. v9 -> v9.1
        self.aliases = {pkg.component(alias): pkg.component(target) for alias, target in pkg.aliases.items()}


def write_conf(repo, project, key):
    conf = repo / "conf"
    conf.mkdir(parents=True, exist_ok=True)
    archive = project.config.archive
    stanzas = []
    for codename in project.codenames:
        dist = project.config.distributions[codename]
        stanzas.append("\n".join([
            f"Origin: {archive['origin']}",
            f"Label: {archive['label']} {project.name}",
            f"Codename: {codename}",
            f"Suite: {codename}",
            f"Architectures: {' '.join(project.archs)}",
            f"Components: {' '.join(project.components)}",
            f"Description: {project.pkg.data['description']} for {dist.title}",
            f"SignWith: {key}",
        ]))
    (conf / "distributions").write_text("\n\n".join(stanzas) + "\n")
    (conf / "options").write_text(f"basedir {repo}\n")


def reprepro(repo, *args):
    return run("reprepro", "--basedir", str(repo), *args)


# ─── indexes ────────────────────────────────────────────────────────────────

def published_components(repo, codename):
    path = repo / "dists" / codename / "Release"
    return set(Release(path.read_text()).get("Components", "").split()) if path.exists() else None


def read_indexes(repo, project):
    """{(codename, component, arch, package): record} from signed indexes."""
    records = {}
    for codename in project.codenames:
        dist = repo / "dists" / codename
        if not (dist / "Release").exists():
            continue
        run("gpg", "--batch", "--verify", str(dist / "Release.gpg"), str(dist / "Release"))
        release = Release((dist / "Release").read_text())
        checksums = {entry["name"]: entry for entry in release.get("SHA256", [])}
        listed = set(release.get("Components", "").split())
        unknown = listed - set(project.components)
        require(not unknown, f"{project.name} {codename}: published components {sorted(unknown)} are no longer "
                             "in package.yaml; never remove a track (let its eol pass instead)")
        for component in sorted(listed):
            for arch in project.archs:
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

def build_infos(artifacts):
    for info_path in sorted(artifacts.rglob("build-info.json")):
        yield info_path, json.loads(info_path.read_text())


def collect(artifacts, project, all_builds=False):
    """Validate this project's build results against package.yaml.

    Builds not marked publishable (canaries, test-only runs) are ignored,
    unless all_builds is set: a release dispatched by hand for a specific
    run publishes everything in it.

    Returns [(info, component, [(deb_path, fields)])] for builds to publish.
    """
    pkg, config = project.pkg, project.config
    builds = []
    seen = set()
    for info_path, info in build_infos(artifacts):
        if info["package"] != project.name:
            continue
        label = f"{info['package']} {info['track']} {info['codename']}/{info['arch']}"
        if not info.get("publish") and not all_builds:
            print(f"  ignore   {label}: not a publishable build")
            continue
        if info.get("skipped"):
            print(f"  skip     {label}: {info.get('skip_reason')}")
            continue
        track = pkg.tracks.get(info["track"])
        require(track and track.active(), f"{label}: unknown or end-of-life track")
        require(info["codename"] in pkg.codenames(config, track), f"{label}: codename not built")
        require(info["arch"] in project.archs, f"{label}: unknown architecture")
        expected_version = pkg.version(config, info["track"], info["codename"])
        require(info["version"] == expected_version,
                f"{label}: built {info['version']}, but package.yaml now says {expected_version}")
        require((info["track"], info["codename"], info["arch"]) not in seen, f"{label}: duplicate build")
        seen.add((info["track"], info["codename"], info["arch"]))

        # Every job builds the Architecture: all packages (so each can be
        # tested on its own); only the primary architecture's are published.
        primary = info["arch"] == config.primary_arch
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
            if fields["Architecture"] == "all" and not primary:
                continue
            fields["SHA256"] = sha256(path)
            debs.append((path, fields))
        built = {fields["Package"] for _, fields in debs}
        require(built == expected, f"{label}: built {sorted(built)}, expected {sorted(expected)}")
        builds.append((info, pkg.component(info["track"]), debs))
    return builds


def include(repo, project, before, builds):
    """Add builds to their track components. Returns (expected index entries
    {(codename, component, arch, package): (version, sha256)}, files added)."""
    expected = {}
    added = 0
    for info, component, debs in builds:
        codename = info["codename"]
        for path, fields in debs:
            archs = project.archs if fields["Architecture"] == "all" else [fields["Architecture"]]
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
            added += 1
            for key in keys:
                expected[key] = (fields["Version"], fields["SHA256"])
    return expected, added


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
    if source.startswith("s3://"):
        run("aws", "s3", "cp", "--only-show-errors", f"{source.rstrip('/')}/{filekey}", str(target))
    else:
        shutil.copy2(Path(source) / filekey, target)
    return target


def sync_aliases(repo, project, pool_source):
    """Make each alias component identical to its target. Returns changes made."""
    changes = 0
    for alias, target in project.aliases.items():
        for codename in project.codenames:
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
                changes += 1
            for (arch, package) in sorted(set(have) - set(want)):
                print(f"  alias    {alias} {codename} remove {package} ({arch})")
                reprepro(repo, "--export=never", "-C", alias, "-A", arch, "remove", codename, package)
                changes += 1
    return changes


# ─── validation ─────────────────────────────────────────────────────────────

def check_result(repo, project, before, after, expected):
    for key in sorted(set(before) | set(after)):
        if key[1] in project.aliases:
            continue
        if key in expected:
            version, digest = expected[key]
            require(key in after, f"{key} is missing after the release")
            require((after[key]["Version"], after[key]["SHA256"]) == (version, digest),
                    f"{key}: published {after[key]['Version']}, expected {version}")
        else:
            require(before.get(key) == after.get(key), f"unrelated entry changed: {key}")
    for alias, target in project.aliases.items():
        for codename in project.codenames:
            for arch in project.archs:
                pick = lambda records, comp: {k[3]: (r["Version"], r["SHA256"]) for k, r in records.items()
                                              if k[:3] == (codename, comp, arch)}
                require(pick(after, alias) == pick(after, target),
                        f"{codename}/{alias}/{arch} does not match {target}")
    for key, (version, digest) in expected.items():
        filename = after[key]["Filename"]
        path = repo / filename
        if path.exists():
            require(sha256(path) == digest, f"pool file checksum mismatch: {filename}")


def write_status(repo, project, after, commit):
    status = {"project": project.name, "updated": now(), "commit": commit, "components": {}}
    for (codename, component, arch, package), record in sorted(after.items()):
        status["components"].setdefault(component, {}).setdefault(codename, {})[package] = record["Version"]
    (repo / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")


def release_project(project, repo, artifacts, key, new=False, pool_source=None, commit=None, all_builds=False):
    """Merge this project's builds into its restored repository and validate.

    Returns the number of changed index entries, or None when there was
    nothing to do (no export, nothing to upload)."""
    database = repo / "db" / "packages.db"
    if new:
        require(not database.exists(), f"{project.name} is not registered, but a repository database exists "
                                       "at the remote; refusing to guess which is right")
    else:
        # A missing database is never treated as a fresh start: a partial
        # build would otherwise erase every other package from the indexes.
        require(database.is_file() and database.stat().st_size > 0,
                f"{project.name} is registered but its repository database is missing; refusing to recreate it "
                "(to start it from scratch on purpose, release with reset_project)")
    write_conf(repo, project, key)
    before = read_indexes(repo, project)
    print(f"{project.name}: restored {len(before)} index entries")

    builds = collect(artifacts, project, all_builds)
    require(builds or not new, f"{project.name}: nothing to publish for a new project")
    expected, added = include(repo, project, before, builds)
    aliased = sync_aliases(repo, project, pool_source)
    layout_changed = any(published_components(repo, c) != set(project.components) for c in project.codenames)
    if not (added or aliased or layout_changed or new):
        print(f"{project.name}: nothing new")
        return None

    reprepro(repo, "export")
    after = read_indexes(repo, project)
    check_result(repo, project, before, after, expected)
    write_status(repo, project, after, commit)
    changed = sum(1 for k in set(before) | set(after) if before.get(k) != after.get(k))
    print(f"{project.name}: {changed} index entries changed, {len(after)} total")
    return changed


# ─── remote ─────────────────────────────────────────────────────────────────

def sync(mode, local, remote, dry_run=False):
    args = ["bash", str(SYNC), mode, str(local), remote]
    if dry_run:
        args.append("--dry-run")
    print(run(*args), end="")


def read_registry(remote):
    """The remote's status.json, or None if the bucket is empty."""
    if remote.startswith("s3://"):
        result = subprocess.run(["aws", "s3", "cp", "--only-show-errors", f"{remote}/status.json", "-"],
                                text=True, capture_output=True)
        if result.returncode != 0:
            if any(s in result.stderr for s in ("404", "Not Found", "NoSuchKey", "does not exist")):
                return None
            raise FreshdebError(f"cannot read {remote}/status.json:\n{result.stderr}")
        text = result.stdout
    else:
        path = Path(remote) / "status.json"
        if not path.exists():
            return None
        text = path.read_text()
    registry = json.loads(text)
    require(isinstance(registry.get("projects"), dict),
            f"{remote}/status.json is not a project registry (old single-repository layout?); "
            "clean up the bucket first")
    return registry


def put_root_files(root_dir, remote, dry_run=False):
    for path in sorted(root_dir.iterdir()):
        target = f"{remote}/{path.name}"
        if dry_run:
            print(f"(dry run) would upload {path.name} to {target}")
        elif remote.startswith("s3://"):
            run("aws", "s3", "cp", "--only-show-errors", "--cache-control", "no-cache", str(path), target)
        else:
            shutil.copy2(path, Path(remote) / path.name)


def export_keyring(root_dir, key):
    keyring = root_dir / "freshdeb-archive-keyring.gpg"
    keyring.write_bytes(subprocess.run(["gpg", "--export", key], capture_output=True, check=True).stdout)
    (root_dir / "freshdeb-archive-keyring.asc").write_text(run("gpg", "--export", "--armor", key))
    require(keyring.stat().st_size > 0, f"gpg exported nothing for key {key}")


def publish(root, work, artifacts, remote, key, bootstrap=False, commit=None, all_builds=False, dry_run=False,
            reset=()):
    """Release every affected project; returns {project: error} for failures."""
    remote = remote.rstrip("/")
    errors = freshdeb.validate_all(root)
    require(not errors, "catalog does not validate:\n  " + "\n  ".join(errors))
    config = freshdeb.load_config(root)
    packages = {pkg.name: pkg for pkg in freshdeb.load_packages(root)}

    registry = read_registry(remote)
    if registry is None:
        require(bootstrap, f"{remote} has no status.json (empty bucket?); use --bootstrap for the first release")
        registry = {"projects": {}}
    else:
        require(not bootstrap, f"--bootstrap given, but {remote} already has a project registry")
    unknown = set(registry["projects"]) - set(packages)
    require(not unknown, f"published projects {sorted(unknown)} are missing from packages/; never remove a package")
    reset = set(reset)
    unknown = reset - set(registry["projects"])
    require(not unknown, f"--reset-project {sorted(unknown)}: not a registered project (new ones need no reset)")

    built = {info["package"] for _, info in build_infos(artifacts)}
    unknown = built - set(packages)
    require(not unknown, f"builds for unknown packages: {sorted(unknown)}")
    require(built or not bootstrap, "nothing to publish; a bootstrap needs at least one build "
                                    "(were the builds made with publishing off? use --all-builds)")
    missing = reset - built
    require(not missing, f"--reset-project {sorted(missing)}: no builds in this run to start from")
    names = sorted(set(registry["projects"]) | built)
    print(f"Projects: {', '.join(names) or 'none'}")

    work.mkdir(parents=True, exist_ok=True)
    (work / "state-before").mkdir(exist_ok=True)
    failures = {}
    for name in names:
        print(f"── {name}")
        project = Project(config, packages[name])
        repo = work / "projects" / name
        try:
            repo.mkdir(parents=True)
            sync("pull", repo, f"{remote}/{name}")
            with tarfile.open(work / "state-before" / f"{name}.tar.gz", "w:gz") as tar:
                tar.add(repo, arcname=name)
            if name in reset:
                require(not (repo / "db").exists(), f"{name}: reset requested, but {remote}/{name}/ still has a "
                                                    "repository database; delete that directory first")
                print(f"{name}: starting from scratch (reset)")
            new = name not in registry["projects"] or name in reset
            changed = release_project(project, repo, artifacts, key, new=new, pool_source=f"{remote}/{name}",
                                      commit=commit, all_builds=all_builds)
            if changed is not None:
                sync("push", repo, f"{remote}/{name}", dry_run)
            entry = registry["projects"].setdefault(name, {})
            entry["commit"] = commit
            if changed is not None:
                entry["updated"] = now()
        except FreshdebError as error:
            print(f"ERROR: {name}: {error}", file=sys.stderr)
            failures[name] = str(error)

    root_dir = work / "root"
    root_dir.mkdir(exist_ok=True)
    export_keyring(root_dir, key)
    registry["updated"] = now()
    (root_dir / "status.json").write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    put_root_files(root_dir, remote, dry_run)
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--artifacts", type=Path, required=True, help="CI build directories")
    parser.add_argument("--remote", required=True, help="s3://bucket[/prefix], or a directory (tests)")
    parser.add_argument("--key", required=True, help="GPG key id to sign with")
    parser.add_argument("--work", type=Path, default=Path("release-work"))
    parser.add_argument("--root", type=Path, default=freshdeb.ROOT)
    parser.add_argument("--commit", help="commit the builds came from (recorded per project)")
    parser.add_argument("--all-builds", action="store_true",
                        help="also publish builds not marked publishable (manual releases of a chosen run)")
    parser.add_argument("--bootstrap", action="store_true", help="first release into an empty bucket")
    parser.add_argument("--dry-run", action="store_true", help="prepare and validate, upload nothing")
    parser.add_argument("--reset-project", action="append", default=[], metavar="NAME",
                        help="start this registered project's repository from scratch (its remote "
                             "directory must already be deleted)")
    args = parser.parse_args()
    try:
        failures = publish(args.root.resolve(), args.work.resolve(), args.artifacts.resolve(), args.remote,
                           args.key, args.bootstrap, args.commit, args.all_builds, args.dry_run,
                           args.reset_project)
    except FreshdebError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    for name, error in failures.items():
        print(f"FAILED: {name}: {error.splitlines()[0]}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
