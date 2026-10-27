# restic

[restic](https://restic.net) built from source with a pinned Go toolchain, using Debian's packaging (`salsa.debian.org/go-team/packages/restic`) for the layout: the `restic` binary, man pages, bash/zsh/fish completions and the HTML manual.

| Track | Component | Upstream support |
|---|---|---|
| 0 | `v0` | upstream supports only its latest release; 0.x rolls forward |

Repository: `deb [signed-by=…] https://deb.freshdeb.com/restic <codename> v0`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 0.14.0 |
| trixie | 0.18.0 |
| jammy | 0.12.1 |
| noble | 0.16.4 |
| resolute | 0.18.1 |

## Changes to Debian's packaging

- **`overlays/go-build.patch`** (all releases): Debian builds against 28 Debian-packaged Go libraries that only exist in unstable, and needs a newer Go than bookworm and trixie ship. We build with the pinned Go toolchain from `config.yaml` and vendored dependencies verified against `go.sum` and sum.golang.org (see [Go programs](../../docs/packages.md#go-programs)). The flags match upstream's release binaries: `CGO_ENABLED=0`, `-trimpath -ldflags "-s -w"`, so the binary is static. restic's own test suite runs during the build.
- **`patches/0/`:** Debian's patches without `0002-disable-azure.patch`. Debian has to disable the Azure backend because the Azure SDK isn't packaged in Debian; we vendor it, so Azure works.
- `restic self-update` is not built (that needs the `selfupdate` build tag), and `remove-self-update.patch` removes it from the docs. Updates come through apt.

## Go toolchain

The track uses the newest patch release of the Go minor that upstream's `go.mod` asks for (0.19.1 says `toolchain go1.25.10`, we build with 1.25.14). watch.yml opens a rebuild PR for every new Go patch release.
