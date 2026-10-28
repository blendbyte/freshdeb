# golang

[Go](https://go.dev) built from upstream's source release, using Debian's packaging (`salsa.debian.org/go-team/compiler/golang`, one source package per minor) for the layout: `golang-1.27-go` (compiler, tools and compiled standard library in `/usr/lib/go-1.27`), `golang-1.27-src`, `golang-1.27-doc` and the `golang-1.27` metapackage, plus what Debian builds from `golang-defaults`: `golang-go` (`/usr/bin/go` and `gofmt` for this Go), `golang-src`, `golang-doc`, `golang` and `golang-any`. The packages keep Debian's names, so they replace the distribution's on upgrade, and `apt install golang-go` gives this Go.

Upstream supports the two newest minors with patch releases (mostly security fixes). There is one track per minor and an alias for the newest:

| Component | Go | Supported until |
|---|---|---|
| `v1.27` | 1.27.x | Go 1.29 is out (about August 2027) |
| `v1.26` | 1.26.x | Go 1.28 is out (about February 2027) |
| `latest` | the newest track (1.27) | moves to each new minor |

Repository: `deb [signed-by=…] https://deb.freshdeb.com/golang <codename> latest` (or `v1.27`, `v1.26`)

Each component's `golang-go` points to its own minor. The minors' own packages install side by side, so `golang-1.26-go` can stay next to `golang-1.27-go` (`/usr/lib/go-1.26/bin/go`).

## Where the distributions are

| Codename | `golang-go` (`go`) | Newer Go in the distribution |
|---|---|---|
| bookworm | 1.19 | up to 1.23.5 in bookworm-backports |
| trixie | 1.24 | 1.26 and 1.27 in trixie-backports |
| jammy | 1.18 | `golang-1.20` … `golang-1.26` (1.26.8) in jammy-updates, not as the default `go` |
| noble | 1.22 | `golang-1.23` … `golang-1.26` (1.26.8) in noble-updates, not as the default `go` |
| resolute | 1.26 (1.26.8 in resolute-updates) | — |

## How it's built

- **Source:** upstream's release tarball (`go1.27.3.src.tar.gz`), pinned by its sha256 (go.dev publishes it); the tag's commit at go.googlesource.com is checked as for every package.
- **Bootstrap:** Go compiles itself with an existing Go (at least go1.24.6 for 1.26 and 1.27). Debian takes the newest `golang-1.*-go` of the distribution, which the older releases don't have; we use the pinned Go from go.dev in `config.yaml` (`build.go: bootstrap`, see [Go itself](../../docs/packages.md#go-itself-bootstrap-toolchain)). Upstream's `make.bash` then rebuilds the toolchain with itself, so the bootstrap compiler doesn't end up in the packages.
- **Unchanged upstream:** none of Debian's patches (they skip tests on mips64le, s390x and riscv64), and upstream's `go.env`: the module mirror proxy.golang.org, the checksum database sum.golang.org and `GOTOOLCHAIN=auto`, as with a Go from go.dev.
- **No test suite in the build:** upstream's `run.bash` takes far longer than the build and upstream runs it for every release; the smoke and upgrade tests check the installed packages instead.

## Changes to Debian's packaging

- **`overlays/bootstrap-build.patch`** (all releases): `GOROOT_BOOTSTRAP` from the build environment instead of the distribution's newest Go, no `golang-1.*-go` build-dependency, no `run.bash`. Debian's autopkgtest `regression-test` (upstream's `cmd/internal/testdir` suite, up to two hours) is dropped and `hello-world` uses `/usr/bin/go`: it found the Go from the source package name, which is `golang` here.
- **`overlays/default-go.patch`** (all releases): the packages and files of Debian's `golang-defaults` (2:1.27~1): `golang-go`, `golang-any`, `golang-src`, `golang-doc` and `golang`, the `/usr/bin/go`, `/usr/bin/gofmt`, `/usr/lib/go` and `/usr/share/go` links, its man pages (written for Go 1.27) and its `preinst`. They depend on this source's packages with the exact version. The whole package carries `golang-defaults`' epoch 2, so `golang-go` sorts above the distributions' (`2:1.19~1` …).
- **`overlays/older-binutils.patch`** (all releases): on arm64, Go uses the gold linker for cgo. It's a package of its own (`binutils-gold`) only since binutils 2.44 (trixie, resolute); `binutils` before 2.44 has it built in, so either satisfies the dependency.
- **`overlays/golang-1.26.patch`** (track 1.26): Debian's packaging is the same for every minor apart from the names; this renames `golang-1.27-*` to `golang-1.26-*`.
- **`packaging.source`:** Debian's source is `golang-1.27`; the build renames it to `golang`, so one package carries every minor.

## New minors

watch.yml bumps patch releases. A new minor (1.28) is a manual pull request: re-import `debian/` from Debian's `golang-1.28` branch (`packaging.ref`, `packaging.source`), add the track (watch.yml proposes it), make the previous minor's track a renaming overlay like `golang-1.26.patch`, drop the oldest track (or let its `eol` pass) and set the real `eol` dates. watch.yml's pull request already moves `latest` to the new track.

## Tests

- **Smoke:** `go version` is the release, `/usr/bin/go` and `gofmt` point to `/usr/lib/go-<minor>`, `go env` shows upstream's defaults, the standard library's sources and `go doc`, then local modules only (no network): a static program without cgo, a cgo program, `go vet`, `go test` with a module from a local `replace` and with the race detector, `gofmt`, and `golang-go`/`golang-src` depend on this minor.
- **Upgrade:** with the distribution's Go (1.18 on jammy), a user sets `go env -w GOPRIVATE=…` and builds a module. Ours has to be the default `go` afterwards, keep the setting and build the module; the distribution's own minor stays installed and runs next to ours.
