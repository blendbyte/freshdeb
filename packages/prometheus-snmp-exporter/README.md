# prometheus-snmp-exporter

[snmp_exporter](https://github.com/prometheus/snmp_exporter) and its configuration generator built from source with a pinned Go toolchain, using Debian's packaging (`salsa.debian.org/go-team/packages/prometheus-snmp-exporter`) for the layout: `/usr/bin/prometheus-snmp-exporter` and `/usr/bin/prometheus-snmp-generator`, systemd unit and init script, `/etc/default/prometheus-snmp-exporter`, `/etc/prometheus/snmp.yml`, logrotate and the man pages. The package keeps Debian's name, so it replaces the distribution's on upgrade.

Upstream supports only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/prometheus-snmp-exporter <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 0.21.0 |
| trixie | 0.28.0 |
| jammy | 0.20.0 |
| noble | 0.25.0 |
| resolute | 0.30.1 |

## Upgrading from 0.20 or 0.21 (jammy, bookworm)

0.23 introduced a new configuration format, with the connection settings (`auths`) separate from the modules. The exporter doesn't start with an `snmp.yml` in the old format; regenerate it with `prometheus-snmp-generator` from a `generator.yml` with an `auths` section (see `/usr/share/doc/prometheus-snmp-exporter/auth-split-migration.md`). Later releases moved metrics between upstream's example modules (0.27, 0.28) and changed how the generator labels enums (0.31); the package's NEWS entry lists them. The generator and the exporter must be the same version, so regenerate with this package's generator either way.

## Changes to Debian's packaging

- **`overlays/go-build.patch`** (all releases): Debian builds against Debian-packaged Go libraries that only exist in unstable. We build with the pinned Go toolchain from `config.yaml` and vendored dependencies verified against `go.sum` and sum.golang.org (see [Go programs](../../docs/packages.md#go-programs)), with upstream's version stamps (`--version` and `snmp_exporter_build_info` show the upstream commit as revision). The exporter is static (`CGO_ENABLED=0`) like upstream's release binary; the generator uses net-snmp's MIB parser, so it links libsnmp like Debian's. Upstream's test suite runs during the build. The overlay also adds the NEWS entry above and creates `/var/log/prometheus`, which the init script logs to but Debian's package doesn't create.
- **`patches/main/`:** Debian's `0001` (default configuration `/etc/prometheus/snmp.yml`), refreshed. `0002` (reverts the kingpin import path to Debian's library name) is dropped.
- Like Debian's, the package ships a placeholder `/etc/prometheus/snmp.yml`: upstream's generated one can't be distributed for licensing reasons, and neither can most MIBs (`snmp-mibs-downloader` is in non-free). The exporter doesn't start until you generate a configuration; see `README.Debian`.

## Tests

- **Smoke:** version and revision, a static exporter and a generator linked to net-snmp, the generator writes a configuration from a small MIB that the exporter accepts (`--dry-run`), the service starts through the init script as `prometheus` and scrapes `snmpd` from the distribution on localhost (`sysUpTime`, `sysName` as a label), and a configuration reload.
- **Upgrade:** the distribution's generator writes the configuration and the distribution's exporter scrapes `snmpd` with it. On noble, trixie and resolute ours has to keep working with that configuration. On jammy and bookworm (old format) ours has to refuse it with a pointer to the migration guide, and after regenerating it with our generator return the same metrics.
