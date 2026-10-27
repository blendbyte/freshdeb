# rclone

[rclone](https://rclone.org) built from source with a pinned Go toolchain, using Debian's packaging (`salsa.debian.org/go-team/packages/rclone`) for the layout: the `rclone` binary, `mount.rclone` helper links, the man page and the manual.

Upstream supports only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/rclone <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 1.60.1 |
| trixie | 1.60.1 |
| jammy | 1.53.3 |
| noble | 1.60.1 |
| resolute | 1.60.1 |

## Changes to Debian's packaging

- **`overlays/go-build.patch`** (all releases): Debian builds against ~170 Debian-packaged Go libraries, and has to exclude every backend and command whose libraries aren't packaged: Azure Blob and Files, Oracle Object Storage, Storj, Qingstor, Filen, Internxt, Cloudinary, `archive`, `serve s3/ftp/nfs`. We build with the pinned Go toolchain from `config.yaml` and vendored dependencies verified against `go.sum` and sum.golang.org (see [Go programs](../../docs/packages.md#go-programs)). That gives the full feature set of upstream's binaries, built with the same flags: `CGO_ENABLED=0 -trimpath -ldflags "-s -X …fs.Version=…"`. `rclone mount` uses rclone's pure-Go FUSE support.
  - Debian's `golang-github-rclone-rclone-dev` package (rclone's Go source code) isn't built.
  - The man page and manual are generated from the source as in Debian. Shell completions (bash, zsh, fish) come from the binary at build time, instead of Debian's static copy.
  - Upstream's test suite doesn't run: Debian ignores its failures, so it gates nothing there either. The smoke test covers local copy/sync/check, an encrypted crypt remote, `serve http`, the backend list, the mount helper, man page and completions. The upgrade test sets up a crypt remote and writes data with the distribution's rclone (1.53 on jammy), then reads, checks and extends it with ours.
- **`patches/main/`:** of Debian's patches, only `0004` (no installation instructions in the man page) and `0013` (no `-DEV` version suffix). All the patches that disable backends or skip tests are dropped.
