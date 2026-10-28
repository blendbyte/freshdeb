# Package format

A package is one directory under `packages/`, named after its Debian source package:

```
packages/<name>/
├── package.yaml        what to build (schema below)
├── debian/             Debian's packaging, imported unmodified
├── patches/<track>/    optional: quilt patch set per track (replaces debian/patches)
├── overlays/*.patch    optional: our changes to debian/, per codename or track
├── hooks/              optional: prepare hooks (see Bundled dependencies)
├── tests/smoke.sh      required: runs after a fresh install and after an upgrade
└── tests/upgrade.sh    optional: "seed" on the distro's version, "check" after upgrading
```

There's no README: a one-line comment in package.yaml says why each overlay, patch set, track or toolchain choice exists. What users need to know at upgrade time goes into a NEWS entry in `debian/` (apt-listchanges shows it).

Nothing outside this directory needs to change to add a package. The builder installs build-dependencies from `debian/control`, CI picks the package up from the diff, and the first release creates its own repository at `deb.freshdeb.com/<name>`.

## package.yaml

```yaml
name: valkey                    # = directory name = Source: in debian/control (or packaging.source)
description: "One line"
homepage: https://valkey.io

upstream:
  git: https://github.com/valkey-io/valkey.git
  tag_regex: '^(?P<version>\d+\.\d+\.\d+)$'   # release tags only; default '^v?(?P<version>\d+(\.\d+)*)$'
  submodules: false             # optional
  tarball: https://memcached.org/files/memcached-{version}.tar.gz
                                # optional; build from upstream's release tarball
                                # instead of the git tag (see below)
  assets:                       # optional; extra release files the build needs
    web-ui: https://github.com/prometheus/prometheus/releases/download/v{version}/prometheus-web-ui-{version}.tar.gz

packaging:
  origin: https://salsa.debian.org/debian/valkey.git
                                # or "freshdeb": debian/ is our own (see
                                # "Own packaging"), without ref or source
  ref: <full commit>            # the commit debian/ was imported from, or
                                # sha256:<checksum> of the .debian.tar.* when
                                # Debian keeps the packaging without a VCS
  source: golang-1.27           # optional; Debian's source name when it differs
                                # from the package name (see Go itself)

epoch: 5                        # optional; only if the distro packages carry an epoch

tracks:                         # one APT component per track: main, or v<track> ("9.1" -> v9.1)
  "9.1":                        # quote keys: YAML reads 9.1 as a number
    tag: "9.1.3"
    commit: <full commit the tag points to>
    sha256: <tarball checksum>  # only with upstream.tarball; watch.yml fills it in
    go: "1.25.14"               # only for Go programs: toolchain from config.yaml (see below)
    assets:                     # only with upstream.assets: sha256 of each; watch.yml fills them in
      web-ui: <sha256>
    line: "3.13"                # only for named tracks: the release line it stays on (see Tracks)
    revision: 1                 # freshdeb packaging revision; back to 1 on a new tag
    eol: 2031-05-19             # optional; after this date the track isn't built anymore
    patches: patches/9.1        # optional; replaces debian/patches for this track
    codenames: [trixie]         # optional; default: every distribution

aliases:                        # optional; extra components mirroring a track
  "9": "9.1"                    # component v9 contains exactly what v9.1 contains
  latest: "9.1"                 # optional; the newest track, moved by watch.yml

overlays:                       # optional; patches to the assembled source tree
  - patch: overlays/legacy-sysusers.patch
    codenames: [jammy]          # optional filters; both must match when given
    tracks: ["8.0"]

codenames: [bookworm, trixie]   # optional; default: every distribution in config.yaml
watch: true                     # optional; false stops watch.yml for this package

build:
  options: [nocheck]            # DEB_BUILD_OPTIONS
  profiles: []                  # DEB_BUILD_PROFILES
  prepare: hooks/prefetch.py    # optional; fetches extra pinned sources (see below)
  go: bootstrap                 # optional; modules (default) or bootstrap (see Go itself)

tests:
  capabilities: [NET_ADMIN]     # optional; added to the test containers (see Tests)
  upgrade_from:                 # optional; when no distribution ships the package
    url: https://github.com/juanfont/headscale/releases/download/v0.28.0/headscale_0.28.0_linux_{arch}.deb
    sha256: {amd64: <sha256>, arm64: <sha256>}
```

`scripts/freshdeb.py validate` checks all of this. CI runs it on every change.

