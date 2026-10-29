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
  components      Print every project and APT component, one per line.

Only the Python standard library and PyYAML are required. "prepare" and
"guard" also need git, patch and dpkg, which the builder image provides.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]*$")
TRACK_RE = re.compile(r"^[0-9a-z][0-9a-z.]*$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TARBALL_SUFFIXES = (".tar.gz", ".tar.xz", ".tar.bz2")
ASSET_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
NUMERIC_RE = re.compile(r"^\d+(\.\d+)*$")
ASSETS_DIR = "freshdeb-assets"
# packaging.origin for packages whose debian/ is written and kept here
# (when Debian has none to import).
OWN_PACKAGING = "freshdeb"
OPTION_RE = re.compile(r"^[a-z0-9][a-z0-9=_.,-]*$")
UPSTREAM_VERSION_RE = re.compile(r"^[0-9][A-Za-z0-9.+~]*$")
VENDOR_PREFIX = {"debian": "deb", "ubuntu": "ubuntu"}
# Capabilities a package's tests may add to their containers (tests.capabilities),
# e.g. NET_ADMIN for keepalived to add its virtual IPs.
TEST_CAPABILITIES = ("NET_ADMIN", "NET_RAW")
# The alias that always names a package's newest track; watch moves it to
# each new track it proposes.
LATEST_ALIAS = "latest"

# The keys package.yaml takes, per mapping (docs/packages.md): a typo
# would otherwise be ignored, an overlay's "codename:" apply everywhere.
PACKAGE_KEYS = {
    "package": {"name", "description", "homepage", "watch", "upstream", "packaging", "epoch", "codenames",
                "build", "tests", "tracks", "aliases", "overlays", "companions"},
    "upstream": {"git", "tag_regex", "tarball", "assets", "submodules"},
    "packaging": {"origin", "ref", "source"},
    "build": {"go", "options", "profiles", "prepare"},
    "track": {"tag", "commit", "revision", "eol", "patches", "codenames", "sha256", "go", "line", "assets",
              "companions"},
    "overlay": {"patch", "tracks", "codenames"},
    "companion upstream": {"git", "tag_regex", "tarball", "submodules"},
    "pin": {"tag", "commit", "sha256", "patches"},
}

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


# Network failures upstream (a git server's HTTP 502) shouldn't fail a
# build or a watch run: retry them a few times.
NETWORK_ATTEMPTS = 3
NETWORK_RETRY_DELAY = 10


def run_network(*args, cwd=None):
    """run() for commands that talk to upstream (git ls-remote, git fetch)."""
    for attempt in range(1, NETWORK_ATTEMPTS + 1):
        try:
            return run(*args, cwd=cwd)
        except FreshdebError as error:
            if attempt == NETWORK_ATTEMPTS:
                raise
            print(f"retrying ({attempt}/{NETWORK_ATTEMPTS - 1}) after: {str(error).splitlines()[0]}", file=sys.stderr)
            time.sleep(NETWORK_RETRY_DELAY * attempt)



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
    toolchains: dict = field(default_factory=dict)  # {"go": {"1.25.14": {"amd64": sha256, …}}}

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
    sha256: str | None = None  # release tarball checksum, for upstream.tarball packages
    go: str | None = None      # Go toolchain version (config.yaml toolchains.go) for Go packages
    line: str | None = None    # version prefix a named track follows ("3.13" for "lts")
    assets: dict[str, str] | None = None  # {asset name: sha256} for upstream.assets
    companions: dict[str, "CompanionPin"] = field(default_factory=dict)  # {companion name: its pin}

    def active(self, today=None):
        return self.eol is None or self.eol >= (today or dt.date.today())

    def follows(self, version):
        """Whether an upstream version belongs to this track: numeric keys
        and line are version prefixes, other tracks follow every release."""
        prefix = self.key if NUMERIC_RE.match(self.key) else self.line
        return prefix is None or version == prefix or version.startswith(prefix + ".")


@dataclass
class Package:
    name: str
    path: Path
    data: dict
    tracks: dict[str, Track]
    aliases: dict[str, str]
    overlays: list[dict] = field(default_factory=list)
    companions: dict[str, "Companion"] = field(default_factory=dict)

    @property
    def upstream(self):
        return self.data["upstream"]

    @property
    def epoch(self):
        return self.data.get("epoch")

    @property
    def go_mode(self):
        """How a track's go: toolchain is used: "modules" builds a Go program
        (vendored modules, offline Go build), "bootstrap" only provides the
        compiler that builds Go itself (GOROOT_BOOTSTRAP)."""
        return (self.data.get("build") or {}).get("go", "modules")

    @property
    def source_name(self):
        """Source: in the imported debian/control. Usually the package name;
        packaging.source when Debian's differs (golang-1.27 for golang). The
        build renames it to the package name."""
        return (self.data.get("packaging") or {}).get("source") or self.name

    @property
    def tag_regex(self):
        return re.compile(self.upstream.get("tag_regex", r"^v?(?P<version>\d+(?:\.\d+)*)$"))

    def tarball_url(self, version):
        """Release tarball URL for packages that build from upstream's
        tarballs (generated configure scripts etc.), or None for git."""
        template = self.upstream.get("tarball")
        return template.format(version=version) if template else None

    @property
    def assets(self):
        """{name: URL template} of extra release files (upstream.assets)."""
        return self.upstream.get("assets") or {}

    def asset_path(self, name):
        """Where prepare puts an asset, relative to the source tree."""
        url = self.assets[name]
        suffix = next((s for s in TARBALL_SUFFIXES if url.endswith(s)), "")
        return f"{ASSETS_DIR}/{name}{suffix}"

    def upstream_version(self, tag):
        match = self.tag_regex.match(tag)
        require(match, f"{self.name}: tag {tag!r} does not match upstream.tag_regex")
        return match.group("version")

    def component(self, key):
        """APT component of a track or alias in this package's repository
        (deb.freshdeb.com/<name>): "v9.1", "v9"; non-numeric keys as is."""
        return f"v{key}" if key[:1].isdigit() else key

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
        yield from matching_overlays(self.overlays, track_key, codename)

    def companions_for(self, track_key, codename):
        """(Companion, CompanionPin) pairs a track builds on a codename."""
        for name, pin in sorted(self.tracks[track_key].companions.items()):
            companion = self.companions[name]
            if companion.builds_on(codename):
                yield companion, pin

    def companion_version(self, config, track_key, codename, name):
        """A companion's binaries are versioned <its upstream>+<package version>,
        Debian's convention for extras tied to one build (ZNC's znc-push)."""
        pin = self.tracks[track_key].companions[name]
        upstream = self.companions[name].upstream_version(pin.tag)
        return f"{upstream}+{self.version(config, track_key, codename)}"


def matching_overlays(overlays, track_key, codename):
    for overlay in overlays:
        if overlay.get("tracks") and track_key not in overlay["tracks"]:
            continue
        if overlay.get("codenames") and codename not in overlay["codenames"]:
            continue
        yield overlay


@dataclass
class CompanionPin:
    tag: str
    commit: str
    sha256: str | None = None   # release tarball checksum, for companions with upstream.tarball
    patches: str | None = None  # replaces the companion's debian/patches, relative to the package


@dataclass
class Companion:
    """Another source package that a package builds in the same job, against
    its fresh build, and publishes in its repository: modules that need the
    package's exact ABI (proftpd's proftpd-mod-*). Its debian/ is imported
    to packages/<package>/companions/<name>/debian."""
    name: str
    path: Path
    data: dict

    @property
    def upstream(self):
        return self.data.get("upstream") or {}

    @property
    def packaging(self):
        return self.data.get("packaging") or {}

    @property
    def source_name(self):
        return self.packaging.get("source") or self.name

    @property
    def tag_regex(self):
        return re.compile(self.upstream.get("tag_regex", r"^v?(?P<version>\d+(?:\.\d+)*)$"))

    def tarball_url(self, version):
        template = self.upstream.get("tarball")
        return template.format(version=version) if template else None

    def upstream_version(self, tag):
        match = self.tag_regex.match(tag)
        require(match, f"{self.name}: tag {tag!r} does not match its upstream.tag_regex")
        return match.group("version")

    def builds_on(self, codename):
        codenames = self.data.get("codenames")
        return codenames is None or codename in codenames

    def overlays_for(self, track_key, codename):
        yield from matching_overlays(self.data.get("overlays") or [], track_key, codename)


def load_config(root=ROOT):
    data = yaml.safe_load((root / "config.yaml").read_text())
    dists = {}
    for codename, entry in data["distributions"].items():
        require(entry["vendor"] in VENDOR_PREFIX, f"config.yaml: unknown vendor for {codename}")
        dists[codename] = Distribution(codename, entry["vendor"], str(entry["version"]), entry["image"])
    archs = data["architectures"]
    require(isinstance(archs, dict) and archs, "config.yaml: architectures must be a non-empty mapping")
    toolchains = {kind: {str(v): dict(sums) for v, sums in (versions or {}).items()}
                  for kind, versions in (data.get("toolchains") or {}).items()}
    return Config(data["archive"], dists, archs, toolchains)


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
                            codenames=entry.get("codenames"),
                            sha256=str(entry["sha256"]) if entry.get("sha256") is not None else None,
                            go=str(entry["go"]) if entry.get("go") is not None else None,
                            line=str(entry["line"]) if entry.get("line") is not None else None,
                            assets=({str(k): str(v) for k, v in entry["assets"].items()}
                                    if isinstance(entry.get("assets"), dict) else entry.get("assets")),
                            companions=parse_companion_pins(name, key, entry.get("companions")))
    aliases = {}
    for key, target in (data.get("aliases") or {}).items():
        require(isinstance(key, str) and isinstance(target, str),
                f"{name}: alias {key!r} and its target must be quoted strings")
        aliases[key] = target
    companions = data.get("companions") or {}
    require(isinstance(companions, dict), f"{name}: companions must map source package names to settings")
    return Package(name=name, path=path, data=data, tracks=tracks, aliases=aliases,
                   overlays=list(data.get("overlays") or []),
                   companions={str(c): Companion(str(c), path / "companions" / str(c),
                                                 entry if isinstance(entry, dict) else {})
                               for c, entry in companions.items()})


