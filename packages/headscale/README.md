# headscale

[headscale](https://github.com/juanfont/headscale), the open source Tailscale control server, built from source with a pinned Go toolchain. Neither Debian nor Ubuntu ship it. Upstream publishes its own `.deb` with each release, but no APT repository; this is one, with packaging of our own (Debian style) laid out like upstream's `.deb`: `/usr/bin/headscale`, `/etc/headscale/config.yaml` (upstream's `config-example.yaml`, a conffile), data in `/var/lib/headscale`, the system user `headscale` and upstream's sandboxed `headscale.service`, enabled and started on install (the example configuration listens on 127.0.0.1:8080). The package is called `headscale` like upstream's, so it upgrades an installation of upstream's `.deb` (`0.29.6-1+freshdeb~…` sorts after upstream's `0.29.6`).

## Tracks

headscale upgrades one minor version at a time (0.28 → 0.29 → 0.30) and checks it: it refuses to start on a database last used by a version more than one minor older, or by a newer one, and names the release to go through. So there is one track per minor, and the older ones stay published as stepping stones:

| Component | Contains |
|---|---|
| `v0.29` | 0.29.x |
| `latest` | the newest minor (now 0.29); watch.yml moves it when a new minor comes out |

Repository: `deb [signed-by=…] https://deb.freshdeb.com/headscale <codename> v0.29` (or `latest`)

Pin the minor component and move to the next one after reading its release notes, or follow `latest` if you keep up with every release. A system that skipped a minor installs the one in between from its component first. Upstream supports only its latest release: a track gets an `eol` date when the next minor is out.

## Upgrading

Upstream's steps apply ([upgrade guide](https://headscale.net/stable/setup/upgrade/)): read the release notes, back up `/etc/headscale` and `/var/lib/headscale`, upgrade, compare the configuration. headscale migrates the database when it starts. If you changed `/etc/headscale/config.yaml`, dpkg keeps your file and puts the new example next to it as `config.yaml.dpkg-dist`; when a minor removes configuration keys, headscale refuses to start and says which (0.29 removed `randomize_client_port`, for example), so carry your changes over to the new file.

## Changes to upstream

- **`patches/0.29/0001`** (ours): `headscale version` and the database version check take the version from Go's build info, which only has it when building in a git checkout of the release tag. Built from the release's source, headscale would say `dev` and skip the check. The patch lets the build set the version, commit and build time with `-ldflags -X`; `debian/rules` passes `v<version>`, the upstream commit and the build date, so `headscale version` reads like upstream's release binaries.
- Built like upstream's release binaries (`.goreleaser.yml`): `CGO_ENABLED=0`, goreleaser's default `-s -w`, Go 1.27.3, with vendored dependencies verified against `go.sum` and sum.golang.org (see [Go programs](../../docs/packages.md#go-programs)). Upstream's unit tests (`hscontrol`, `cmd`) run during the build; its integration tests run Tailscale clients in Docker and don't.
- Like upstream's `.deb`, purging the package removes the user and `/var/lib/headscale`, the database included. Shell completions for bash, zsh and fish come from `headscale completion`.

## Tests

- **Smoke:** version, commit and build time; static binary; the layout of upstream's package (user, conffile, unit enabled); `headscale configtest`; `headscale serve` as `headscale` answers `/health`; users, pre-auth keys and nodes through the CLI over the unix socket; a database from two minors back is refused. The tests use a local DERP map and no update check, so nothing leaves the container.
- **Upgrade:** from upstream's own 0.28.0 package (`tests.upgrade_from`, pinned by sha256). Its user sets up the configuration, a user and a pre-auth key; after the upgrade the configuration is kept, the new version refuses it for the removed key, the edits go to the new file, and headscale migrates the database: the user and key are there and the database records 0.29.6.