### Git tag or release tarball

By default the source is the git tag, checked out at the pinned commit. Set `upstream.tarball` when Debian builds from upstream's release tarball instead, typically because it contains generated files (`configure`, version stamps) that the git tag doesn't. The tarball becomes the orig tarball, verified against the track's `sha256` before anything is unpacked. The tag's commit is still checked, so a moved tag fails the build either way.

### Release assets

Some builds need files that upstream publishes with each release but doesn't keep in git, such as Prometheus' built web UI. List them under `upstream.assets` (name → URL with `{version}`) and pin each one per track under `assets:` with its sha256. The build downloads them into the source tree as `freshdeb-assets/<name>` plus the URL's archive suffix (`freshdeb-assets/web-ui.tar.gz`), verifies them, and the orig tarball includes them, so the source package holds everything the build used. watch.yml pins the new checksums when it bumps a track. Assets can't be combined with `upstream.tarball`.

### Go programs

Debian builds Go programs against Debian-packaged Go libraries (`golang-*-dev`) and the distribution's Go compiler. Older releases have neither in the versions current upstreams need. Instead, Go packages build with a pinned toolchain:

- **Toolchain:** the track's `go:` version must be listed under `toolchains.go` in `config.yaml`, with a sha256 per architecture. The builder downloads that exact release from go.dev and verifies it.
- **Dependencies:** `go mod download` and `go mod verify` check every module against the project's `go.sum` and the Go checksum database (sum.golang.org). `go mod vendor` then puts them into the source tree.
- **Build:** runs offline: `GOFLAGS=-mod=vendor`, `GOPROXY=off`, `GOTOOLCHAIN=local`, with the toolchain first on `PATH`.
- **Packaging:** keep Debian's `debian/` and replace the dh-golang build with an overlay (see `packages/restic/overlays/go-build.patch`): drop the `golang-*` build-dependencies and build with `go build`.
- **watch.yml:** on a new upstream release, it reads `go.mod` and moves the track to a newer Go minor if upstream requires one. When Go publishes a patch release of a track's minor, it opens a rebuild PR (new toolchain, `revision` + 1). Those releases are mostly security fixes, and a static binary only gets them by being rebuilt.
- **Supported Go only:** Go supports its two newest minors. When a track's minor drops out of support, watch.yml opens a rebuild PR that moves it to the oldest supported minor (pick upstream's release Go when adding a package, unless that one is out of support already).

### Go itself (bootstrap toolchain)

`packages/golang` builds the Go toolchain, which needs an existing Go to compile itself. With `build.go: bootstrap`, a track's `go:` toolchain is only that bootstrap compiler:

- **Build:** the pinned toolchain is installed and verified as for Go programs, but nothing is vendored (Go's own repository has no `go.mod` at its root). The build gets it as `GOROOT_BOOTSTRAP`, with `GOTOOLCHAIN=local` and `GOPROXY=off`; debian/rules has to use that instead of looking for a distribution's `/usr/lib/go-*`.
- **Version:** it must be at least what the Go release asks for (`minBootstrap` in `src/cmd/dist/buildtool.go`, go1.24.6 for Go 1.26 and 1.27). Nothing checks this automatically; the build fails if it's too old.
- **watch.yml:** doesn't read `go.mod` and doesn't propose rebuilds when Go has a patch release: Go rebuilds itself with the bootstrap compiler, which doesn't end up in the result. Upstream's own releases are bumped as usual; a new track copies the bootstrap toolchain from the one it's based on.

Debian packages each Go minor as its own source (`golang-1.27`). `packaging.source` names it, validation checks the imported `debian/control` against it, and the build renames `Source:` to the package name, so one package (and repository) carries every minor as a track.

### Bundled dependencies (prepare hooks)

Some projects build their own copies of libraries that older releases lack or have too old. Neovim does this with upstream's pinned `cmake.deps/deps.txt`. A `build.prepare` hook fetches those sources before the build, in the unpacked source tree, with network access, so the build itself runs offline. Hooks get the source directory as their argument (and `FRESHDEB_PACKAGE`, `FRESHDEB_TRACK`, `FRESHDEB_UPSTREAM_VERSION`). They **must verify every download against a pinned checksum**; nothing checks them later. See `packages/neovim/hooks/prefetch-deps.py`. Make the build fail on any download attempt that slipped through (neovim's rules set an unusable proxy).

### Binaries with derived versions