def parse_companion_pins(name, key, pins):
    if pins is None:
        return {}
    require(isinstance(pins, dict), f"{name}: track {key} companions must map companion names to pins")
    parsed = {}
    for companion, pin in pins.items():
        require(isinstance(pin, dict), f"{name}: track {key} companion {companion} must be a mapping")
        parsed[str(companion)] = CompanionPin(
            tag=str(pin.get("tag", "")), commit=str(pin.get("commit", "")),
            sha256=str(pin["sha256"]) if pin.get("sha256") is not None else None,
            patches=pin.get("patches"))
    return parsed


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
    known_keys(data, "package", "package.yaml", check)
    for section in ("upstream", "packaging", "build"):
        known_keys(data.get(section), section, section, check)
    for key, entry in (data.get("tracks") or {}).items():
        known_keys(entry, "track", f"track {key}", check)
        for name, pin in ((entry.get("companions") if isinstance(entry, dict) else None) or {}).items():
            known_keys(pin, "pin", f"track {key} companion {name}", check)
    for index, overlay in enumerate(pkg.overlays):
        known_keys(overlay, "overlay", f"overlay #{index + 1}", check)
    for where, values in [("codenames", data.get("codenames")),
                          *((f"track {k} codenames", t.codenames) for k, t in pkg.tracks.items()),
                          *((f"overlay #{i + 1} {field}", o.get(field)) for i, o in enumerate(pkg.overlays)
                            if isinstance(o, dict) for field in ("tracks", "codenames"))]:
        check(values is None or (isinstance(values, list) and values),
              f"{where} must be a non-empty list (leave it out for all)")
    components = pkg.components()
    duplicates = sorted({c for c in components if components.count(c) > 1})
    check(not duplicates, f"tracks and aliases share the component(s) {', '.join(duplicates)}")
    check(NAME_RE.match(pkg.name) is not None, f"invalid package name {pkg.name!r}")
    check(data.get("name") == pkg.name, f"name field must equal the directory name ({pkg.name})")
    for key in ("description", "homepage"):
        check(isinstance(data.get(key), str) and data.get(key), f"missing {key}")
    upstream = data.get("upstream") or {}
    check(isinstance(upstream.get("git"), str) and upstream.get("git", "").startswith("https://"),
          "upstream.git must be an https:// URL")
    tarball = upstream.get("tarball")
    if tarball is not None:
        check(isinstance(tarball, str) and tarball.startswith("https://") and "{version}" in tarball
              and tarball.endswith(TARBALL_SUFFIXES),
              "upstream.tarball must be an https:// URL containing {version} and ending in "
              + "/".join(TARBALL_SUFFIXES))
    assets = upstream.get("assets")
    if assets is not None:
        check(isinstance(assets, dict) and assets, "upstream.assets must map asset names to URLs")
        for name, url in (assets.items() if isinstance(assets, dict) else []):
            check(isinstance(name, str) and ASSET_RE.match(name) is not None,
                  f"upstream.assets: invalid name {name!r} (lowercase letters, digits, dashes)")
            check(isinstance(url, str) and url.startswith("https://") and "{version}" in url,
                  f"upstream.assets {name}: must be an https:// URL containing {{version}}")
        # No package needs both; release tarballs carry their generated files.
        check(tarball is None, "upstream.assets can't be combined with upstream.tarball")
    asset_names = set(assets) if isinstance(assets, dict) else set()
    try:
        regex = pkg.tag_regex
        check("version" in regex.groupindex, "upstream.tag_regex needs a (?P<version>…) group")
    except re.error as error:
        check(False, f"upstream.tag_regex is not a valid regex: {error}")
        regex = None
    packaging = data.get("packaging") or {}
    check(isinstance(packaging.get("origin"), str), "packaging.origin is required (where debian/ came from)")
    ref = str(packaging.get("ref", ""))
    if packaging.get("origin") == OWN_PACKAGING:
        # debian/ is maintained here: nothing upstream of it to point at.
        check("ref" not in packaging, "packaging.ref is only for imported packaging (origin is freshdeb)")
        check("source" not in packaging, "packaging.source is only for imported packaging (origin is freshdeb)")
    else:
        check(COMMIT_RE.match(ref) is not None or re.fullmatch(r"sha256:[0-9a-f]{64}", ref) is not None,
              "packaging.ref must be the full commit debian/ was imported from, or sha256:<checksum> of the "
              "Debian source package's .debian.tar.* when its packaging isn't kept in a VCS")
    source = packaging.get("source")
    check(source is None or (isinstance(source, str) and NAME_RE.match(source) is not None and source != pkg.name),
          "packaging.source must be Debian's source name, when it differs from the package name")
    epoch = data.get("epoch")
    check(epoch is None or (isinstance(epoch, int) and epoch > 0), "epoch must be a positive integer")

    debian = pkg.path / "debian"
    if (debian / "control").is_file():
        if pkg.source_name == pkg.name:
            check(control_source(debian) == pkg.name, "debian/control Source must equal the package name")
        else:
            check(control_source(debian) == pkg.source_name,
                  f"debian/control Source must equal packaging.source ({pkg.source_name})")
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
                if NUMERIC_RE.match(key) or track.line is not None:
                    check(track.follows(version),
                          f"{where}: tag {track.tag} does not belong to track {key}"
                          + (f" (line {track.line})" if track.line else ""))
        if track.line is not None:
            check(not NUMERIC_RE.match(key), f"{where}: line is only for named tracks; {key} is a line already")
            check(NUMERIC_RE.match(track.line) is not None, f"{where}: line must be a version prefix like \"3.13\"")
        check(COMMIT_RE.match(track.commit) is not None, f"{where}: commit must be a full 40-character SHA")
        if track.go is not None:
            sums = config.toolchains.get("go", {}).get(track.go)
            check(sums is not None, f"{where}: Go {track.go} is not in config.yaml toolchains.go")
            if sums is not None:
                check(all(SHA256_RE.match(str(sums.get(arch, ""))) for arch in config.architectures),
                      f"{where}: config.yaml toolchains.go {track.go} needs a sha256 for every architecture")
        if tarball is not None:
            check(SHA256_RE.match(track.sha256 or "") is not None,
                  f"{where}: sha256 of the release tarball is required (upstream.tarball is set)")
        else:
            check(track.sha256 is None, f"{where}: sha256 is only used with upstream.tarball")
        if asset_names:
            pinned = track.assets if isinstance(track.assets, dict) else {}
            check(set(pinned) == asset_names and all(SHA256_RE.match(v) for v in pinned.values()),
                  f"{where}: assets needs the sha256 of every upstream asset ({', '.join(sorted(asset_names))})")
        else:
            check(track.assets is None, f"{where}: assets is only used with upstream.assets")
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
        # An end-of-life target is fine: its component stays published as it is.
        check(target in pkg.tracks, f"alias {key} points to unknown track {target}")

    if data.get("codenames") is not None:
        check(set(data["codenames"]) <= set(config.distributions), "unknown codename in codenames")

    for index, overlay in enumerate(pkg.overlays):
        where = f"overlay #{index + 1}"
        check(isinstance(overlay, dict) and isinstance(overlay.get("patch"), str), f"{where}: patch is required")
        if isinstance(overlay, dict) and isinstance(overlay.get("patch"), str):
            check((pkg.path / overlay["patch"]).is_file(), f"{where}: {overlay['patch']} does not exist")
            check(set(overlay.get("tracks") or []) <= set(pkg.tracks), f"{where}: unknown track")
            check(set(overlay.get("codenames") or []) <= set(config.distributions), f"{where}: unknown codename")

    errors += validate_companions(pkg, config, check)

    build = data.get("build") or {}
    hook = build.get("prepare")
    if hook is not None:
        check(isinstance(hook, str) and (pkg.path / hook).is_file(), f"build.prepare: {hook} does not exist")
    check(pkg.go_mode in ("modules", "bootstrap"), "build.go must be modules or bootstrap")
    if pkg.go_mode == "bootstrap":
        check(all(track.go for track in pkg.tracks.values()),
              "build.go bootstrap: every track needs a go: toolchain to bootstrap with")
    for key in ("options", "profiles"):
        values = build.get(key) or []
        check(isinstance(values, list) and all(isinstance(v, str) and OPTION_RE.match(v) for v in values),
              f"build.{key} must be a list of simple words")

    tests = pkg.path / "tests"
    check((tests / "smoke.sh").is_file(), "tests/smoke.sh is required")
    test_config = data.get("tests") or {}
    check(isinstance(test_config, dict) and set(test_config) <= {"capabilities", "upgrade_from"},
          "tests only takes capabilities and upgrade_from")
    capabilities = test_config.get("capabilities") or [] if isinstance(test_config, dict) else []
    check(isinstance(capabilities, list) and set(capabilities) <= set(TEST_CAPABILITIES),
          f"tests.capabilities must be a list of {', '.join(TEST_CAPABILITIES)}")
    upgrade_from = test_config.get("upgrade_from") if isinstance(test_config, dict) else None
    if upgrade_from is not None:
        ok = isinstance(upgrade_from, dict) and set(upgrade_from) == {"url", "sha256"}
        check(ok, "tests.upgrade_from needs url and sha256 (and nothing else)")
        if ok:
            url, sums = upgrade_from["url"], upgrade_from["sha256"]
            check(isinstance(url, str) and url.startswith("https://") and "{arch}" in url and url.endswith(".deb"),
                  "tests.upgrade_from.url must be an https:// URL of a .deb with an {arch} placeholder")
            check(isinstance(sums, dict) and set(sums) == set(config.architectures)
                  and all(isinstance(v, str) and SHA256_RE.match(v) for v in sums.values()),
                  "tests.upgrade_from.sha256 needs a sha256 for every architecture")
    return errors


