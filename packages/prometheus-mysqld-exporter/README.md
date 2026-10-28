# prometheus-mysqld-exporter

[mysqld_exporter](https://github.com/prometheus/mysqld_exporter) built from source with a pinned Go toolchain, using Debian's packaging (`salsa.debian.org/go-team/packages/prometheus-mysqld-exporter`) for the layout: `/usr/bin/prometheus-mysqld-exporter`, systemd unit and init script, `/etc/default/prometheus-mysqld-exporter`, logrotate and the man page. The package keeps Debian's name, so it replaces the distribution's on upgrade.

Upstream supports only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/prometheus-mysqld-exporter <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 0.14.0 |
| trixie | 0.17.2 |
| jammy | 0.13.0 |
| noble | 0.15.0 |
| resolute | 0.18.0 |

## Credentials

The exporter reads them from `/var/lib/prometheus/.my.cnf`, the `prometheus` user's `~/.my.cnf`, or from the file in `--config.my-cnf`:

```ini
[client]
user = prometheus
password = secret
socket = /run/mysqld/mysqld.sock
```

`/etc/default/prometheus-mysqld-exporter` documents the rest: `--mysqld.username`, `--mysqld.address` and `MYSQLD_EXPORTER_PASSWORD` instead of a file, and the monitoring user's privileges (`PROCESS, REPLICATION CLIENT, SELECT`, plus `SLAVE MONITOR` on MariaDB 10.5 and later, without which the `slave_status` collector fails).

**Upgrading from jammy's 0.13 or bookworm's 0.14:** 0.15 dropped `DATA_SOURCE_NAME`, which Debian's defaults file documented. A setup that sets it no longer starts; the log says `DATA_SOURCE_NAME is no longer supported` and the credentials have to move to the file above. Setups that used `~/.my.cnf` keep working. NEWS (shown by apt-listchanges) has the details, along with the metric changes since 0.14.

## Changes to Debian's packaging

- **`overlays/go-build.patch`** (all releases): Debian builds against Debian-packaged Go libraries that only exist in unstable. We build with the pinned Go toolchain from `config.yaml` and vendored dependencies verified against `go.sum` and sum.golang.org (see [Go programs](../../docs/packages.md#go-programs)), like upstream's release binaries: `CGO_ENABLED=0`, the same version stamps (`--version` and `mysqld_exporter_build_info` show the upstream commit as revision). Upstream's test suite runs during the build; it needs no database. The overlay also replaces the defaults file's outdated `DATA_SOURCE_NAME` instructions (and adds MariaDB's `SLAVE MONITOR` privilege), exports `MYSQLD_EXPORTER_PASSWORD` from the init script, and adds NEWS entries for the changes since 0.13.
- **`patches/main/`:** ours. `0001` keeps `/var/lib/prometheus/.my.cnf` as the default `--config.my-cnf` (upstream's default since 0.15 is `.my.cnf` in the working directory, which for the service is `/`); `0002` logs a warning when `DATA_SOURCE_NAME` is set. Debian's `0001` (reverts the kingpin import path to Debian's library name) and `0002` (sorts the scrapers; upstream keeps them in a list since 0.21) are dropped.

## Tests

- **Smoke:** version and revision, static linking, `DATA_SOURCE_NAME` alone makes it exit with the warning, the service starts through the init script as `prometheus` with the credentials in `~prometheus/.my.cnf` and monitors the distribution's MariaDB (`mysql_up 1`, status and version metrics), and `--mysqld.username`/`--mysqld.address`/`MYSQLD_EXPORTER_PASSWORD` work without a file.
- **Upgrade:** the distribution's version (0.13.0 on jammy) runs against MariaDB with the credentials in `~prometheus/.my.cnf` (passed with `--config.my-cnf` from 0.15) and another port in `ARGS`. Ours has to keep the configuration and monitor the server on the same port.
