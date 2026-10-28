# prometheus-postgres-exporter

[postgres_exporter](https://github.com/prometheus-community/postgres_exporter) built from source with a pinned Go toolchain, using Debian's packaging (`salsa.debian.org/go-team/packages/prometheus-postgres-exporter`) for the layout: `/usr/bin/prometheus-postgres-exporter`, systemd unit and init script, `/etc/default/prometheus-postgres-exporter` (the connection string, readable only by `prometheus`), logrotate and the man page. The package keeps Debian's name, so it replaces the distribution's on upgrade.

Upstream supports only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/prometheus-postgres-exporter <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 0.11.1 |
| trixie | 0.17.1 |
| jammy | 0.10.1 |
| noble | 0.15.0 |
| resolute | 0.19.0 |

## Changes to Debian's packaging

- **`overlays/go-build.patch`** (all releases): Debian builds against Debian-packaged Go libraries that only exist in unstable. We build with the pinned Go toolchain from `config.yaml` and vendored dependencies verified against `go.sum` and sum.golang.org (see [Go programs](../../docs/packages.md#go-programs)), like upstream's release binaries: `CGO_ENABLED=0`, the same version stamps (`--version` and `postgres_exporter_build_info` show the upstream commit as revision). Upstream's unit tests run during the build; its integration tests need a running PostgreSQL and are left to our smoke test.
- **NEWS** (same overlay): a new entry for the changes since the distributions' versions that need action: the log format (0.16), the `replication_slot` → `replication_slots` collector and metric rename, the removed `--disable-settings-metrics` and the standalone collectors (0.20). Debian's entry for the multi-target change (0.12) is already there. `DATA_SOURCE_NAME` in `/etc/default/prometheus-postgres-exporter` keeps working.
- **`overlays/init-export-environment.patch`** (all releases): the init script only sourced `/etc/default/prometheus-postgres-exporter` without exporting it, so on systems without systemd the exporter never saw `DATA_SOURCE_NAME` and ran without a database (`pg_up 0`). It now exports the file's settings, as systemd's `EnvironmentFile` does.
- **`patches/main/`:** empty. Debian's only patch (reverts the kingpin import path to Debian's library name) isn't needed with vendored modules.

## Tests

- **Smoke:** version and revision, static linking, the defaults file is `prometheus`-owned and `0640`, the service starts through the init script as `prometheus` against the distribution's PostgreSQL (the `prometheus` role with `pg_monitor` over the local socket, as `README.Debian` describes) and reports `pg_up 1`, database sizes, `pg_stat_database` and settings metrics.
- **Upgrade:** the distribution's version (0.10.1 on jammy) runs with that `DATA_SOURCE_NAME` and custom `ARGS` (another port) from the defaults file; it's started directly, since its init script doesn't pass `DATA_SOURCE_NAME` on. Ours has to keep both and reach PostgreSQL on the same port through the init script.