def known_keys(mapping, kind, where, check):
    if isinstance(mapping, dict):
        extra = sorted(str(key) for key in set(mapping) - PACKAGE_KEYS[kind])
        check(not extra, f"{where}: unknown key(s) {', '.join(extra)}")


def validate_upstream(upstream, check, where):
    """upstream.git, tarball and tag_regex; returns the compiled regex or None."""
    check(isinstance(upstream.get("git"), str) and upstream.get("git", "").startswith("https://"),
          f"{where}upstream.git must be an https:// URL")
    tarball = upstream.get("tarball")
    if tarball is not None:
        check(isinstance(tarball, str) and tarball.startswith("https://") and "{version}" in tarball
              and tarball.endswith(TARBALL_SUFFIXES),
              f"{where}upstream.tarball must be an https:// URL containing {{version}} and ending in "
              + "/".join(TARBALL_SUFFIXES))
    try:
        regex = re.compile(upstream.get("tag_regex", r"^v?(?P<version>\d+(?:\.\d+)*)$"))
    except re.error as error:
        check(False, f"{where}upstream.tag_regex is not a valid regex: {error}")
        return None
    check("version" in regex.groupindex, f"{where}upstream.tag_regex needs a (?P<version>…) group")
    return regex if "version" in regex.groupindex else None


def validate_companions(pkg, config, check):
    """companions: (their settings, imported debian/) and the tracks' pins.
    Returns no errors itself; check() collects them."""
    data = pkg.data
    if data.get("companions") is not None:
        check(isinstance(data["companions"], dict) and data["companions"],
              "companions must map source package names to their settings")
        check(data.get("epoch") is None,
              "a package with companions can't have an epoch (their versions end in +<package version>)")
    debian = pkg.path / "debian"
    seen = set(control_binaries(debian)) if (debian / "control").is_file() else set()
    for name, companion in pkg.companions.items():
        where = f"companion {name}: "
        check(NAME_RE.match(name) is not None and name != pkg.name, f"{where}invalid source package name")
        check(set(companion.data) <= {"upstream", "packaging", "codenames", "overlays"},
              f"{where}takes upstream, packaging, codenames and overlays")
        validate_upstream(companion.upstream, check, where)
        known_keys(companion.data.get("upstream"), "companion upstream", f"{where}upstream", check)
        known_keys(companion.data.get("packaging"), "packaging", f"{where}packaging", check)
        packaging = companion.packaging
        check(isinstance(packaging.get("origin"), str) and packaging.get("origin") != OWN_PACKAGING,
              f"{where}packaging.origin is required (where debian/ came from)")
        ref = str(packaging.get("ref", ""))
        check(COMMIT_RE.match(ref) is not None or re.fullmatch(r"sha256:[0-9a-f]{64}", ref) is not None,
              f"{where}packaging.ref must be the full commit debian/ was imported from")
        cdebian = companion.path / "debian"
        if (cdebian / "control").is_file():
            check(control_source(cdebian) == companion.source_name,
                  f"{where}debian/control Source must be {companion.source_name}")
            binaries = set(control_binaries(cdebian))
            check(binaries, f"{where}debian/control declares no binary packages")
            check(not binaries & seen, f"{where}binary packages {sorted(binaries & seen)} are built twice")
            seen |= binaries
        else:
            check(False, f"{where}companions/{name}/debian/control is missing")
        for file in ("rules", "changelog", "source/format"):
            check((cdebian / file).is_file(), f"{where}companions/{name}/debian/{file} is missing")
        codenames = companion.data.get("codenames")
        check(codenames is None or (isinstance(codenames, list) and codenames
                                    and set(codenames) <= set(config.distributions)),
              f"{where}codenames must be a non-empty list of known codenames")
        for index, overlay in enumerate(companion.data.get("overlays") or []):
            ok = isinstance(overlay, dict) and isinstance(overlay.get("patch"), str)
            check(ok, f"{where}overlay #{index + 1}: patch is required")
            known_keys(overlay, "overlay", f"{where}overlay #{index + 1}", check)
            if ok:
                check((pkg.path / overlay["patch"]).is_file(), f"{where}{overlay['patch']} does not exist")
                check(set(overlay.get("tracks") or []) <= set(pkg.tracks), f"{where}overlay: unknown track")
                check(set(overlay.get("codenames") or []) <= set(config.distributions),
                      f"{where}overlay: unknown codename")
    for key, track in pkg.tracks.items():
        # Not building one means apt keeps the package back for its users;
        # limit it with codenames instead, where it can't build.
        unpinned = sorted(set(pkg.companions) - set(track.companions))
        check(not unpinned, f"track {key}: companions needs a pin for every companion ({', '.join(unpinned)})")
        for name, pin in track.companions.items():
            where = f"track {key} companion {name}: "
            companion = pkg.companions.get(name)
            check(companion is not None, f"{where}not declared under companions")
            if companion is None:
                continue
            regex = validate_upstream(companion.upstream, lambda *_: None, "")
            match = regex.match(pin.tag) if regex else None
            check(match is not None, f"{where}tag {pin.tag!r} does not match its upstream.tag_regex")
            if match:
                check(UPSTREAM_VERSION_RE.match(match.group("version")) is not None,
                      f"{where}upstream version {match.group('version')!r} is not a valid Debian upstream version")
            check(COMMIT_RE.match(pin.commit) is not None, f"{where}commit must be a full 40-character SHA")
            if companion.upstream.get("tarball"):
                check(SHA256_RE.match(pin.sha256 or "") is not None, f"{where}sha256 of the release tarball is required")
            else:
                check(pin.sha256 is None, f"{where}sha256 is only used with upstream.tarball")
            if pin.patches is not None:
                check((pkg.path / pin.patches / "series").is_file(), f"{where}{pin.patches} has no series file")
    return []


def validate_all(root=ROOT, today=None):
    config = load_config(root)
    errors = []
    require(config.primary_arch, "config.yaml: no architectures")
    for arch, entry in config.architectures.items():
        if not (isinstance(entry, dict) and entry.get("runner")):
            errors.append(f"config.yaml: architecture {arch} needs a runner")
    for name in package_names(root):
        try:
            pkg = load_package(name, root)
        except (FreshdebError, yaml.YAMLError, KeyError, TypeError) as error:
            errors.append(f"{name}: {error}")
            continue
        errors.extend(validate_package(pkg, config, today))
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

# How much a planned track builds, weakest first:
#   canary  build machinery changed: one job, no upstream test suite
#   tests   only the package's own tests changed: every codename/arch, no
#           upstream test suite (upstream's code didn't change)
#   full    a release, or a manual rebuild: everything
SCOPES = ("canary", "tests", "full")

# package.yaml keys that never end up in a .deb: changing them needs no build.
METADATA_KEYS = ("description", "homepage", "watch")

INFRA_PREFIXES = ("builders/", "scripts/", ".github/workflows/ci.yml")
NON_BUILD_SUFFIXES = (".md",)


