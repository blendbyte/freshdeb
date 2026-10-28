# prometheus-redis-exporter

[redis_exporter](https://github.com/oliver006/redis_exporter) (Valkey and Redis) built from source with a pinned Go toolchain, using Debian's packaging (`salsa.debian.org/go-team/packages/prometheus-redis-exporter`) for the layout: `/usr/bin/prometheus-redis-exporter`, systemd unit and init script, `/etc/default/prometheus-redis-exporter`, logrotate, the man page and upstream's Grafana dashboards and sample Lua scripts as examples. The package keeps Debian's name, so it replaces the distribution's on upgrade.

Upstream supports only its latest release and releases about once a week, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/prometheus-redis-exporter <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 1.45.0 |
| trixie | 1.69.0 |
| jammy | 1.33.0 |
| noble | 1.54.0 |
| resolute | 1.81.0 |

## Changes to Debian's packaging

- **`overlays/go-build.patch`** (all releases): Debian builds against Debian-packaged Go libraries that only exist in unstable. We build with the pinned Go toolchain from `config.yaml` and vendored dependencies verified against `go.sum` and sum.golang.org (see [Go programs](../../docs/packages.md#go-programs)), like upstream's release binaries: `CGO_ENABLED=0`, the same version stamps (`--version` and `redis_exporter_build_info` show `v<version>` and the upstream commit). Upstream's test suite needs running Redis servers, so it doesn't run during the build, as in Debian; the smoke test scrapes a real one.
- Debian has no patches. Its man page is a static file written for 1.4.0 and stays as it is; `prometheus-redis-exporter --help` lists every current option.
- No command-line flag was removed between 1.33 and the current release, so `ARGS` from the distributions' versions keep working. Metrics that changed on the way: Go runtime metrics (`go_*`) are only exported with `--include-go-runtime-metrics` (1.77), and the client metrics of `--export-client-list` have different labels (1.64).

## Tests

- **Smoke:** version and commit, static linking, the service starts through the init script as `prometheus` and scrapes the distribution's `redis-server` (`redis_up`, clients, keys per database, commands, `redis_exporter_build_info` with our version and commit), and a password-protected server works with `--redis.password-file` and is reported down without it.
- **Upgrade:** the distribution's version (1.33.0 on jammy) runs with custom `ARGS` (another Redis server and listen port). Ours has to keep the configuration and scrape the same server on the same port.
