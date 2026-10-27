# prometheus

[Prometheus](https://prometheus.io) built from source with a pinned Go toolchain, using Debian's packaging (`salsa.debian.org/go-team/packages/prometheus`) for the layout: `prometheus` (server, systemd unit and init script, `/etc/default/prometheus`, `/etc/prometheus/prometheus.yml`, data in `/var/lib/prometheus/metrics2`, logrotate) and `promtool`, with man pages.

Two tracks, matching upstream's support policy:

| Track | Component | Follows | Supported upstream |
|---|---|---|---|
| `main` | `main` | every release (a new minor every six weeks) | the latest minor |
| `lts` | `lts` | the current long-term support line, 3.13.x | until 2027-07-31 |

Repository: `deb [signed-by=…] https://deb.freshdeb.com/prometheus <codename> main` (or `lts`)

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 2.42.0 |
| trixie | 2.53.3 |
| jammy | 2.31.2 |
| noble | 2.45.3 |
| resolute | 2.53.5 |

Every 2.x release is end of life upstream. Debian's own Prometheus 3 (3.5.3, unstable/testing only) ships without the web UI.

## Changes to Debian's packaging

- **`overlays/go-build.patch`** (all releases): Debian builds against ~70 Debian-packaged Go libraries and has to disable what isn't packaged: the web UI, xDS/Kuma, Consul, Nomad, Azure, OpenStack and StackIT service discovery, Azure AD for remote write, and OTLP ingestion. We build with the pinned Go toolchain from `config.yaml` and vendored dependencies verified against `go.sum` and sum.golang.org (see [Go programs](../../docs/packages.md#go-programs)), so everything is there, as in upstream's release binaries:
  - Same build as upstream's: `CGO_ENABLED=0`, `-tags netgo,builtinassets`, the same Go minor (`.promu.yml`), and the same version stamps (`--version` and `/api/v1/status/buildinfo` show the upstream commit as revision, read from our changelog entry).
  - **Web UI:** upstream's release asset `prometheus-web-ui-<version>.tar.gz`, pinned by sha256 per track ([release assets](../../docs/packages.md#release-assets)), compressed and embedded by upstream's `scripts/compress_assets.sh`, exactly what upstream's own build embeds. Debian's "no UI" placeholder and its `install-ui.sh` are gone, as are the symlinks to JavaScript libraries left over from the classic UI.
  - Debian's `golang-github-prometheus-prometheus-dev` package (Prometheus' Go source code) isn't built.
  - **`debian/NEWS`:** Debian's "the web UI has been disabled" entry is replaced by one about the upgrade from Prometheus 2 (below), which apt-listchanges shows on upgrade.
  - Upstream's test suite doesn't run: Debian has to disable a dozen flaky or resource-hungry tests to get through it. The smoke and upgrade tests run against the installed packages instead.
- **`patches/main/`, `patches/lts/`:** of Debian's patches only `0004` (default paths: `/etc/prometheus/prometheus.yml`, `/var/lib/prometheus/metrics2/`, …), refreshed for each line. All the patches that disable features or skip tests are dropped.
- **`overlays/sysusers-no-lock-modifier.patch`** (bookworm, noble): their systemd-sysusers doesn't know the `u!` (locked account) modifier, so plain `u`.
- **`overlays/legacy-sysusers.patch`** (jammy): no `dh_installsysusers` there, and tmpfiles rules only apply under systemd, so `postinst` creates the user and directories with adduser, as Debian did before Prometheus 3.

## Upgrading from Prometheus 2

Adding this repository upgrades the distribution's 2.x to 3.x on the next `apt upgrade`. Prometheus 3 reads the data Prometheus 2 wrote, and Debian's default configuration and empty `ARGS` work unchanged. But 3.0 removed or changed some flags, configuration settings and PromQL behaviours ([migration guide](https://prometheus.io/docs/prometheus/latest/migration/)), and data written by 3.x is only readable by 2.55 or newer, so there's no going back to the distribution's version. `debian/NEWS` says so.

## Tests

- **Smoke:** versions and revision of both binaries, static linking, a configuration with every service discovery mechanism Debian disables plus Azure AD remote write passes `promtool check config`, the OTLP receiver flag exists, Prometheus starts through the init script as `prometheus`, scrapes itself, answers the API and `promtool query`, serves the web UI with its assets, and writes to `/var/lib/prometheus/metrics2`.
- **Upgrade:** the distribution's Prometheus 2 (2.31 on jammy) scrapes itself, and its `promtool` backfills a block six hours back. Ours has to start on the same configuration and data directory and return both the samples from the WAL and the backfilled ones.

## Moving `lts`

watch.yml only bumps `lts` within `line`. When upstream names the next LTS (roughly once a year, see [the release schedule](https://prometheus.io/docs/introduction/release-cycle/)), move `line`, `tag`, `commit`, `go` and the asset checksum to it in a PR.
