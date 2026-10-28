# prometheus-alertmanager

[Alertmanager](https://github.com/prometheus/alertmanager) built from source with a pinned Go toolchain, using Debian's packaging (`salsa.debian.org/go-team/packages/prometheus-alertmanager`) for the layout: `/usr/bin/prometheus-alertmanager` and `amtool`, systemd unit and init script, `/etc/default/prometheus-alertmanager`, `/etc/prometheus/alertmanager.yml`, data in `/var/lib/prometheus/alertmanager`, logrotate, man pages and bash completion. The package keeps Debian's name, so it replaces the distribution's on upgrade.

Upstream supports only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/prometheus-alertmanager <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 0.25.0 |
| trixie | 0.28.1 |
| jammy | 0.23.0 |
| noble | 0.26.0 |
| resolute | 0.28.1 |

## Changes to Debian's packaging

- **`overlays/go-build.patch`** (all releases): Debian builds against Debian-packaged Go libraries that only exist in unstable, and can't ship the web UI (building it needs an Elm toolchain Debian doesn't have), so its packages have no UI, no Kafka output for the event recorder and, in 0.28, no Jira receiver. We build with the pinned Go toolchain from `config.yaml` and vendored dependencies verified against `go.sum` and sum.golang.org (see [Go programs](../../docs/packages.md#go-programs)), like upstream's release binaries: `CGO_ENABLED=0`, tag `netgo`, the same version stamps (`--version` shows the upstream commit as revision), and the web UI from upstream's release asset `alertmanager-web-ui-<version>.tar.gz` (pinned by sha256, see [Release assets](../../docs/packages.md#release-assets)) embedded the way upstream's build does. Upstream's test suite runs during the build, including the acceptance tests against the built binaries. Debian's `generate-ui.sh`, its "no UI" page and the `-dev` package are dropped; the default templates stay in `/usr/share/prometheus/alertmanager` for reference. `debian/tests/basic-functionality` needs systemd, so it's marked `isolation-machine` (our smoke test covers it); `generate-ui` is dropped. The init script matches the daemon by pidfile and executable only: by name (cut to 15 characters by the kernel) it found nothing, so `service prometheus-alertmanager reload` failed. A NEWS entry explains the changes, including the removal of `/api/v1` in 0.27.
- **`patches/main/`:** Debian's `0003` (Debian defaults: `/etc/prometheus/alertmanager.yml`, `/var/lib/prometheus/alertmanager`, `amtool` reading `/etc/prometheus/amtool.yml` and talking to `localhost:9093`), so the defaults stay what the distributions' versions had. `0004` (ours) accepts `--web.ui-path`, the flag Debian's packages added for a separately built UI, and ignores it with a warning, so settings that pass it keep working. Debian's other patches are dropped: `0001` (no embedded UI and templates), `0005` (kingpin import path), `0006` (Kafka disabled), `0010` (an OpenTelemetry version for Debian's library), and the test patches `0002`, `0004`, `0007`–`0009`.

## Tests

- **Smoke:** version and revision of both binaries, static linking, `amtool check-config`, the service starts through the init script as `prometheus`, `/api/v2/status` reports the version, the web UI page and its script are served, an alert posted through `/api/v2/alerts` shows up in `amtool`, a silence can be added, listed and expired, and `service prometheus-alertmanager reload` reloads the configuration.
- **Upgrade:** the distribution's version (0.23.0 on jammy) runs with custom `ARGS` (no cluster, and Debian's `--web.ui-path`), a changed `alertmanager.yml` and a silence. Ours has to start with the same settings, warn about `--web.ui-path`, and still have the silence.