def git_show(root, rev, path):
    result = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=root, text=True, capture_output=True)
    return result.stdout if result.returncode == 0 else None


def changed_files(root, base, head):
    out = run("git", "diff", "--name-only", f"{base}...{head}", cwd=root)
    return [line for line in out.splitlines() if line]


# Track fields that change what gets built from the same tag: changing one
# needs a new revision, or the release finds the version published already.
BUILD_FIELDS = ("commit", "patches", "sha256", "go", "assets")


def file_tracks(rel, manifests, keys):
    """The tracks (of keys) that a changed file under packages/<name>/ can
    affect, going by what references it in package.yaml now or before:
    a patches directory its tracks (and companion pins), an overlay the
    tracks it is scoped to, companions/<n>/ the tracks that pin <n>.
    Anything else, or a file nothing references, affects every track."""
    def within(directory):
        return bool(directory) and (rel + "/").startswith(directory.rstrip("/") + "/")

    hits, referenced = set(), False
    for pkg in manifests:
        if rel.startswith("companions/"):
            name = rel.split("/")[1]
            referenced = referenced or name in pkg.companions
            hits |= {k for k in keys if k in pkg.tracks and name in pkg.tracks[k].companions}
            continue
        for key, track in pkg.tracks.items():
            if within(track.patches) or any(within(pin.patches) for pin in track.companions.values()):
                referenced = True
                hits |= {key} & set(keys)
        entries = [*pkg.overlays, *(o for c in pkg.companions.values() for o in c.data.get("overlays") or [])]
        for entry in entries:
            if isinstance(entry, dict) and entry.get("patch") == rel:
                referenced = True
                hits |= {k for k in keys if not entry.get("tracks") or k in entry["tracks"]}
    return hits if referenced else set(keys)


def track_overlays(pkg, key):
    """The overlay entries (package and companions) a track applies."""
    entries = [*pkg.overlays, *(o for c in pkg.companions.values() for o in c.data.get("overlays") or [])]
    return [o for o in entries if not (isinstance(o, dict) and o.get("tracks")) or key in o["tracks"]]


def newest_track(keys):
    numeric = [k for k in keys if re.fullmatch(r"\d+(\.\d+)*", k)]
    return max(numeric, key=_VersionKey) if numeric else keys[0]


def plan_changes(root, base, head, today=None, package_bases=None):
    """Work out what a diff needs built.

    package_bases maps a package to the commit it was last released from;
    its own files are diffed from there, so a failed or skipped release is
    planned again. Everything else is diffed from base.

    Returns (selected, errors, alias_changes): selected maps (package, track)
    to {"publish": bool, "codenames": {"*"} or a set}; errors lists packaging
    changes without a version bump; alias_changes says a release is needed
    even if nothing gets built.

    Rules:
      * package.yaml track entry added or changed       -> that track, publish;
                                                            commit, patches,
                                                            sha256, go or assets
                                                            need a new version
      * track or package codenames added                 -> those codenames
      * packaging changed                                -> the tracks it affects
                                                            (file_tracks), publish;
                                                            each must have a new
                                                            version
      * only tests/ or tests: changed                    -> newest track, test only,
                                                            no upstream test suite
      * build infrastructure changed                     -> newest track of every
                                                            package, one job each,
                                                            no upstream test suite
      * codename added to config.yaml                    -> every active track
                                                            on it, publish
    """
    config = load_config(root)
    files = changed_files(root, base, head)
    package_bases = package_bases or {}
    diffs = {base: files}
    selected = {}
    errors = []
    new_codenames = set()
    alias_changes = False

    base_config_text = git_show(root, base, "config.yaml")
    if base_config_text is not None:
        base_dists = set((yaml.safe_load(base_config_text) or {}).get("distributions") or {})
        new_codenames = set(config.distributions) - base_dists

    def select(name, key, publish, codenames=None, scope="full"):
        entry = selected.setdefault((name, key), {"publish": False, "codenames": set(), "scope": scope})
        entry["publish"] |= publish
        entry["codenames"] |= set(codenames) if codenames else {"*"}
        entry["scope"] = max(entry["scope"], scope, key=SCOPES.index)

    infra = any(f.startswith(INFRA_PREFIXES) for f in files)
    for pkg in load_packages(root):
        active = [k for k, t in pkg.tracks.items() if t.active(today)]
        if not active:
            continue
        newest = newest_track(active)
        prefix = f"packages/{pkg.name}/"
        pkg_base = package_bases.get(pkg.name, base)
        if pkg_base not in diffs:
            diffs[pkg_base] = changed_files(root, pkg_base, head)
        pkg_files = [f[len(prefix):] for f in diffs[pkg_base] if f.startswith(prefix)]
        base_text = git_show(root, pkg_base, prefix + "package.yaml")
        base_pkg = parse_package(pkg.name, pkg.path, yaml.safe_load(base_text)) if base_text else None

        if pkg_files and base_pkg is None:
            for key in active:
                select(pkg.name, key, True)
            continue

        changed = {}  # track -> what changed in its build
        tests_changed = any(f.startswith("tests/") for f in pkg_files)
        for f in pkg_files:
            if f != "package.yaml" and not f.startswith("tests/") and not f.endswith(NON_BUILD_SUFFIXES):
                for key in file_tracks(f, (pkg, base_pkg), active):
                    changed.setdefault(key, set()).add(f)
        if "package.yaml" in pkg_files and base_pkg:
            # Keys handled one by one below; the rest affects every track.
            own = ("tracks", "aliases", "tests", "overlays", "companions", "codenames", *METADATA_KEYS)
            strip = lambda d: {k: v for k, v in d.items() if k not in own}
            if strip(pkg.data) != strip(base_pkg.data):
                for key in active:
                    changed.setdefault(key, set()).add("package.yaml")
            tests_changed |= pkg.data.get("tests") != base_pkg.data.get("tests")
            if pkg.aliases != base_pkg.aliases:
                alias_changes = True
            settings = lambda p, name: p.companions[name].data if name in p.companions else None
            for key in active:
                new, old = pkg.tracks[key], base_pkg.tracks.get(key)
                if old is None:
                    select(pkg.name, key, True)
                    continue
                if track_overlays(pkg, key) != track_overlays(base_pkg, key):
                    changed.setdefault(key, set()).add("package.yaml overlays")
                for name, pin in new.companions.items():
                    old_pin = old.companions.get(name)
                    if settings(pkg, name) != settings(base_pkg, name):
                        changed.setdefault(key, set()).add(f"package.yaml companion {name}")
                    elif old_pin and old_pin.tag == pin.tag and old_pin != pin:
                        # Same companion version: the build must change it.
                        changed.setdefault(key, set()).add(f"track {key} companion {name}")
                fields = [f for f in BUILD_FIELDS if getattr(old, f) != getattr(new, f)]
                if fields:
                    changed.setdefault(key, set()).add(f"track {key} {', '.join(fields)}")
                if (old.tag, old.revision) != (new.tag, new.revision) or old.companions != new.companions:
                    select(pkg.name, key, True)
                added = set(pkg.codenames(config, new)) - set(base_pkg.codenames(config, old))
                if added:
                    select(pkg.name, key, True, added)

        for key in sorted(changed):
            select(pkg.name, key, True)
            old = base_pkg.tracks.get(key) if base_pkg else None
            if old and (old.tag, old.revision) == (pkg.tracks[key].tag, pkg.tracks[key].revision):
                errors.append(f"{pkg.name} {key}: packaging changed ({', '.join(sorted(changed[key]))}) "
                              f"but tag and revision did not; bump revision so the version changes")
        if tests_changed:
            select(pkg.name, newest, False, scope="tests")

        if infra:
            select(pkg.name, newest, False, scope="canary")
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
        scope = entry.get("scope", "full")
        codenames = pkg.codenames(config, track)
        if scope == "canary":
            codenames = codenames[:1]  # the oldest Debian release, unless the package skips it
        for codename in codenames:
            if "*" not in entry["codenames"] and codename not in entry["codenames"]:
                continue
            if codename_filter and codename not in codename_filter:
                continue
            for arch, arch_entry in config.architectures.items():
                if (arch_indep or scope == "canary") and arch != config.primary_arch:
                    continue
                include.append({
                    "package": name, "track": key, "codename": codename, "arch": arch,
                    "runner": arch_entry["runner"], "image": config.distributions[codename].image,
                    "publish": entry["publish"],
                    "nocheck": scope != "full",
                    "version": pkg.version(config, key, codename),
                })
    require(len(include) <= MAX_MATRIX,
            f"plan has {len(include)} jobs; GitHub allows {MAX_MATRIX}. Split the change or dispatch per package.")
    return include


