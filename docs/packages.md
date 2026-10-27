# Package format

A package is one directory under `packages/`, named after its Debian source package:

```
packages/<name>/
├── package.yaml        what to build (schema below)
├── debian/             Debian's packaging, imported unmodified
├── patches/<track>/    optional: quilt patch set per track (replaces debian/patches)
├── overlays/*.patch    optional: our changes to debian/, per codename or track
├── tests/smoke.sh      required: runs after a fresh install and after an upgrade
├── tests/upgrade.sh    optional: "seed" on the distro's version, "check" after upgrading
└── README.md           packaging notes: where things came from, quirks
```

Nothing outside this directory needs to change to add a package. The builder installs build-dependencies from `debian/control`, CI picks the package up from the diff, and the release adds its components.

## package.yaml

```yaml
name: valkey                    # = directory name = Source: in debian/control
description: "One line"
homepage: https://valkey.io
maintainers: [github-handle]    # who reviews bumps and build failures

upstream:
  git: https://github.com/valkey-io/valkey.git
  tag_regex: '^(?P<version>\d+\.\d+\.\d+)$'   # release tags only; default '^v?(?P<version>\d+(\.\d+)*)$'
  submodules: false             # optional

packaging:
  origin: https://salsa.debian.org/debian/valkey.git
  ref: <full commit>            # the commit debian/ was imported from

epoch: 5                        # optional; only if the distro packages carry an epoch

tracks:                         # one APT component per track: <name>-<track>
  "9.1":                        # quote keys: YAML reads 9.1 as a number
    tag: "9.1.3"
    commit: <full commit the tag points to>
    revision: 1                 # freshdeb packaging revision; back to 1 on a new tag
    eol: 2031-05-19             # optional; after this date the track isn't built anymore
    patches: patches/9.1        # optional; replaces debian/patches for this track
    codenames: [trixie]         # optional; default: every distribution

aliases:                        # optional; extra components mirroring a track
  "9": "9.1"                    # valkey-9 contains exactly what valkey-9.1 contains

overlays:                       # optional; patches to the assembled source tree
  - patch: overlays/legacy-sysusers.patch
    codenames: [jammy]          # optional filters; both must match when given
    tracks: ["8.0"]

codenames: [bookworm, trixie]   # optional; default: every distribution in config.yaml
watch: true                     # optional; false stops watch.yml for this package

build:
  options: [nocheck]            # DEB_BUILD_OPTIONS
  profiles: []                  # DEB_BUILD_PROFILES
```

`scripts/freshdeb.py validate` checks all of this. CI runs it on every change.

### Tracks

Use one track if upstream has a single release line, for example `"1"` for a project that only ships 1.x. Use one track per line when upstream patches several lines in parallel. The track key must be the version prefix of its tags (`"9.1"` covers 9.1.x). watch.yml relies on that to find patch releases and new lines.

Never remove a track. When upstream support ends, the `eol` date stops builds and the component stays published as-is. The release refuses to publish if a component disappears.

### Versions

Every build is versioned `[epoch:]<upstream>-<revision>+freshdeb~<deb12|deb13|ubuntu22.04|…>`.

- Bump `revision` whenever the packaging changes (debian/, patches, overlays, build options) without a new upstream tag. CI rejects packaging changes that don't change the version.
- Published versions are immutable. Re-running a release never replaces an existing file.
- Before building, the version guard compares against everything the distribution ships for that codename. If the distro has the same or a newer version, the build is skipped and nothing is published there.

## Packaging from Debian

1. Find the package on salsa.debian.org (or Ubuntu's Launchpad git if Debian doesn't have it). Copy `debian/` from the newest upload that matches your newest track, and record the commit in `packaging.ref`.
2. Leave `debian/` as imported. Put every change somewhere you can see it:
   - **Different upstream lines need different quilt patches:** use `patches/<track>/`, starting from the patch set Debian or Ubuntu used for that line. Drop CVE backports that upstream's newer patch release already contains.
   - **Older codenames need packaging changes** (old debhelper, old systemd, missing libraries): use an overlay patch, scoped with `codenames:`, with a comment in package.yaml saying why.
   - Create overlays with `diff -ruN a/debian b/debian` from a copy of the source tree.
3. The build replaces the source package's `Maintainer` with ours (keeping Debian's as `XSBC-Original-Maintainer`), drops `Uploaders` and `Vcs-*`, and adds a changelog entry. You don't need to do this yourself.

Refreshing `debian/` from a newer Debian upload means re-importing it, updating `packaging.ref`, checking every overlay and patch set still applies, and bumping `revision` on every track.

## Tests

CI installs the built packages in a bare container of the target distribution (no systemd, but init scripts work via `service`):

| Step | What runs |
|---|---|
| install | `apt install` our debs, then `tests/smoke.sh`, then Debian's autopkgtests from `debian/tests` |
| upgrade | `apt install` the distro's own version (if it has one), `tests/upgrade.sh seed`, `apt install` our debs, `tests/upgrade.sh check`, `tests/smoke.sh` |

Tests run as root under `bash -euo pipefail` and get `FRESHDEB_VERSION` and `FRESHDEB_UPSTREAM_VERSION`. Check real behaviour: the service starts as the right user, data survives the upgrade, the advertised features are compiled in.

Autopkgtests run without the autopkgtest tool. Tests needing `isolation-machine` or a reboot are reported as skipped. Tests that need the source tree aren't supported.

## Locally

```bash
scripts/freshdeb.py validate
scripts/freshdeb.py info valkey                        # versions per track and codename
scripts/build.sh valkey 9.1 bookworm arm64             # build for your machine's arch
scripts/verify.sh artifacts/valkey_9.1_bookworm_arm64  # install + upgrade tests
docker build -f tests/release.Dockerfile -t freshdeb-tests . && \
  docker run --rm -v "$PWD:/workspace:ro" freshdeb-tests   # unit + release tests
```

The scripts need Python 3 with PyYAML (`pip install pyyaml`) and Docker.
