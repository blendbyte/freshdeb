# prometheus-node-exporter

[node_exporter](https://github.com/prometheus/node_exporter) built from source with a pinned Go toolchain, using Debian's packaging (`salsa.debian.org/go-team/packages/prometheus-node-exporter`) for the layout: `/usr/bin/prometheus-node-exporter`, systemd unit and init script, `/etc/default/prometheus-node-exporter`, the textfile directory `/var/lib/prometheus/node-exporter`, logrotate and the man page. The package keeps Debian's name, so it replaces the distribution's on upgrade.

Upstream supports only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/prometheus-node-exporter <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 1.5.0 |
| trixie | 1.9.0 |
| jammy | 1.3.1 |
| noble | 1.7.0 |
| resolute | 1.10.2 |

## Changes to Debian's packaging

- **`overlays/go-build.patch`** (all releases): Debian builds against Debian-packaged Go libraries that only exist in unstable. We build with the pinned Go toolchain from `config.yaml` and vendored dependencies verified against `go.sum` and sum.golang.org (see [Go programs](../../docs/packages.md#go-programs)), like upstream's release binaries: `CGO_ENABLED=0`, the same version stamps (`--version` and `node_exporter_build_info` show the upstream commit as revision). Upstream's test suite runs during the build, with its fixtures, as upstream's `make test` does.
- **`patches/main/`:** Debian's `0001` (Debian defaults: systemd collector on, textfile directory `/var/lib/prometheus/node-exporter`, `/run`, `/mnt` and `/media` mounts and `.target` units excluded, the deprecated whitelist/blacklist flags removed), so the defaults stay what the distributions' versions had. `0002` (reverts the kingpin import path to Debian's library name) and `0003` (a procfs fix that's part of 1.12) are dropped.
- `prometheus-node-exporter-collectors` (the textfile scripts for apt, smartmon, ipmitool etc. and their timers) is a separate source package and stays the distribution's; the textfile format hasn't changed.

## Tests

- **Smoke:** version and revision, static linking, the service starts through the init script as `prometheus` and serves CPU, memory, load, network and uname metrics plus a metric from the textfile directory, and `--web.config.file` with basic auth refuses requests without credentials and accepts them with.
- **Upgrade:** the distribution's version (1.3.1 on jammy) runs with custom `ARGS` (another port, only the cpu and textfile collectors) and a textfile metric. Ours has to keep the configuration and serve the same metrics on the same port, without the disabled collectors.