def released_bases(root, path, head):
    """{package: commit} from the registry (deb.freshdeb.com/status.json),
    keeping only commits this checkout has as ancestors of head."""
    if not path or not Path(path).is_file() or not Path(path).read_text().strip():
        return {}
    try:
        projects = json.loads(Path(path).read_text()).get("projects") or {}
    except (json.JSONDecodeError, AttributeError):
        print(f"plan: ignoring unreadable {path}", file=sys.stderr)
        return {}
    bases = {}
    for name, entry in projects.items():
        commit = (entry or {}).get("commit")
        if commit and re.fullmatch(r"[0-9a-f]{7,40}", commit) and subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit, head], cwd=root, capture_output=True).returncode == 0:
            bases[name] = commit
            print(f"plan: {name} last released from {commit[:12]}", file=sys.stderr)
    return bases


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
                selected[(name, key)] = {"publish": args.publish, "codenames": {"*"}, "scope": "full"}
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
                        selected[(pkg.name, key)] = {"publish": True, "codenames": {"*"}, "scope": "full"}
            errors = []
        else:
            selected, errors, alias_changes = plan_changes(root, base, args.head, today,
                                                           released_bases(root, args.released, args.head))
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

def check_tag(pkg, track):
    """The release tag must still point to the pinned commit."""
    check_tag_pin(pkg.name, pkg.upstream["git"], track.tag, track.commit)


def check_tag_pin(label, url, tag, pinned):
    refs = run_network("git", "ls-remote", url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}")
    resolved = {}
    for line in refs.splitlines():
        sha, ref = line.split("\t")
        resolved[ref] = sha
    commit = resolved.get(f"refs/tags/{tag}^{{}}") or resolved.get(f"refs/tags/{tag}")
    require(commit, f"{label}: tag {tag} not found at {url}")
    require(commit == pinned,
            f"{label}: tag {tag} now points to {commit}, package.yaml pins {pinned}. "
            "Upstream moved the tag; investigate before updating the pin.")


def download(url, dest):
    """Download url to dest; returns its sha256. Retries server errors, rate
    limits, dropped connections and cut-off bodies; other client errors (a
    404) fail right away."""
    request = urllib.request.Request(url, headers={"User-Agent": "freshdeb (+https://github.com/blendbyte/freshdeb)"})
    for attempt in range(1, NETWORK_ATTEMPTS + 1):
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(request, timeout=120) as response, open(dest, "wb") as fh:
                size = 0
                for chunk in iter(lambda: response.read(1 << 20), b""):
                    digest.update(chunk)
                    fh.write(chunk)
                    size += len(chunk)
                # read() returns what it got when a connection closes early.
                length = response.headers.get("Content-Length")
                if length and length.isdigit() and int(length) != size:
                    raise http.client.IncompleteRead(b"", int(length) - size)
            return digest.hexdigest()
        except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
            client_error = isinstance(error, urllib.error.HTTPError) and error.code < 500 and error.code != 429
            if client_error or attempt == NETWORK_ATTEMPTS:
                raise
            print(f"retrying ({attempt}/{NETWORK_ATTEMPTS - 1}) {url} after: {error}", file=sys.stderr)
            time.sleep(NETWORK_RETRY_DELAY * attempt)


def download_verified(url, dest, expected):
    """download() until the file has the pinned sha256, a few times: a
    runner once got a corrupted toolchain from a CDN. Returns the last
    digest; the caller fails if it still doesn't match."""
    for attempt in range(1, NETWORK_ATTEMPTS + 1):
        digest = download(url, dest)
        if digest == expected or attempt == NETWORK_ATTEMPTS:
            return digest
        print(f"retrying ({attempt}/{NETWORK_ATTEMPTS - 1}) {url}: sha256 {digest}, expected {expected}",
              file=sys.stderr)
        time.sleep(NETWORK_RETRY_DELAY * attempt)


def fetch_tarball(pkg, track, orig, dest):
    """Download the release tarball as the orig tarball, verify it, unpack it."""
    fetch_release_tarball(pkg.name, pkg.tarball_url(pkg.upstream_version(track.tag)), track.sha256, orig, dest)


def fetch_release_tarball(label, url, sha256, orig, dest):
    digest = download_verified(url, orig, sha256)
    require(digest == sha256, f"{label}: {url} has sha256 {digest}, package.yaml pins {sha256}")
    dest.mkdir(parents=True)
    run("tar", "-xf", str(orig), "-C", str(dest), "--strip-components=1")
    require(any(dest.iterdir()), f"{label}: {url} unpacked to nothing")


def fetch_assets(pkg, track, srcdir):
    """Download the track's release assets (upstream.assets, e.g. a prebuilt
    web UI) into the source tree, each verified against its pinned sha256."""
    version = pkg.upstream_version(track.tag)
    fetched = []
    for name, template in pkg.assets.items():
        url = template.format(version=version)
        dest = srcdir / pkg.asset_path(name)
        require(not dest.exists(), f"{pkg.name}: {dest} already exists in the source tree")
        dest.parent.mkdir(parents=True, exist_ok=True)
        expected = track.assets[name]
        digest = download_verified(url, dest, expected)
        require(digest == expected, f"{pkg.name}: {url} has sha256 {digest}, package.yaml pins {expected}")
        print(f"  asset {name}: {url} (sha256 ok)")
        fetched.append({"name": name, "url": url, "sha256": digest, "path": pkg.asset_path(name)})
    return fetched


GO_DOWNLOAD = "https://go.dev/dl/go{version}.linux-{arch}.tar.gz"


def install_go(config, version, arch, dest):
    """Install the pinned Go toolchain into dest/go; returns its GOROOT."""
    expected = config.toolchains["go"][version][arch]
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / f"go{version}.linux-{arch}.tar.gz"
    digest = download_verified(GO_DOWNLOAD.format(version=version, arch=arch), archive, expected)
    require(digest == expected, f"Go {version} for {arch} has sha256 {digest}, config.yaml pins {expected}")
    run("tar", "-xzf", str(archive), "-C", str(dest))
    archive.unlink()
    goroot = dest / "go"
    reported = run(str(goroot / "bin/go"), "env", "GOVERSION").strip()
    require(reported == f"go{version}", f"installed Go reports {reported}, expected go{version}")
    return goroot


def go_env(goroot, cache, offline):
    """Environment for the go tool: our toolchain only, caches under cache."""
    env = {
        "PATH": f"{goroot}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "GOTOOLCHAIN": "local",          # never let go.mod switch to a downloaded toolchain
        "GOWORK": "off",                 # vendor and build the main module only, not a go.work workspace
        "GOPATH": f"{cache}/gopath",
        "GOMODCACHE": f"{cache}/gopath/pkg/mod",
        "GOCACHE": f"{cache}/gocache",
        "GOTELEMETRY": "off",
    }
    if offline:
        env.update({"GOFLAGS": "-mod=vendor", "GOPROXY": "off"})
    return env


def go_bootstrap_env(goroot):
    """Environment for building Go itself: the pinned toolchain as
    GOROOT_BOOTSTRAP, never a downloaded one, no module proxy."""
    return {
        "GOROOT_BOOTSTRAP": str(goroot),
        "GOTOOLCHAIN": "local",
        "GOPROXY": "off",
        "GOTELEMETRY": "off",
    }


def vendor_go_modules(srcdir, env):
    """Fetch dependencies, verify them against go.sum and the Go checksum
    database (sum.golang.org), and vendor them so the build runs offline."""
    require((srcdir / "go.mod").is_file(), f"{srcdir} has no go.mod")
    full = {**os.environ, **env, "GOFLAGS": "-mod=mod"}
    for command in (["go", "mod", "download"], ["go", "mod", "verify"], ["go", "mod", "vendor"]):
        result = subprocess.run(command, cwd=srcdir, env=full, text=True, capture_output=True)
        require(result.returncode == 0, f"{' '.join(command)} failed:\n{result.stdout}{result.stderr}")
        print(f"  {' '.join(command)}: ok")


def run_prepare_hook(pkg, track, srcdir, hook):
    """Run a package's prepare hook in the unpacked source tree, with network
    access. Hooks fetch extra sources a build would otherwise download (e.g.
    bundled dependencies), and must verify every download against a pinned
    checksum: the build itself runs without them checking anything."""
    script = (pkg.path / hook).resolve()
    command = [sys.executable, str(script)] if script.suffix == ".py" else ["bash", str(script)]
    env = {**os.environ, "FRESHDEB_PACKAGE": pkg.name, "FRESHDEB_TRACK": track.key,
           "FRESHDEB_UPSTREAM_VERSION": pkg.upstream_version(track.tag)}
    result = subprocess.run([*command, str(srcdir)], cwd=srcdir, env=env, text=True, capture_output=True)
    print(result.stdout, end="")
    require(result.returncode == 0, f"{pkg.name}: prepare hook {hook} failed:\n{result.stdout}{result.stderr}")


def fetch_upstream(pkg, track, dest):
    fetch_git(pkg.name, pkg.upstream["git"], track.tag, track.commit, dest, pkg.upstream.get("submodules"))