Every binary carries the package's version, with one exception from Debian's conventions: bundled extras versioned from it, e.g. ZNC's `znc-push` as `1.0.0+git20190521.78d0385+<znc version>`. The release and the install test accept a binary version that equals the package version or ends in `+<package version>`, and nothing else.

### Architecture: all packages

Every build job also builds the package's `Architecture: all` binaries, so each job's packages install and test on their own (`neovim` needs `neovim-runtime`). The release publishes them only from the first architecture in `config.yaml`.

### Tracks

Most projects only maintain their latest release. They get one track named `main` (component `main`), which follows every upstream release, minors and majors included:

```
deb [signed-by=…] https://deb.freshdeb.com/restic trixie main
```

Use version tracks only when upstream patches several release lines in parallel, like valkey. Each line gets a track keyed by its version prefix (`"9.1"` covers 9.1.x, component `v9.1`), plus optional aliases per major (`"9"` → component `v9`). watch.yml relies on the prefix to find patch releases and to propose new lines.

Version tracks also fit projects whose upgrades must go one line at a time, like headscale (0.28 → 0.29 → 0.30, enforced by headscale itself): each minor is a track, and the older ones stay published as stepping stones. An alias named `latest` follows the newest track; when watch.yml proposes a new track, it moves `latest` to it in the same pull request.

When upstream names one line for long-term support and the lines around it come and go quickly, like Prometheus (a minor every six weeks, one LTS line a year), use `main` plus a named track with a `line`:

```yaml
tracks:
  main:             # every release
    tag: "v3.15.1"
  lts:              # component lts, only 3.13.x
    line: "3.13"
    tag: "v3.13.5"
```

watch.yml only bumps `lts` within its line. When upstream names the next LTS, move `line` and `tag` to it in a PR, so users of the `lts` component move along.

Never remove a track. When upstream support ends, the `eol` date stops builds and the component stays published as-is. The release refuses to publish if a component disappears. If components really have to change before anyone uses them, delete the project's directory in the bucket and release with `reset_project` (a dispatch input of release.yml).

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

## Own packaging, when Debian has none

Some projects have no packaging in Debian or Ubuntu to import (frp). Then `debian/` is ours: `packaging.origin: freshdeb`, no `ref`. The changelog entry says the packaging is maintained here.

- Write it the way Debian would: debhelper (compat 13, which jammy has), Debian's paths and naming, conffiles in `/etc`, a system user created in `postinst` with `adduser`, a `debian/copyright`. Look at how comparable Debian packages do it.
- Stay close to upstream: its build flags, its configuration files and defaults, its service files if it has (or had) any. Every difference needs a one-line reason, in package.yaml or next to the change.
- Edit `debian/` directly; overlays are only for differences between codenames. Patches to upstream's source go in `patches/<track>/`.
- Start `debian/changelog` with one entry for the first version; the build adds ours on top.

## Tests

CI installs the built packages in a bare container of the target distribution (no systemd, but init scripts work via `service`):

| Step | What runs |
|---|---|
| install | `apt install` our debs, then `tests/smoke.sh`, then Debian's autopkgtests from `debian/tests` |
| upgrade | `apt install` the distro's own version (if it has one, else the upstream package in `tests.upgrade_from`), `tests/upgrade.sh seed`, `apt install` our debs, `tests/upgrade.sh check`, `tests/smoke.sh` |

Tests run as root under `bash -euo pipefail` and get `FRESHDEB_VERSION` and `FRESHDEB_UPSTREAM_VERSION`. Check real behaviour: the service starts as the right user, data survives the upgrade, the advertised features are compiled in.

We test the packaging; upstream tests its code. Keep upstream's test suite in the build only when it's quick, otherwise set `build.options: [nocheck]` (git, memcached, valkey).

When no distribution ships the package but upstream publishes its own `.deb`, `tests.upgrade_from` names it: a URL with an `{arch}` placeholder (the dpkg architecture) and a sha256 per architecture. `verify.sh` downloads it on the host, checks the checksum and the upgrade step starts from it, which is the path users of upstream's package take. Pin a release of the previous line, and move it along when the tracks move.

Test containers get `SYS_PTRACE` (init scripts need it to find daemons running as another user) and nothing else. A package whose tests need more lists it in `tests.capabilities`: `NET_ADMIN` (keepalived adds its virtual IPs) or `NET_RAW`. Ask for as little as the tests need.

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
