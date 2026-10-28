# prometheus-blackbox-exporter

[blackbox_exporter](https://github.com/prometheus/blackbox_exporter) built from source with a pinned Go toolchain, using Debian's packaging (`salsa.debian.org/go-team/packages/prometheus-blackbox-exporter`) for the layout: `/usr/bin/prometheus-blackbox-exporter`, systemd unit and init script, `/etc/default/prometheus-blackbox-exporter`, the configuration `/etc/prometheus/blackbox.yml`, the debconf question for ICMP probing (`CAP_NET_RAW`), logrotate and the man page. The package keeps Debian's name, so it replaces the distribution's on upgrade.

Upstream supports only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/prometheus-blackbox-exporter <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 0.23.0 |
| trixie | 0.26.0 |
| jammy | 0.19.0 |
| noble | 0.24.0 |
| resolute | 0.26.0 |

## Changes to Debian's packaging

- **`overlays/go-build.patch`** (all releases): Debian builds against Debian-packaged Go libraries that only exist in unstable. We build with the pinned Go toolchain from `config.yaml` and vendored dependencies verified against `go.sum` and sum.golang.org (see [Go programs](../../docs/packages.md#go-programs)), like upstream's release binaries: `CGO_ENABLED=0`, the same version stamps (`--version` and `blackbox_exporter_build_info` show the upstream commit as revision). Upstream's test suite runs during the build the way its CI runs it (`CI=true`, which skips the gRPC, DNS and TCP tests that need `localhost` to resolve to `::1`), with `-short` as Debian runs it (no tests that need the internet).
- **`overlays/init-script.patch`** (all releases): two fixes to Debian's init script, which systems without systemd (and containers) use. It checked the configuration with `promtool check config`, which only knows Prometheus configurations (and needs the prometheus package), so `service prometheus-blackbox-exporter start` failed; it now runs the exporter's own `--config.check`. And `reload` looked for the daemon by a name longer than the 15 characters `start-stop-daemon` can match, so it failed; the pid file and binary identify it now. With systemd neither applies.
- **`patches/main/`:** empty. Debian's `0001` and `0003` disable or adjust tests that need `::1` (upstream's CI skips them the same way), `0004` and `0005` change import paths for Debian's library packages.

## Upgrading

Configurations keep working. Since 0.29 a module with an unknown `prober` is an error at startup instead of being ignored; check with `prometheus-blackbox-exporter --config.check --config.file=/etc/prometheus/blackbox.yml`. Logging changed since the distributions' versions: 0.26 switched to Go's `log/slog` format (`time=` instead of `ts=`), and 0.28 made probe logs a separate logger: errors are logged at `error`, details at `debug`; `--log.prober=debug` shows them all. The default HTTP User-Agent is `Blackbox-Exporter/<version>` since 0.28.

## Tests

- **Smoke:** version and revision, static linking, the shipped configuration passes `--config.check`, the service starts through the init script as `prometheus`, HTTP (`http_2xx`) and TCP probes succeed and a probe of a closed port fails, the debconf question sets and removes `cap_net_raw` and an ICMP probe works with it, `service … reload` picks up a new module, and the man page.
- **Upgrade:** the distribution's version (0.19.0 on jammy) gets a module of its own in `blackbox.yml` and custom `ARGS` (another port). It runs directly rather than through the distribution's init script, which can't start it (see above). Ours has to keep both and probe with that module on that port.