def fetch_git(label, url, tag, commit, dest, submodules=False):
    dest.mkdir(parents=True)
    run("git", "init", "-q", str(dest))
    run_network("git", "-C", str(dest), "fetch", "-q", "--depth", "1", url, f"refs/tags/{tag}")
    head = run("git", "-C", str(dest), "rev-parse", "FETCH_HEAD^{commit}").strip()
    require(head == commit, f"{label}: fetched {head}, expected {commit}")
    run("git", "-C", str(dest), "-c", "advice.detachedHead=false", "checkout", "-q", head)
    if submodules:
        run_network("git", "-C", str(dest), "submodule", "update", "-q", "--init", "--recursive", "--depth", "1")


def rewrite_control(debian_dir, maintainer, source_name=None):
    """Make us the maintainer of the source package and drop Debian's VCS
    pointers: bugs in our builds must come to us, not to Debian.

    Also default Priority to optional. Current Debian packaging may omit it
    (dpkg >= 1.22 fills it in), but older dpkg then builds .debs without a
    Priority, and reprepro refuses those.

    With source_name, Source becomes that name: the package name, when
    Debian's differs (packaging.source), so control and our changelog entry
    agree."""
    path = debian_dir / "control"
    paragraphs = control_paragraphs(path.read_text())
    source = paragraphs[0]
    dropped = {"Uploaders", "Vcs-Git", "Vcs-Browser", "XSBC-Original-Maintainer"}
    original = next((value for key, value in source if key == "Maintainer"), None)
    kept = []
    for key, value in source:
        if key in dropped:
            continue
        if key == "Source" and source_name:
            kept.append(["Source", source_name])
            continue
        if key == "Maintainer":
            kept.append(["Maintainer", maintainer])
            if original and original.strip() != maintainer:
                kept.append(["XSBC-Original-Maintainer", original.strip()])
            continue
        kept.append([key, value])
    if not any(key == "Priority" for key, _ in kept):
        after = next((i + 1 for i, (key, _) in enumerate(kept) if key == "Section"), 1)
        kept.insert(after, ["Priority", "optional"])
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
        f"    (commit {track.commit}).",  # debian/rules may read it from here (prometheus)
        *([f"  * Release tarball: {pkg.tarball_url(pkg.upstream_version(track.tag))}",
           f"    (sha256 {track.sha256})."] if track.sha256 else []),
        *[line for name, template in pkg.assets.items()
          for line in (f"  * Release asset {name}: {template.format(version=pkg.upstream_version(track.tag))}",
                       f"    (sha256 {track.assets[name]}).")],
        *([f"  * Built with Go {track.go}, dependencies vendored and verified",
           "    against go.sum and sum.golang.org."] if track.go and pkg.go_mode == "modules" else []),
        *([f"  * Bootstrapped with Go {track.go} from go.dev."] if track.go and pkg.go_mode == "bootstrap" else []),
        (f"  * Packaging: maintained in freshdeb (packages/{pkg.name}/debian),"
         if packaging["origin"] == OWN_PACKAGING else
         f"  * Packaging: {packaging['origin']} at {packaging['ref'].removeprefix('sha256:')[:12]},"),
        f"    freshdeb revision {track.revision}.",
        "",
        f" -- {config.archive['maintainer']}  {email.utils.formatdate(when).replace('-0000', '+0000')}",
    ]
    return "\n".join(lines) + "\n"


def companion_changelog_entry(pkg, config, companion, pin, codename, version, package_version, when):
    dist = config.distributions[codename]
    packaging = companion.packaging
    lines = [
        f"{companion.name} ({version}) {codename}; urgency=medium",
        "",
        f"  * freshdeb build of {companion.name} {companion.upstream_version(pin.tag)} for {dist.title},",
        f"    against {pkg.name} {package_version}.",
        f"  * Upstream: {companion.upstream['git']} tag {pin.tag}",
        f"    (commit {pin.commit}).",
        *([f"  * Release tarball: {companion.tarball_url(companion.upstream_version(pin.tag))}",
           f"    (sha256 {pin.sha256})."] if pin.sha256 else []),
        f"  * Packaging: {packaging['origin']} at {str(packaging['ref']).removeprefix('sha256:')[:12]}.",
        "",
        f" -- {config.archive['maintainer']}  {email.utils.formatdate(when).replace('-0000', '+0000')}",
    ]
    return "\n".join(lines) + "\n"


def prepare_companions(pkg, config, key, codename, version, out, when):
    """Source trees of the companions a track builds on a codename, under
    out/companions/; container-build.sh builds them after the package."""
    prepared = []
    for companion, pin in pkg.companions_for(key, codename):
        label = f"{pkg.name} companion {companion.name}"
        upstream = companion.upstream_version(pin.tag)
        cversion = pkg.companion_version(config, key, codename, companion.name)
        orig_version = cversion.rsplit("-", 1)[0]  # dpkg's upstream part: <companion>+<package upstream>
        cdir = out / "companions"
        cdir.mkdir(exist_ok=True)
        srcdir = cdir / f"{companion.name}-{orig_version}"
        require(not srcdir.exists(), f"{srcdir} already exists")
        check_tag_pin(label, companion.upstream["git"], pin.tag, pin.commit)
        tarball = companion.tarball_url(upstream)
        if tarball:
            suffix = next(s for s in TARBALL_SUFFIXES if tarball.endswith(s))
            fetch_release_tarball(label, tarball, pin.sha256, cdir / f"{companion.name}_{orig_version}.orig{suffix}",
                                  srcdir)
        else:
            fetch_git(label, companion.upstream["git"], pin.tag, pin.commit, srcdir,
                      companion.upstream.get("submodules"))
            shutil.rmtree(srcdir / ".git")
        if (srcdir / "debian").exists():
            shutil.rmtree(srcdir / "debian")
        shutil.copytree(companion.path / "debian", srcdir / "debian", symlinks=True)
        if pin.patches:
            shutil.rmtree(srcdir / "debian/patches", ignore_errors=True)
            shutil.copytree(pkg.path / pin.patches, srcdir / "debian/patches", symlinks=True)
        applied = []
        for overlay in companion.overlays_for(key, codename):
            patch = (pkg.path / overlay["patch"]).resolve()
            run("patch", "-p1", "--forward", "--no-backup-if-mismatch", "--fuzz=0", "-i", str(patch), cwd=srcdir)
            applied.append(overlay["patch"])
        rewrite_control(srcdir / "debian", config.archive["maintainer"], source_name=companion.name)
        changelog = srcdir / "debian/changelog"
        changelog.write_text(companion_changelog_entry(pkg, config, companion, pin, codename, cversion, version, when)
                             + "\n" + changelog.read_text())
        prepared.append({
            "name": companion.name, "version": cversion, "upstream_version": upstream, "tag": pin.tag,
            "commit": pin.commit, "source_dir": f"companions/{srcdir.name}",
            "binaries": control_binaries(srcdir / "debian"), "overlays": applied,
        })
        print(f"Prepared companion {companion.name} {cversion}")
    return prepared


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
    check_tag(pkg, track)
    tarball = pkg.tarball_url(upstream_version)
    if tarball:
        suffix = next(s for s in TARBALL_SUFFIXES if tarball.endswith(s))
        fetch_tarball(pkg, track, out / f"{name}_{upstream_version}.orig{suffix}", srcdir)
    else:
        fetch_upstream(pkg, track, srcdir)
        shutil.rmtree(srcdir / ".git")
    if (srcdir / "debian").exists():
        print(f"  upstream ships a debian/ directory; replacing it with ours")
        shutil.rmtree(srcdir / "debian")
    build = pkg.data.get("build") or {}
    if build.get("prepare"):
        run_prepare_hook(pkg, track, srcdir, build["prepare"])
    assets = fetch_assets(pkg, track, srcdir)
    build_env = {}
    if track.go:
        goroot = install_go(config, track.go, arch, out / "toolchain")
        if pkg.go_mode == "bootstrap":
            # Go itself: nothing to vendor, the toolchain only bootstraps it.
            build_env = go_bootstrap_env(goroot)
        else:
            vendor_go_modules(srcdir, go_env(goroot, out / "cache", offline=False))
            build_env = go_env(goroot, out / "cache", offline=True)

    shutil.copytree(pkg.path / "debian", srcdir / "debian", symlinks=True)
    if track.patches:
        shutil.rmtree(srcdir / "debian/patches", ignore_errors=True)
        shutil.copytree(pkg.path / track.patches, srcdir / "debian/patches", symlinks=True)
    applied = []
    for overlay in pkg.overlays_for(key, codename):
        patch = (pkg.path / overlay["patch"]).resolve()
        run("patch", "-p1", "--forward", "--no-backup-if-mismatch", "--fuzz=0", "-i", str(patch), cwd=srcdir)
        applied.append(overlay["patch"])

    rewrite_control(srcdir / "debian", config.archive["maintainer"], source_name=name)
    changelog = srcdir / "debian/changelog"
    changelog.write_text(changelog_entry(pkg, config, track, codename, version, when) + "\n" + changelog.read_text())

    binaries = control_binaries(srcdir / "debian")
    binary_versions = {binary: version for binary in binaries}
    companions = prepare_companions(pkg, config, key, codename, version, out, when)
    for companion in companions:
        binaries.update(companion["binaries"])
        binary_versions.update({binary: companion["version"] for binary in companion["binaries"]})
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
        "binary_versions": binary_versions,
        "companions": companions,
        "build_options": build.get("options") or [],
        "build_profiles": build.get("profiles") or [],
        "build_env": build_env,
        "test_capabilities": (pkg.data.get("tests") or {}).get("capabilities") or [],
        "test_upgrade_from": test_upgrade_from(pkg, arch),
        "assets": assets,
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


