#!/usr/bin/env python3
"""freshdeb: package catalog, CI planning, source preparation, upstream watch.

Subcommands
  validate        Check config.yaml and every packages/*/package.yaml.
  plan            Compute the CI build matrix from a git diff (or explicit
                  filters) and write it as JSON.
  prepare         Assemble a buildable source tree for one package, track and
                  codename. Runs inside the builder container.
  guard           Decide whether a prepared build would be newer than every
                  version the distribution itself ships. Runs inside the
                  builder container, after "apt-get update".
  watch           Look for new upstream releases and, with --apply, edit
                  package.yaml files accordingly (used by watch.yml).
  components      Print every APT component, one per line.

Only the Python standard library and PyYAML are required. "prepare" and
"guard" also need git, patch and dpkg, which the builder image provides.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]*$")
TRACK_RE = re.compile(r"^[0-9a-z][0-9a-z.]*$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
OPTION_RE = re.compile(r"^[a-z0-9][a-z0-9=_.,-]*$")
UPSTREAM_VERSION_RE = re.compile(r"^[0-9][A-Za-z0-9.+~]*$")
VENDOR_PREFIX = {"debian": "deb", "ubuntu": "ubuntu"}

# GitHub Actions refuses matrices with more than 256 jobs.
MAX_MATRIX = 256


class FreshdebError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise FreshdebError(message)


def run(*args, cwd=None, check=True):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise FreshdebError(f"command failed ({result.returncode}): {' '.join(map(str, args))}\n"
                            f"{result.stdout}{result.stderr}")
    return result.stdout


# ─── Debian version comparison ──────────────────────────────────────────────
# A faithful port of dpkg's verrevcmp, so planning and watching work on hosts
# without dpkg (macOS, minimal CI images). Builds still double-check with
# "dpkg --compare-versions" where dpkg exists.

def _order(char):
    if char.isdigit():
        return 0
    if char.isalpha():
        return ord(char)
    if char == "~":
        return -1
    return ord(char) + 256 if char else 0


def _verrevcmp(a, b):
    i = j = 0
    while i < len(a) or j < len(b):
        first_diff = 0
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = _order(a[i]) if i < len(a) else 0
            bc = _order(b[j]) if j < len(b) else 0
            if ac != bc:
                return ac - bc
            i += 1
            j += 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if first_diff:
            return first_diff
    return 0


def _split_version(version):
    epoch, _, rest = version.rpartition(":") if ":" in version else ("0", "", version)
    upstream, _, revision = rest.rpartition("-") if "-" in rest else (rest, "", "")
    return int(epoch or 0), upstream, revision


def compare_versions(a, b):
    """Negative, zero or positive, like dpkg --compare-versions."""
    ea, ua, ra = _split_version(a)
    eb, ub, rb = _split_version(b)
    if ea != eb:
        return ea - eb
    return _verrevcmp(ua, ub) or _verrevcmp(ra, rb)


# ─── Catalog ────────────────────────────────────────────────────────────────

@dataclass
class Distribution:
    codename: str
    vendor: str
    version: str
    image: str

    @property
    def suffix(self):
        return f"{VENDOR_PREFIX[self.vendor]}{self.version}"

    @property
    def title(self):
        return f"{self.vendor.capitalize()} {self.version} ({self.codename})"


@dataclass
class Config:
    archive: dict
    distributions: dict[str, Distribution]
    architectures: dict[str, dict]

    @property
    def primary_arch(self):
        # Architecture: all packages are built once, on the first architecture.
        return next(iter(self.architectures))


@dataclass
class Track:
    key: str
    tag: str
    commit: str
    revision: int
    eol: dt.date | None = None
    patches: str | None = None
    codenames: list[str] | None = None

    def active(self, today=None):
        return self.eol is None or self.eol >= (today or dt.date.today())


@dataclass
class Package:
    name: str
    path: Path
    data: dict
    tracks: dict[str, Track]
    aliases: dict[str, str]
    overlays: list[dict] = field(default_factory=list)

    @property
    def upstream(self):
        return self.data["upstream"]

    @property
    def epoch(self):
        return self.data.get("epoch")

    @property
    def tag_regex(self):
        return re.compile(self.upstream.get("tag_regex", r"^v?(?P<version>\d+(?:\.\d+)*)$"))

    def upstream_version(self, tag):
        match = self.tag_regex.match(tag)
        require(match, f"{self.name}: tag {tag!r} does not match upstream.tag_regex")
        return match.group("version")

    def component(self, key):
        return f"{self.name}-{key}"

    def components(self):
        """Track components first, then aliases, in manifest order."""
        return [self.component(key) for key in self.tracks] + [self.component(key) for key in self.aliases]

    def codenames(self, config, track):
        names = track.codenames or self.data.get("codenames") or list(config.distributions)
        return [name for name in config.distributions if name in names]

    def version(self, config, track_key, codename):
        track = self.tracks[track_key]
        dist = config.distributions[codename]
        epoch = f"{self.epoch}:" if self.epoch else ""
        return f"{epoch}{self.upstream_version(track.tag)}-{track.revision}+freshdeb~{dist.suffix}"

    def overlays_for(self, track_key, codename):
        for overlay in self.overlays:
            if overlay.get("tracks") and track_key not in overlay["tracks"]:
                continue
            if overlay.get("codenames") and codename not in overlay["codenames"]:
                continue
            yield overlay


def load_config(root=ROOT):
    data = yaml.safe_load((root / "config.yaml").read_text())
    dists = {}
    for codename, entry in data["distributions"].items():
        require(entry["vendor"] in VENDOR_PREFIX, f"config.yaml: unknown vendor for {codename}")
        dists[codename] = Distribution(codename, entry["vendor"], str(entry["version"]), entry["image"])
    archs = data["architectures"]
    require(isinstance(archs, dict) and archs, "config.yaml: architectures must be a non-empty mapping")
    return Config(data["archive"], dists, archs)


def parse_package(name, path, data):
    require(isinstance(data, dict), f"{name}: package.yaml is empty or not a mapping")
    tracks = {}
    for key, entry in (data.get("tracks") or {}).items():
        require(isinstance(key, str), f"{name}: track key {key!r} must be quoted (YAML reads 9.1 as a number)")
        require(isinstance(entry, dict), f"{name}: track {key} must be a mapping")
        eol = entry.get("eol")
        require(eol is None or isinstance(eol, dt.date), f"{name}: track {key} eol must be a YYYY-MM-DD date")
        tracks[key] = Track(key=key, tag=str(entry.get("tag", "")), commit=str(entry.get("commit", "")),
                            revision=entry.get("revision"), eol=eol, patches=entry.get("patches"),
                            codenames=entry.get("codenames"))
    aliases = {}
    for key, target in (data.get("aliases") or {}).items():
        require(isinstance(key, str) and isinstance(target, str),
                f"{name}: alias {key!r} and its target must be quoted strings")
        aliases[key] = target
    return Package(name=name, path=path, data=data, tracks=tracks, aliases=aliases,
                   overlays=list(data.get("overlays") or []))


def load_package(name, root=ROOT):
    path = root / "packages" / name
    require((path / "package.yaml").is_file(), f"unknown package: {name}")
    return parse_package(name, path, yaml.safe_load((path / "package.yaml").read_text()))


def package_names(root=ROOT):
    return sorted(p.parent.name for p in (root / "packages").glob("*/package.yaml"))


def load_packages(root=ROOT):
    return [load_package(name, root) for name in package_names(root)]


# ─── debian/control helpers ─────────────────────────────────────────────────

def control_paragraphs(text):
    """Split debian/control into paragraphs of (field, value) pairs, keeping
    continuation lines verbatim so the file can be written back unchanged."""
    paragraphs = []
    for block in re.split(r"\n\s*\n", text.strip("\n")):
        fields = []
        for line in block.splitlines():
            if line.startswith("#"):
                continue
            if line[:1] in (" ", "\t") and fields:
                fields[-1][1] += "\n" + line
            else:
                key, _, value = line.partition(":")
                fields.append([key.strip(), value.strip() if not value.startswith(" ") else value[1:]])
        if fields:
            paragraphs.append(fields)
    return paragraphs


def render_control(paragraphs):
    return "\n\n".join("\n".join(f"{key}: {value}" if value and not value.startswith("\n") else f"{key}:{value}"
                                 for key, value in fields) for fields in paragraphs) + "\n"


def control_source(debian_dir):
    paragraphs = control_paragraphs((debian_dir / "control").read_text())
    return dict(paragraphs[0]).get("Source", "").strip()


def control_binaries(debian_dir):
    """Binary package names and their Architecture field (udebs excluded)."""
    binaries = {}
    for fields in control_paragraphs((debian_dir / "control").read_text())[1:]:
        values = dict(fields)
        if values.get("Package-Type", "deb").strip() != "deb":
            continue
        binaries[values["Package"].strip()] = " ".join(values.get("Architecture", "any").split())
    return binaries


# ─── validate ───────────────────────────────────────────────────────────────

def validate_package(pkg, config, today=None):
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(f"{pkg.name}: {message}")

    data = pkg.data
    check(NAME_RE.match(pkg.name) is not None, f"invalid package name {pkg.name!r}")
    check(data.get("name") == pkg.name, f"name field must equal the directory name ({pkg.name})")
    for key in ("description", "homepage"):
        check(isinstance(data.get(key), str) and data.get(key), f"missing {key}")
    check(isinstance(data.get("maintainers"), list) and data.get("maintainers"),
          "maintainers must list at least one GitHub handle")
    upstream = data.get("upstream") or {}
    check(isinstance(upstream.get("git"), str) and upstream.get("git", "").startswith("https://"),
          "upstream.git must be an https:// URL")
    try:
        regex = pkg.tag_regex
        check("version" in regex.groupindex, "upstream.tag_regex needs a (?P<version>…) group")
    except re.error as error:
        check(False, f"upstream.tag_regex is not a valid regex: {error}")
        regex = None
    packaging = data.get("packaging") or {}
    check(isinstance(packaging.get("origin"), str), "packaging.origin is required (where debian/ came from)")
    check(COMMIT_RE.match(str(packaging.get("ref", ""))) is not None,
          "packaging.ref must be the full commit the debian/ directory was imported from")
    epoch = data.get("epoch")
    check(epoch is None or (isinstance(epoch, int) and epoch > 0), "epoch must be a positive integer")

    debian = pkg.path / "debian"
    if (debian / "control").is_file():
        check(control_source(debian) == pkg.name, "debian/control Source must equal the package name")
        check(control_binaries(debian), "debian/control declares no binary packages")
    else:
        check(False, "debian/control is missing")
    for name in ("rules", "changelog", "source/format"):
        check((debian / name).is_file(), f"debian/{name} is missing")
    if (debian / "source/format").is_file():
        check((debian / "source/format").read_text().strip() in ("3.0 (quilt)", "3.0 (native)"),
              "debian/source/format must be 3.0 (quilt) or 3.0 (native)")

    check(pkg.tracks, "at least one track is required")
    for key, track in pkg.tracks.items():
        where = f"track {key}"
        check(TRACK_RE.match(key) is not None, f"{where}: invalid key (lowercase letters, digits, dots)")
        check(track.tag != "", f"{where}: tag is required")
        if regex and track.tag:
            match = regex.match(track.tag)
            check(match is not None, f"{where}: tag {track.tag!r} does not match upstream.tag_regex")
            if match and "version" in regex.groupindex:
                version = match.group("version")
                check(UPSTREAM_VERSION_RE.match(version) is not None,
                      f"{where}: upstream version {version!r} is not a valid Debian upstream version")
                if re.fullmatch(r"\d+(\.\d+)*", key):
                    check(version == key or version.startswith(key + "."),
                          f"{where}: tag {track.tag} does not belong to track {key}")
        check(COMMIT_RE.match(track.commit) is not None, f"{where}: commit must be a full 40-character SHA")
        check(isinstance(track.revision, int) and not isinstance(track.revision, bool) and track.revision >= 1,
              f"{where}: revision must be an integer >= 1")
        if track.patches is not None:
            check((pkg.path / track.patches / "series").is_file(),
                  f"{where}: patches directory {track.patches} has no series file")
        if track.codenames is not None:
            check(set(track.codenames) <= set(config.distributions), f"{where}: unknown codename in codenames")

    for key, target in pkg.aliases.items():
        check(TRACK_RE.match(key) is not None, f"alias {key}: invalid key")
        check(key not in pkg.tracks, f"alias {key} collides with a track of the same name")
        check(target in pkg.tracks, f"alias {key} points to unknown track {target}")
        if target in pkg.tracks:
            check(pkg.tracks[target].active(today), f"alias {key} points to end-of-life track {target}")

    if data.get("codenames") is not None:
        check(set(data["codenames"]) <= set(config.distributions), "unknown codename in codenames")

    for index, overlay in enumerate(pkg.overlays):
        where = f"overlay #{index + 1}"
        check(isinstance(overlay, dict) and isinstance(overlay.get("patch"), str), f"{where}: patch is required")
        if isinstance(overlay, dict) and isinstance(overlay.get("patch"), str):
            check((pkg.path / overlay["patch"]).is_file(), f"{where}: {overlay['patch']} does not exist")
            check(set(overlay.get("tracks") or []) <= set(pkg.tracks), f"{where}: unknown track")
            check(set(overlay.get("codenames") or []) <= set(config.distributions), f"{where}: unknown codename")

    build = data.get("build") or {}
    for key in ("options", "profiles"):
        values = build.get(key) or []
        check(isinstance(values, list) and all(isinstance(v, str) and OPTION_RE.match(v) for v in values),
              f"build.{key} must be a list of simple words")

    tests = pkg.path / "tests"
    check((tests / "smoke.sh").is_file(), "tests/smoke.sh is required")
    return errors


def validate_all(root=ROOT, today=None):
    config = load_config(root)
    errors = []
    require(config.primary_arch, "config.yaml: no architectures")
    for arch, entry in config.architectures.items():
        if not (isinstance(entry, dict) and entry.get("runner")):
            errors.append(f"config.yaml: architecture {arch} needs a runner")
    seen = {}
    for name in package_names(root):
        try:
            pkg = load_package(name, root)
        except (FreshdebError, yaml.YAMLError, KeyError, TypeError) as error:
            errors.append(f"{name}: {error}")
            continue
        errors.extend(validate_package(pkg, config, today))
        for component in pkg.components():
            if component in seen:
                errors.append(f"{name}: component {component} is also used by {seen[component]}")
            seen[component] = name
    return errors


def cmd_validate(args):
    errors = validate_all(args.root)
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"OK: {len(package_names(args.root))} package(s) valid")
    return 0


# ─── plan ───────────────────────────────────────────────────────────────────

INFRA_PREFIXES = ("builders/", "scripts/", ".github/workflows/ci.yml")
NON_BUILD_SUFFIXES = (".md",)


def git_show(root, rev, path):
    result = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=root, text=True, capture_output=True)
    return result.stdout if result.returncode == 0 else None


def changed_files(root, base, head):
    out = run("git", "diff", "--name-only", f"{base}...{head}", cwd=root)
    return [line for line in out.splitlines() if line]


def track_fields(track):
    return (track.tag, track.commit, track.revision, track.patches, track.codenames)


def newest_track(keys):
    numeric = [k for k in keys if re.fullmatch(r"\d+(\.\d+)*", k)]
    return max(numeric, key=_VersionKey) if numeric else keys[0]


def plan_changes(root, base, head, today=None):
    """Work out what a diff needs built.

    Returns (selected, errors, alias_changes): selected maps (package, track)
    to {"publish": bool, "codenames": {"*"} or a set}; errors lists packaging
    changes without a version bump; alias_changes says a release is needed
    even if nothing gets built.

    Rules:
      * package.yaml track entry added or changed       -> that track, publish
      * packaging changed (debian/, patches/, overlays/,
        or package.yaml outside tracks/aliases)          -> all active tracks,
                                                            publish; each must
                                                            have a new version
      * only tests/ or docs changed                      -> newest track, test only
      * build infrastructure changed                     -> newest track of every
                                                            package, test only
      * codename added to config.yaml                    -> every active track
                                                            on it, publish
    """
    config = load_config(root)
    files = changed_files(root, base, head)
    selected = {}
    errors = []
    new_codenames = set()
    alias_changes = False

    base_config_text = git_show(root, base, "config.yaml")
    if base_config_text is not None:
        base_dists = set((yaml.safe_load(base_config_text) or {}).get("distributions") or {})
        new_codenames = set(config.distributions) - base_dists

    def select(name, key, publish, codenames=None):
        entry = selected.setdefault((name, key), {"publish": False, "codenames": set()})
        entry["publish"] |= publish
        entry["codenames"] |= set(codenames) if codenames else {"*"}

    infra = any(f.startswith(INFRA_PREFIXES) for f in files)
    for pkg in load_packages(root):
        active = [k for k, t in pkg.tracks.items() if t.active(today)]
        if not active:
            continue
        newest = newest_track(active)
        prefix = f"packages/{pkg.name}/"
        pkg_files = [f[len(prefix):] for f in files if f.startswith(prefix)]
        base_text = git_show(root, base, prefix + "package.yaml")
        base_pkg = parse_package(pkg.name, pkg.path, yaml.safe_load(base_text)) if base_text else None

        if pkg_files and base_pkg is None:
            for key in active:
                select(pkg.name, key, True)
            continue

        packaging = [f for f in pkg_files if f != "package.yaml" and not f.startswith("tests/")
                     and not f.endswith(NON_BUILD_SUFFIXES)]
        if "package.yaml" in pkg_files and base_pkg:
            strip = lambda d: {k: v for k, v in d.items() if k not in ("tracks", "aliases")}
            if strip(pkg.data) != strip(base_pkg.data):
                packaging.append("package.yaml")
            if pkg.aliases != base_pkg.aliases:
                alias_changes = True
            for key in active:
                old = base_pkg.tracks.get(key)
                if old is None or track_fields(old) != track_fields(pkg.tracks[key]):
                    select(pkg.name, key, True)

        if packaging:
            for key in active:
                select(pkg.name, key, True)
                old = base_pkg.tracks.get(key) if base_pkg else None
                if old and (old.tag, old.revision) == (pkg.tracks[key].tag, pkg.tracks[key].revision):
                    errors.append(f"{pkg.name} {key}: packaging changed ({', '.join(sorted(set(packaging)))}) "
                                  f"but tag and revision did not; bump revision so the version changes")
        elif any(f.startswith("tests/") for f in pkg_files):
            select(pkg.name, newest, False)

        if infra:
            select(pkg.name, newest, False)
        if new_codenames:
            for key in active:
                select(pkg.name, key, True, new_codenames)
    return selected, errors, alias_changes


def expand_matrix(root, selected, codename_filter=None, today=None):
    config = load_config(root)
    include = []
    for (name, key), entry in sorted(selected.items()):
        pkg = load_package(name, root)
        track = pkg.tracks[key]
        arch_indep = all(a == "all" for a in control_binaries(pkg.path / "debian").values())
        for codename in pkg.codenames(config, track):
            if "*" not in entry["codenames"] and codename not in entry["codenames"]:
                continue
            if codename_filter and codename not in codename_filter:
                continue
            for arch, arch_entry in config.architectures.items():
                if arch_indep and arch != config.primary_arch:
                    continue
                include.append({
                    "package": name, "track": key, "codename": codename, "arch": arch,
                    "runner": arch_entry["runner"], "image": config.distributions[codename].image,
                    "publish": entry["publish"],
                    "version": pkg.version(config, key, codename),
                })
    require(len(include) <= MAX_MATRIX,
            f"plan has {len(include)} jobs; GitHub allows {MAX_MATRIX}. Split the change or dispatch per package.")
    return include


def cmd_plan(args):
    root = args.root
    today = dt.date.today()
    alias_changes = False
    if args.packages or args.all:
        names = package_names(root) if args.all or args.packages == ["all"] else args.packages
        selected = {}
        for name in names:
            pkg = load_package(name, root)
            keys = [k for k, t in pkg.tracks.items() if t.active(today)]
            if args.tracks and args.tracks != ["all"]:
                unknown = set(args.tracks) - set(keys)
                require(not unknown, f"{name}: unknown or end-of-life track(s): {', '.join(sorted(unknown))}")
                keys = args.tracks
            for key in keys:
                selected[(name, key)] = {"publish": args.publish, "codenames": {"*"}}
        errors = []
    else:
        require(args.base, "--base is required unless --packages/--all is given")
        base = args.base
        if re.fullmatch(r"0{40}", base) or subprocess.run(
                ["git", "cat-file", "-e", f"{base}^{{commit}}"], cwd=root, capture_output=True).returncode:
            # First push or a base we don't have: treat everything as new.
            print(f"plan: base {base} unavailable, planning every active track", file=sys.stderr)
            selected = {}
            for pkg in load_packages(root):
                for key, track in pkg.tracks.items():
                    if track.active(today):
                        selected[(pkg.name, key)] = {"publish": True, "codenames": {"*"}}
            errors = []
        else:
            selected, errors, alias_changes = plan_changes(root, base, args.head, today)
        if not args.publish:
            for entry in selected.values():
                entry["publish"] = False
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    codenames = None if not args.codenames or args.codenames == ["all"] else set(args.codenames)
    include = expand_matrix(root, selected, codenames, today)
    publish = any(job["publish"] for job in include) or (alias_changes and args.publish)
    result = {"matrix": {"include": include}, "count": len(include), "publish": publish}
    text = json.dumps(result, indent=2)
    if args.github_output:
        with open(args.github_output, "a") as fh:
            fh.write(f"matrix={json.dumps(result['matrix'])}\n")
            fh.write(f"count={len(include)}\n")
            fh.write(f"publish={'true' if publish else 'false'}\n")
    print(text)
    for job in include:
        print(f"  {job['package']} {job['track']} {job['codename']}/{job['arch']} "
              f"{job['version']}{' (publish)' if job['publish'] else ''}", file=sys.stderr)
    return 0


# ─── prepare ────────────────────────────────────────────────────────────────

def fetch_upstream(pkg, track, dest):
    url = pkg.upstream["git"]
    refs = run("git", "ls-remote", url, f"refs/tags/{track.tag}", f"refs/tags/{track.tag}^{{}}")
    resolved = {}
    for line in refs.splitlines():
        sha, ref = line.split("\t")
        resolved[ref] = sha
    commit = resolved.get(f"refs/tags/{track.tag}^{{}}") or resolved.get(f"refs/tags/{track.tag}")
    require(commit, f"{pkg.name}: tag {track.tag} not found at {url}")
    require(commit == track.commit,
            f"{pkg.name}: tag {track.tag} now points to {commit}, package.yaml pins {track.commit}. "
            "Upstream moved the tag; investigate before updating the pin.")
    dest.mkdir(parents=True)
    run("git", "init", "-q", str(dest))
    run("git", "-C", str(dest), "fetch", "-q", "--depth", "1", url, f"refs/tags/{track.tag}")
    head = run("git", "-C", str(dest), "rev-parse", "FETCH_HEAD^{commit}").strip()
    require(head == track.commit, f"{pkg.name}: fetched {head}, expected {track.commit}")
    run("git", "-C", str(dest), "-c", "advice.detachedHead=false", "checkout", "-q", head)
    if pkg.upstream.get("submodules"):
        run("git", "-C", str(dest), "submodule", "update", "-q", "--init", "--recursive", "--depth", "1")


def make_orig_tarball(src, tarball, prefix, mtime):
    def normalize(info):
        if "/.git" in info.name or info.name.endswith("/.git"):
            return None
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mtime = mtime
        return info

    with tarfile.open(tarball, "w:gz", compresslevel=6) as tar:
        for path in sorted(src.rglob("*")):
            rel = path.relative_to(src)
            if rel.parts[0] == ".git" or ".git" in rel.parts:
                continue
            tar.add(path, arcname=f"{prefix}/{rel}", recursive=False, filter=normalize)


def rewrite_control(debian_dir, maintainer):
    """Make us the maintainer of the source package and drop Debian's VCS
    pointers: bugs in our builds must come to us, not to Debian."""
    path = debian_dir / "control"
    paragraphs = control_paragraphs(path.read_text())
    source = paragraphs[0]
    dropped = {"Uploaders", "Vcs-Git", "Vcs-Browser", "XSBC-Original-Maintainer"}
    original = next((value for key, value in source if key == "Maintainer"), None)
    kept = []
    for key, value in source:
        if key in dropped:
            continue
        if key == "Maintainer":
            kept.append(["Maintainer", maintainer])
            if original and original.strip() != maintainer:
                kept.append(["XSBC-Original-Maintainer", original.strip()])
            continue
        kept.append([key, value])
    paragraphs[0] = kept
    path.write_text(render_control(paragraphs))


def changelog_entry(pkg, config, track, codename, version, when):
    dist = config.distributions[codename]
    packaging = pkg.data["packaging"]
    lines = [
        f"{pkg.name} ({version}) {codename}; urgency=medium",
        "",
        f"  * freshdeb build of {pkg.name} {pkg.upstream_version(track.tag)} for {dist.title}.",
        f"  * Upstream: {pkg.upstream['git']} tag {track.tag}",
        f"    (commit {track.commit}).",
        f"  * Packaging: {packaging['origin']} at {packaging['ref'][:12]},",
        f"    freshdeb revision {track.revision}.",
        "",
        f" -- {config.archive['maintainer']}  {email.utils.formatdate(when).replace('-0000', '+0000')}",
    ]
    return "\n".join(lines) + "\n"


def prepare(root, name, key, codename, arch, out, publish=False, when=None):
    config = load_config(root)
    pkg = load_package(name, root)
    require(key in pkg.tracks, f"{name}: unknown track {key}")
    require(codename in config.distributions, f"unknown codename {codename}")
    require(arch in config.architectures, f"unknown architecture {arch}")
    track = pkg.tracks[key]
    require(codename in pkg.codenames(config, track), f"{name} {key} is not built for {codename}")
    upstream_version = pkg.upstream_version(track.tag)
    version = pkg.version(config, key, codename)
    when = when if when is not None else int(os.environ.get("SOURCE_DATE_EPOCH", "0")) or int(dt.datetime.now().timestamp())

    out.mkdir(parents=True, exist_ok=True)
    srcdir = out / f"{name}-{upstream_version}"
    require(not srcdir.exists(), f"{srcdir} already exists")
    fetch_upstream(pkg, track, srcdir)
    shutil.rmtree(srcdir / ".git")
    if (srcdir / "debian").exists():
        print(f"  upstream ships a debian/ directory; replacing it with ours")
        shutil.rmtree(srcdir / "debian")

    make_orig_tarball(srcdir, out / f"{name}_{upstream_version}.orig.tar.gz", f"{name}-{upstream_version}", when)

    shutil.copytree(pkg.path / "debian", srcdir / "debian", symlinks=True)
    if track.patches:
        shutil.rmtree(srcdir / "debian/patches", ignore_errors=True)
        shutil.copytree(pkg.path / track.patches, srcdir / "debian/patches", symlinks=True)
    applied = []
    for overlay in pkg.overlays_for(key, codename):
        patch = (pkg.path / overlay["patch"]).resolve()
        run("patch", "-p1", "--forward", "--no-backup-if-mismatch", "--fuzz=0", "-i", str(patch), cwd=srcdir)
        applied.append(overlay["patch"])

    rewrite_control(srcdir / "debian", config.archive["maintainer"])
    changelog = srcdir / "debian/changelog"
    changelog.write_text(changelog_entry(pkg, config, track, codename, version, when) + "\n" + changelog.read_text())

    binaries = control_binaries(srcdir / "debian")
    build = pkg.data.get("build") or {}
    info = {
        "package": name,
        "track": key,
        "codename": codename,
        "arch": arch,
        "version": version,
        "upstream_version": upstream_version,
        "tag": track.tag,
        "commit": track.commit,
        "component": pkg.component(key),
        "image": config.distributions[codename].image,
        "source_dir": srcdir.name,
        "binaries": binaries,
        "build_options": build.get("options") or [],
        "build_profiles": build.get("profiles") or [],
        "build_arch_indep": arch == config.primary_arch,
        "overlays": applied,
        "publish": bool(publish),
        "skipped": False,
        "skip_reason": None,
        "debs": [],
    }
    (out / "build-info.json").write_text(json.dumps(info, indent=2) + "\n")
    print(f"Prepared {name} {version} in {srcdir}")
    for overlay in applied:
        print(f"  overlay: {overlay}")
    return info


def cmd_prepare(args):
    prepare(args.root, args.package, args.track, args.codename, args.arch, args.out.resolve(),
            publish=args.publish)
    return 0


# ─── guard ──────────────────────────────────────────────────────────────────

def distro_versions(binary):
    """Every version of a binary package the configured apt sources offer."""
    result = subprocess.run(["apt-cache", "madison", binary], text=True, capture_output=True)
    versions = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) >= 2 and parts[0] == binary and parts[1] not in versions:
            versions.append(parts[1])
    return versions


def cmd_guard(args):
    """Mark the build as skipped when the distribution already ships the same
    or a newer version; publishing it would downgrade or shadow users."""
    info_path = args.info
    info = json.loads(info_path.read_text())
    ours = info["version"]
    blockers = []
    for binary in info["binaries"]:
        versions = distro_versions(binary)
        if not versions:
            continue
        theirs = max(versions, key=_VersionKey)
        print(f"  distro ships {binary} {', '.join(versions)}")
        if shutil.which("dpkg"):
            # dpkg is the authority; our port must agree with it.
            newer = subprocess.run(["dpkg", "--compare-versions", ours, "gt", theirs]).returncode == 0
            require(newer == (compare_versions(ours, theirs) > 0),
                    f"version comparison disagrees with dpkg for {ours} vs {theirs}")
        if compare_versions(ours, theirs) <= 0:
            blockers.append(f"{binary} {theirs}")
    if blockers:
        info["skipped"] = True
        info["skip_reason"] = (f"{info['codename']} already ships {', '.join(blockers)}, "
                               f"not older than {ours}")
        print(f"SKIP: {info['skip_reason']}")
    else:
        print(f"OK: {ours} is newer than anything {info['codename']} ships")
    info_path.write_text(json.dumps(info, indent=2) + "\n")
    return 0


# ─── watch ──────────────────────────────────────────────────────────────────

def remote_tags(url):
    tags = {}
    for line in run("git", "ls-remote", "--tags", url).splitlines():
        sha, ref = line.split("\t")
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            tags[name[:-3]] = sha  # peeled commit of an annotated tag wins
        else:
            tags.setdefault(name, sha)
    return tags


def numeric_prefix(version, depth):
    parts = version.split(".")
    if len(parts) < depth or not all(p.isdigit() for p in parts[:depth]):
        return None
    return ".".join(parts[:depth])


def watch_package(pkg, tags, today=None):
    """Return proposed changes for one package given {tag: commit}."""
    releases = {}
    for tag, sha in tags.items():
        match = pkg.tag_regex.match(tag)
        if match:
            releases[match.group("version")] = (tag, sha)
    changes = []
    active = {k: t for k, t in pkg.tracks.items() if t.active(today)}
    numeric_tracks = [k for k in pkg.tracks if re.fullmatch(r"\d+(\.\d+)*", k)]

    for key, track in active.items():
        current = pkg.upstream_version(track.tag)
        candidates = [v for v in releases if (v == key or v.startswith(key + ".")) or not re.fullmatch(r"\d+(\.\d+)*", key)]
        if not candidates:
            continue
        newest = max(candidates, key=lambda v: _VersionKey(v))
        if compare_versions(newest, current) > 0:
            tag, sha = releases[newest]
            changes.append({"package": pkg.name, "kind": "bump", "track": key, "from": track.tag,
                            "tag": tag, "commit": sha})

    if numeric_tracks:
        depth = len(numeric_tracks[0].split("."))
        known = set(numeric_tracks)
        highest = max(numeric_tracks, key=_VersionKey)
        new_lines = {}
        for version in releases:
            line = numeric_prefix(version, depth)
            if line and line not in known and compare_versions(line, highest) > 0:
                if line not in new_lines or compare_versions(version, new_lines[line]) > 0:
                    new_lines[line] = version
        for line, version in sorted(new_lines.items(), key=lambda item: _VersionKey(item[0])):
            tag, sha = releases[version]
            change = {"package": pkg.name, "kind": "new-track", "track": line, "tag": tag, "commit": sha,
                      "based_on": highest, "aliases": {}}
            for alias in pkg.aliases:
                if re.fullmatch(r"\d+(\.\d+)*", alias) and (line + ".").startswith(alias + "."):
                    change["aliases"][alias] = line
            major = line.split(".")[0]
            alias_depths = {len(a.split(".")) for a in pkg.aliases if re.fullmatch(r"\d+(\.\d+)*", a)}
            if alias_depths == {1} and major not in pkg.aliases and depth > 1:
                change["aliases"][major] = line
            changes.append(change)
    return changes


class _VersionKey:
    def __init__(self, version):
        self.version = version

    def __lt__(self, other):
        return compare_versions(self.version, other.version) < 0

    def __eq__(self, other):
        return compare_versions(self.version, other.version) == 0


def yq_string(value):
    return json.dumps(value)


def apply_change(root, change):
    """Edit package.yaml with yq so comments and layout survive."""
    pkg = load_package(change["package"], root)
    path = pkg.path / "package.yaml"
    key = yq_string(change["track"])
    if change["kind"] == "bump":
        expr = (f'.tracks[{key}].tag = {yq_string(change["tag"])} | '
                f'.tracks[{key}].commit = {yq_string(change["commit"])} | '
                f'.tracks[{key}].revision = 1')
    else:
        base = pkg.tracks[change["based_on"]]
        entry = {"tag": change["tag"], "commit": change["commit"], "revision": 1}
        if base.patches:
            new_dir = f"patches/{change['track']}"
            src = pkg.path / base.patches
            if not (pkg.path / new_dir).exists():
                shutil.copytree(src, pkg.path / new_dir)
            entry["patches"] = new_dir
        expr = f".tracks = ({{{key}: {json.dumps(entry)}}} + .tracks)"
        for alias, target in change["aliases"].items():
            expr += f" | .aliases[{yq_string(alias)}] = {yq_string(target)}"
    run("yq", "-i", expr, str(path))
    # yq writes "9.1" keys quoted only if needed; force string keys to stay quoted.
    text = path.read_text()
    text = re.sub(r'^(\s+)(\d+(?:\.\d+)+):', r'\1"\2":', text, flags=re.M)
    text = re.sub(r'^(\s+)(\d+): ', r'\1"\2": ', text, flags=re.M)
    path.write_text(text)


def cmd_watch(args):
    root = args.root
    names = args.packages or package_names(root)
    changes = []
    for name in names:
        pkg = load_package(name, root)
        if not pkg.data.get("watch", True):
            continue
        changes.extend(watch_package(pkg, remote_tags(pkg.upstream["git"])))
    if args.tracks:
        changes = [c for c in changes if c["track"] in args.tracks]
    if args.apply:
        for change in changes:
            apply_change(root, change)
    print(json.dumps(changes, indent=2))
    return 0


def autopkgtest_plan(control_text):
    """Turn debian/tests/control into runnable entries for container-verify.sh.

    We run Debian's autopkgtests against the installed packages in a
    throwaway container, without the autopkgtest tool. Tests that need things
    a plain container can't give (a full machine, a reboot) are reported as
    skipped instead of failing.
    """
    unsupported = {"isolation-machine", "needs-reboot", "breaks-testbed-machine"}
    entries = []
    for fields in control_paragraphs(control_text):
        values = {key.lower(): " ".join(value.split()) for key, value in fields}
        restrictions = set(re.split(r"[\s,]+", values.get("restrictions", ""))) - {""}
        depends = [d.strip() for d in values.get("depends", "@").split(",") if d.strip()]
        extra = [d for d in depends if d not in ("@", "@builddeps@")]
        names = values.get("tests", "").replace(",", " ").split()
        commands = [(name, f"debian/tests/{name}") for name in names]
        if "test-command" in values:
            commands = [("command" + str(len(entries) + 1), values["test-command"])]
        for name, command in commands:
            entries.append({
                "name": name,
                "command": command,
                "allow_stderr": "allow-stderr" in restrictions,
                "skip": ", ".join(sorted(restrictions & unsupported)) or None,
                "depends": extra,
            })
    return entries


def cmd_autopkgtest_plan(args):
    control = args.tests_dir / "control"
    entries = autopkgtest_plan(control.read_text()) if control.is_file() else []
    with open(args.output, "w") as fh:
        for entry in entries:
            # name, allow_stderr, skip reason, extra deps, command (last: may contain tabs? no)
            fh.write("\t".join([entry["name"], "1" if entry["allow_stderr"] else "0", entry["skip"] or "-",
                                 " ".join(entry["depends"]) or "-", entry["command"]]) + "\n")
    print(f"{len(entries)} autopkgtest(s)")
    return 0


def cmd_components(args):
    for pkg in load_packages(args.root):
        for component in pkg.components():
            print(component)
    return 0


def cmd_info(args):
    config = load_config(args.root)
    pkg = load_package(args.package, args.root)
    print(json.dumps({
        "package": pkg.name,
        "tracks": {k: {"tag": t.tag, "active": t.active(), "component": pkg.component(k),
                       "codenames": pkg.codenames(config, t),
                       "versions": {c: pkg.version(config, k, c) for c in pkg.codenames(config, t)}}
                   for k, t in pkg.tracks.items()},
        "aliases": {k: {"target": v, "component": pkg.component(k)} for k, v in pkg.aliases.items()},
        "binaries": control_binaries(pkg.path / "debian"),
    }, indent=2))
    return 0


# ─── CLI ────────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate").set_defaults(func=cmd_validate)

    p = sub.add_parser("plan")
    p.add_argument("--base")
    p.add_argument("--head", default="HEAD")
    p.add_argument("--all", action="store_true", help="every package and active track")
    p.add_argument("--packages", nargs="*")
    p.add_argument("--tracks", nargs="*")
    p.add_argument("--codenames", nargs="*")
    p.add_argument("--publish", action="store_true")
    p.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("prepare")
    p.add_argument("--package", required=True)
    p.add_argument("--track", required=True)
    p.add_argument("--codename", required=True)
    p.add_argument("--arch", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--publish", action="store_true")
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("guard")
    p.add_argument("--info", type=Path, required=True)
    p.set_defaults(func=cmd_guard)

    p = sub.add_parser("watch")
    p.add_argument("--packages", nargs="*")
    p.add_argument("--tracks", nargs="*", help="only changes for these tracks (with --apply: one PR each)")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_watch)

    sub.add_parser("components").set_defaults(func=cmd_components)

    p = sub.add_parser("autopkgtest-plan")
    p.add_argument("tests_dir", type=Path)
    p.add_argument("output", type=Path)
    p.set_defaults(func=cmd_autopkgtest_plan)

    p = sub.add_parser("info")
    p.add_argument("package")
    p.set_defaults(func=cmd_info)

    args = parser.parse_args(argv)
    args.root = args.root.resolve()
    try:
        return args.func(args)
    except FreshdebError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
