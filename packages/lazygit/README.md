# lazygit

[lazygit](https://github.com/jesseduffield/lazygit), a terminal UI for git, built from source with a pinned Go toolchain, using Debian's packaging (`salsa.debian.org/go-team/packages/lazygit`): `/usr/bin/lazygit` and its man page. The package keeps Debian's name, so on trixie and resolute it replaces the distribution's on upgrade; jammy, bookworm and noble don't ship lazygit at all.

Upstream supports only its latest release, so there is one track, `main`, which follows every release. Releases come every week or two.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/lazygit <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | – |
| trixie | 0.50.0 |
| jammy | – |
| noble | – |
| resolute | 0.57.0 |

## Changes to Debian's packaging

- **`overlays/go-build.patch`** (all releases): Debian builds against Debian-packaged Go libraries (some of them Debian's own forks) that only exist in unstable. We build with the pinned Go toolchain from `config.yaml` and vendored dependencies verified against `go.sum` and sum.golang.org (see [Go programs](../../docs/packages.md#go-programs)), like upstream's release binaries: `CGO_ENABLED=0` and upstream's version stamps, so `lazygit --version` shows the version, the upstream commit and the release's build date. Build source is `freshdeb` (upstream's releases say `binaryRelease`); lazygit's self-update only runs for builds marked `buildBinary`, so it stays off and apt does the updates. Upstream's unit tests run during the build, as upstream's CI runs them (`-short`, without the integration tests that drive lazygit in a terminal). Debian's man page stays; it is written by hand for 0.57, so `lazygit --help` is the reference for newer options.
- **`patches/main/`:** empty. Debian's patches switch import paths to its library packages and forks, and remove the commit and build date from `--version`; ours are reproducible (from the changelog and `SOURCE_DATE_EPOCH`).

## Tests

- **Smoke:** version, commit, build date and build source, static linking, the config directory and `--config`, and the UI in a pseudo-terminal: lazygit opens a scratch repository, lists its changed file and shows the diff, and quits with `q`.
- **Upgrade** (trixie, resolute): a `~/.config/lazygit/config.yml` that switches the UI to Dutch, made for the distribution's version; ours has to pick it up ("Bestanden" instead of "Files").