def test_upgrade_from(pkg, arch):
    """The upstream package the upgrade test starts from when the
    distributions ship none (tests.upgrade_from): {url, sha256} for this
    architecture, or None."""
    upgrade_from = (pkg.data.get("tests") or {}).get("upgrade_from")
    if not upgrade_from:
        return None
    return {"url": upgrade_from["url"].format(arch=arch), "sha256": upgrade_from["sha256"][arch]}


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
    blockers = []
    for binary in info["binaries"]:
        # Companions' binaries carry their own (derived) versions.
        ours = info.get("binary_versions", {}).get(binary, info["version"])
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
            blockers.append(f"{binary} {theirs} (ours {ours})")
    if blockers:
        info["skipped"] = True
        info["skip_reason"] = f"{info['codename']} already ships the same or newer: {', '.join(blockers)}"
        print(f"SKIP: {info['skip_reason']}")
    else:
        print(f"OK: {info['version']} is newer than anything {info['codename']} ships")
    info_path.write_text(json.dumps(info, indent=2) + "\n")
    return 0


# ─── watch ──────────────────────────────────────────────────────────────────

def remote_tags(url):
    tags = {}
    for line in run_network("git", "ls-remote", "--tags", url).splitlines():
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


def upstream_releases(pkg, tags):
    """{version: (tag, commit)} for the tags that match the package's tag_regex."""
    releases = {}
    for tag, sha in tags.items():
        match = pkg.tag_regex.match(tag)
        if match:
            releases[match.group("version")] = (tag, sha)
    return releases


def watch_package(pkg, tags, today=None):
    """Return proposed changes for one package given {tag: commit}."""
    releases = upstream_releases(pkg, tags)
    changes = []
    active = {k: t for k, t in pkg.tracks.items() if t.active(today)}
    numeric_tracks = [k for k in pkg.tracks if re.fullmatch(r"\d+(\.\d+)*", k)]

    for key, track in active.items():
        current = pkg.upstream_version(track.tag)
        candidates = [v for v in releases if track.follows(v)]
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
            # "latest" always names the newest track.
            if LATEST_ALIAS in pkg.aliases:
                change["aliases"][LATEST_ALIAS] = line
            changes.append(change)
    return changes


def companion_releases(companion, tags):
    """{version: (tag, commit)} for a companion's tags."""
    releases = {}
    for tag, sha in tags.items():
        match = companion.tag_regex.match(tag)
        if match:
            releases[match.group("version")] = (tag, sha)
    return releases


def watch_companions(pkg, companion_tags, today=None):
    """Changes for companions with a newer upstream release, per track that
    pins them. Their binaries' versions are <companion>+<package version>,
    so a new companion release needs no revision bump."""
    changes = []
    for key, track in pkg.tracks.items():
        if not track.active(today):
            continue
        for name, pin in sorted(track.companions.items()):
            companion = pkg.companions[name]
            releases = companion_releases(companion, companion_tags.get(name, {}))
            if not releases:
                continue
            newest = max(releases, key=_VersionKey)
            if compare_versions(newest, companion.upstream_version(pin.tag)) > 0:
                tag, sha = releases[newest]
                changes.append({"package": pkg.name, "kind": "companion", "track": key, "companion": name,
                                "from": pin.tag, "tag": tag, "commit": sha})
    return changes


def companion_report(pkg, companion_tags, changes, today=None):
    lines = []
    for key, track in pkg.tracks.items():
        if not track.active(today):
            continue
        for name, pin in sorted(track.companions.items()):
            companion = pkg.companions[name]
            releases = companion_releases(companion, companion_tags.get(name, {}))
            newest = max(releases, key=_VersionKey) if releases else None
            change = next((c for c in changes if c["kind"] == "companion" and c["track"] == key
                           and c["companion"] == name), None)
            decision = (f"update to {change['tag']}" if change else
                        "up to date" if newest else "no upstream release matches tag_regex")
            lines.append(f"  {pkg.name} {key}: companion {name} ours {companion.upstream_version(pin.tag)}, "
                         f"upstream {newest or '-'} -> {decision}")
    return lines


class _VersionKey:
    def __init__(self, version):
        self.version = version

    def __lt__(self, other):
        return compare_versions(self.version, other.version) < 0

    def __eq__(self, other):
        return compare_versions(self.version, other.version) == 0


GO_RELEASES = "https://go.dev/dl/?mode=json&include=all"


def go_key(version):
    return tuple(int(part) for part in version.split("."))


def go_minor(version):
    return ".".join(version.split(".")[:2])


def go_releases(archs):
    """{version: {arch: sha256}} for stable Go releases that have linux
    archives for every architecture we build."""
    with tempfile.TemporaryDirectory() as tmp:
        download(GO_RELEASES, Path(tmp) / "releases.json")
        data = json.loads((Path(tmp) / "releases.json").read_text())
    releases = {}
    for release in data:
        if not release.get("stable") or not re.fullmatch(r"go\d+\.\d+\.\d+", release["version"]):
            continue
        sums = {f["arch"]: f["sha256"] for f in release["files"]
                if f["os"] == "linux" and f["kind"] == "archive" and f["arch"] in archs}
        if set(sums) == set(archs):
            releases[release["version"][2:]] = sums
    return releases


def supported_go_minors(releases):
    """The Go minors that still get fixes: the two newest
    (https://go.dev/doc/devel/release#policy)."""
    minors = sorted({go_minor(v) for v in releases}, key=go_key)
    return set(minors[-2:])


def newest_go_patch(minor, releases):
    patches = [v for v in releases if go_minor(v) == minor]
    return max(patches, key=go_key) if patches else None


def required_go_minor(url, tag):
    """The Go minor a tag's go.mod asks for (toolchain line, else go line)."""
    with tempfile.TemporaryDirectory() as tmp:
        run("git", "init", "-q", tmp)
        run_network("git", "-C", tmp, "fetch", "-q", "--depth", "1", url, f"refs/tags/{tag}")
        gomod = run("git", "-C", tmp, "show", "FETCH_HEAD:go.mod")
    toolchain = re.search(r"^toolchain\s+go(\d+\.\d+)", gomod, re.M)
    directive = re.search(r"^go\s+(\d+\.\d+)", gomod, re.M)
    require(toolchain or directive, f"{url} {tag}: go.mod has neither a toolchain nor a go line")
    return (toolchain or directive).group(1)


def watch_go(pkg, changes, releases):
    """Pick Go toolchains for a Go package's changes, and propose rebuilds
    when Go has a newer patch release (those are mostly security fixes,
    which a static binary only gets by being rebuilt)."""
    bumped = set()
    supported = supported_go_minors(releases)
    for change in changes:
        if change["package"] != pkg.name:
            continue
        base = pkg.tracks.get(change["track"]) or pkg.tracks.get(change.get("based_on", ""))
        if not base or not base.go:
            continue
        # The track's minor, or newer if upstream needs it or the track's is
        # no longer supported.
        needed = required_go_minor(pkg.upstream["git"], change["tag"])
        oldest = min(supported, key=go_key) if supported else needed
        minor = max(go_minor(base.go), needed, oldest, key=go_key)
        version = newest_go_patch(minor, releases)
        require(version, f"{pkg.name} {change['track']}: no stable Go {minor} release on go.dev")
        change["go"] = version
        change["go_sha256"] = releases[version]
        bumped.add(change["track"])
    extra = []
    for key, track in pkg.tracks.items():
        if not track.go or not track.active() or key in bumped:
            continue
        if supported and go_minor(track.go) not in supported:
            # Go supports its two newest minors; an older one gets no more
            # security fixes. Move to the oldest supported one.
            version = newest_go_patch(min(supported, key=go_key), releases)
            extra.append({"package": pkg.name, "kind": "toolchain", "track": key, "from": track.go,
                          "go": version, "go_sha256": releases[version],
                          "reason": f"Go {go_minor(track.go)} is no longer supported"})
            continue
        version = newest_go_patch(go_minor(track.go), releases)
        if version and go_key(version) > go_key(track.go):
            extra.append({"package": pkg.name, "kind": "toolchain", "track": key, "from": track.go,
                          "go": version, "go_sha256": releases[version]})
    return extra


