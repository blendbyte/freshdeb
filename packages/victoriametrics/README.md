# victoriametrics

[VictoriaMetrics](https://github.com/VictoriaMetrics/VictoriaMetrics) (single-node) built from source with a pinned Go toolchain, using Debian's packaging (`salsa.debian.org/go-team/packages/victoriametrics`) for the layout: the binary package `victoria-metrics` with `/usr/bin/victoria-metrics` and the tools `vmagent`, `vmalert`, `vmalert-tool`, `vmauth`, `vmbackup`, `vmrestore` and `vmctl`, systemd unit and init script for the server (user `_victoria-metrics`), `/etc/default/victoria-metrics`, data in `/var/lib/victoria-metrics`. The package keeps Debian's name, so it replaces the distribution's on upgrade.

Upstream releases about every two weeks and supports only the latest release for the open-source version, so there is one track, `main`, which follows every release. Its long-term support lines (v1.148.x, v1.136.x) are published for the enterprise version only, and upstream advises everyone else to stay on the latest release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/victoriametrics <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 1.79.5 |
| trixie | 1.112.0 |
| jammy | 1.71.0 |
| noble | 1.79.14 |
| resolute | 1.112.0 |

## Upgrading

The data directory is upgraded in place and can't go back to the distributions' versions afterwards (their storage format is older than 1.90), so take a snapshot and `vmbackup` first if you might need to. A few flags were removed since the distributions' versions, and VictoriaMetrics doesn't start with an unknown flag: `-relabelDebug` (victoria-metrics), `-remoteWrite.multitenantURL`, `-remoteWrite.relabelDebug` and `-remoteWrite.urlRelabelDebug` (vmagent), `-datasource.lookback`, `-datasource.queryTimeAlignment`, `-remoteRead.ignoreRestoreErrors` and `-rule.configCheckInterval` (vmalert). The NEWS entry (shown by apt-listchanges) lists these and the behaviour and metric changes that may need action.

## Changes to Debian's packaging

- **`overlays/go-build.patch`** (all releases): Debian builds against ~60 Debian-packaged Go libraries that only exist in unstable, and removes the web UI from its source (it is committed built, and Debian can't vouch for its provenance). We build with the pinned Go toolchain from `config.yaml` and vendored dependencies verified against `go.sum` and sum.golang.org (see [Go programs](../../docs/packages.md#go-programs)), the way upstream builds its release binaries: cgo for zstd compression (gozstd with its bundled static zstd library, part of the checksum-verified module), linked statically, tags `netgo` and `osusergo`, and upstream's version stamp (`-version` prints `victoria-metrics-<date>-tags-v<version>-0-g<commit>`). The web UI (`/vmui/`) and the vmagent and vmalert pages are included. Upstream's unit tests (`lib/` and `app/`) run during the build with Debian's three skips; the integration tests in `apptest/` need the cluster binaries and don't. The `-dev` package isn't built. The init script matches the daemon by pid file and binary (by name, cut to 15 characters by the kernel, `reload` failed), and the systemd unit gets `ExecReload` (SIGHUP, which reloads `-promscrape.config`, relabeling and stream aggregation configs). NEWS covers the upgrade.
- **`patches/main/`:** Debian's test fix for `TestMTLSCertificateRotation`. Debian's `Disable-vmui` and `Do-not-fail-if-vmselect-cannot-load-the-vmui-config` are dropped (the UI is built in), `Skip-memory-test-on-arm` only matters on 32-bit arm.
- Not included, as in Debian's package: the cluster version (`vminsert`, `vmselect`, `vmstorage`, upstream's `-cluster` tags), VictoriaLogs (its own project now), and services for `vmagent`, `vmalert` and `vmauth`, which run with configurations you write.

## Tests

- **Smoke:** every binary reports the version and commit and is static, the server starts through the init script as `_victoria-metrics`, samples written with `/api/v1/import/prometheus` come back from `/api/v1/query`, the web UI and its script are served, `vmagent` scrapes the server and remote-writes into it, `vmalert-tool` runs a rule unit test, a snapshot goes through `vmbackup` and `vmrestore` and a second server on the restored directory returns the samples, and `service victoria-metrics reload` keeps it running.
- **Upgrade:** the distribution's version (1.71.0 on jammy) runs with custom `ARGS` (another port, a longer retention) and gets samples from ten minutes and a day back. Ours has to start with the same settings on the same data directory, return both and keep taking new samples.