def watch_report(pkg, tags, changes, go=None, today=None):
    """Lines for the watch log: per track our version, the newest upstream
    release it follows and what watch decided, the same for its Go
    toolchain, and new release lines."""
    releases = upstream_releases(pkg, tags)
    newest = max(releases, key=_VersionKey) if releases else None
    lines = [f"{pkg.name}: {len(releases)} upstream releases match tag_regex"
             + (f", newest {newest}" if newest else "")]
    for key, track in pkg.tracks.items():
        label = f"{pkg.name} {key}" + (f" (line {track.line})" if track.line else "")
        ours = pkg.upstream_version(track.tag)
        if not track.active(today):
            lines.append(f"  {label}: ours {ours}, end of life since {track.eol} -> skipped")
            continue
        candidates = [v for v in releases if track.follows(v)]
        found = max(candidates, key=_VersionKey) if candidates else None
        bump = next((c for c in changes if c["kind"] == "bump" and c["track"] == key), None)
        if bump:
            decision = f"update to {bump['tag']}" + (f" with Go {bump['go']}" if bump.get("go") else "")
        elif found is None:
            decision = "no upstream release for this track"
        else:
            decision = "up to date"
        lines.append(f"  {label}: ours {ours}, upstream {found or '-'} -> {decision}")
        if track.go and pkg.go_mode == "bootstrap":
            lines.append(f"  {label}: bootstrap Go {track.go} -> pinned (Go rebuilds itself with it)")
        elif track.go and not bump:
            rebuild = next((c for c in changes if c["kind"] == "toolchain" and c["track"] == key), None)
            newest_go = newest_go_patch(go_minor(track.go), go) if go else None
            if rebuild:
                go_decision = f"rebuild with Go {rebuild['go']}" + (f" ({rebuild['reason']})" if rebuild.get("reason") else "")
            elif go is None:
                go_decision = "not checked"
            else:
                go_decision = "up to date"
            lines.append(f"  {label}: Go {track.go}, newest {go_minor(track.go)}.x {newest_go or '-'} -> {go_decision}")
    numeric = [k for k in pkg.tracks if NUMERIC_RE.match(k)]
    if numeric:
        # Lines between our tracks that we don't follow: watch only adds
        # lines above the newest track, these need a look by hand.
        depth = len(numeric[0].split("."))
        lowest, highest = min(numeric, key=_VersionKey), max(numeric, key=_VersionKey)
        gaps = {}
        for version in releases:
            line = numeric_prefix(version, depth)
            if (line and line not in pkg.tracks and compare_versions(line, lowest) > 0
                    and compare_versions(line, highest) < 0
                    and (line not in gaps or compare_versions(version, gaps[line]) > 0)):
                gaps[line] = version
        for line, version in sorted(gaps.items(), key=lambda item: _VersionKey(item[0])):
            lines.append(f"  {pkg.name}: release line {line} ({releases[version][0]}) is between tracks {lowest} "
                         f"and {highest} -> not added; add a track by hand if it should be")
    for change in changes:
        if change["kind"] == "new-track":
            moves = ", ".join(f"alias {a} -> {t}" for a, t in change["aliases"].items())
            lines.append(f"  {pkg.name}: new release line {change['track']} ({change['tag']}) -> add track "
                         f"{change['track']} based on {change['based_on']}" + (f", {moves}" if moves else ""))
    return lines


def ensure_go_toolchain(root, version, sums):
    """Add a Go release's checksums to config.yaml if they aren't there."""
    if version in load_config(root).toolchains.get("go", {}):
        return
    path = root / "config.yaml"
    run("yq", "-i", f".toolchains.go[{json.dumps(version)}] = {json.dumps(sums)}", str(path))
    text = path.read_text()
    path.write_text(re.sub(r'^(\s+)(\d+(?:\.\d+)+):', r'\1"\2":', text, flags=re.M))


def yq_string(value):
    return json.dumps(value)


def apply_change(root, change):
    """Edit package.yaml with yq so comments and layout survive."""
    pkg = load_package(change["package"], root)
    path = pkg.path / "package.yaml"
    key = yq_string(change["track"])
    if change.get("go"):
        ensure_go_toolchain(root, change["go"], change["go_sha256"])
    if change["kind"] == "companion":
        name = yq_string(change["companion"])
        companion = pkg.companions[change["companion"]]
        expr = (f'.tracks[{key}].companions[{name}].tag = {yq_string(change["tag"])} | '
                f'.tracks[{key}].companions[{name}].commit = {yq_string(change["commit"])}')
        if companion.upstream.get("tarball"):
            url = companion.tarball_url(companion.upstream_version(change["tag"]))
            sha256 = change.get("sha256")
            if sha256 is None:
                with tempfile.TemporaryDirectory() as tmp:
                    sha256 = download(url, Path(tmp) / "release")
                change["sha256"] = sha256
            expr += f' | .tracks[{key}].companions[{name}].sha256 = {yq_string(sha256)}'
        run("yq", "-i", expr, str(path))
        return
    if change["kind"] == "toolchain":
        revision = pkg.tracks[change["track"]].revision + 1
        run("yq", "-i", f'.tracks[{key}].go = {yq_string(change["go"])} | .tracks[{key}].revision = {revision}',
            str(path))
        return
    sha256 = change.get("sha256")
    if sha256 is None and pkg.upstream.get("tarball"):
        # Pin the release tarball now; builds verify it against this.
        url = pkg.tarball_url(pkg.upstream_version(change["tag"]))
        with tempfile.TemporaryDirectory() as tmp:
            sha256 = download(url, Path(tmp) / "release")
        change["sha256"] = sha256
    assets = change.get("assets")
    if assets is None and pkg.assets:
        # Pin the release assets the same way.
        version = pkg.upstream_version(change["tag"])
        assets = {}
        with tempfile.TemporaryDirectory() as tmp:
            for name, template in pkg.assets.items():
                assets[name] = download(template.format(version=version), Path(tmp) / name)
        change["assets"] = assets
    if change["kind"] == "bump":
        expr = (f'.tracks[{key}].tag = {yq_string(change["tag"])} | '
                f'.tracks[{key}].commit = {yq_string(change["commit"])} | '
                f'.tracks[{key}].revision = 1')
        if sha256:
            expr += f' | .tracks[{key}].sha256 = {yq_string(sha256)}'
        if change.get("go"):
            expr += f' | .tracks[{key}].go = {yq_string(change["go"])}'
        for name, digest in (assets or {}).items():
            expr += f' | .tracks[{key}].assets[{yq_string(name)}] = {yq_string(digest)}'
    else:
        base = pkg.tracks[change["based_on"]]
        entry = {"tag": change["tag"], "commit": change["commit"], "revision": 1}
        if sha256:
            entry["sha256"] = sha256
        if change.get("go"):
            entry["go"] = change["go"]
        elif base.go and pkg.go_mode == "bootstrap":
            entry["go"] = base.go
        if assets:
            entry["assets"] = assets
        if base.codenames:
            entry["codenames"] = base.codenames
        if base.companions:
            entry["companions"] = {name: {k: v for k, v in vars(pin).items() if v is not None}
                                   for name, pin in base.companions.items()}
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
    config = load_config(root)
    changes = []
    releases = None
    failed = []
    for name in names:
        # One upstream that is down (or a bug with one package) must not
        # keep the others from getting their pull requests.
        try:
            pkg = load_package(name, root)
            if not pkg.data.get("watch", True):
                print(f"{pkg.name}: watch disabled", file=sys.stderr)
                continue
            tags = remote_tags(pkg.upstream["git"])
            found = watch_package(pkg, tags)
            if pkg.go_mode == "modules" and any(track.go for track in pkg.tracks.values()):
                releases = releases if releases is not None else go_releases(list(config.architectures))
                found += watch_go(pkg, found, releases)
            companion_tags = {c: remote_tags(companion.upstream["git"]) for c, companion in pkg.companions.items()}
            found += watch_companions(pkg, companion_tags)
        except Exception as error:  # noqa: BLE001
            print(f"ERROR: {name}: {error}", file=sys.stderr)
            failed.append(name)
            continue
        # The report goes to stderr: stdout is the JSON the workflow reads.
        print("\n".join(watch_report(pkg, tags, found, releases)
                        + companion_report(pkg, companion_tags, found)), file=sys.stderr)
        changes.extend(found)
    if args.tracks:
        changes = [c for c in changes if c["track"] in args.tracks]
    if args.kinds:
        changes = [c for c in changes if c["kind"] in args.kinds]
    if args.companions:
        changes = [c for c in changes if c.get("companion") in args.companions]
    if args.apply:
        for change in changes:
            apply_change(root, change)
    print(json.dumps(changes, indent=2))
    if failed:
        print(f"ERROR: watch failed for {', '.join(failed)}; the other packages' changes are above", file=sys.stderr)
        return 1
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
            print(f"{pkg.name} {component}")
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
    p.add_argument("--released", help="registry JSON (status.json) with each project's last released commit")
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
    p.add_argument("--kinds", nargs="*", help="only these kinds of change: bump, new-track, toolchain, companion")
    p.add_argument("--companions", nargs="*", help="only changes for these companions (with --apply: one PR each)")
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
