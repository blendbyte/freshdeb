#!/bin/bash
#
# Smoke test for snmp_exporter, run as root in a fresh container right after
# our package was installed (and again after an upgrade from the distro's
# version). The generator writes a configuration from a small MIB, and the
# exporter, running through its init script as the prometheus user, scrapes
# snmpd from the distribution on localhost; nothing leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 0.31.0

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/prometheus-snmp-exporter/tests/lib.sh
source "$(dirname "$0")/lib.sh"
work="$(mktemp -d)"
config=/etc/prometheus/snmp.yml
cp -p "$config" "$work/snmp.yml.orig"
trap '[[ -s "$SNMP_DIR/snmpd.pid" ]] && stop_snmpd; cp -p "$work/snmp.yml.orig" "$config"; rm -rf "$work"' EXIT

echo "binary reports the expected version"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
version="$(prometheus-snmp-exporter --version 2>&1)"
[[ "$version" =~ ^"snmp_exporter, version ${expected} (branch: HEAD, revision: "[0-9a-f]{40}")" ]] \
    || { echo "$version" >&2; exit 1; }

echo "static exporter, generator linked to net-snmp"
linkage="$(ldd /usr/bin/prometheus-snmp-exporter 2>&1 || true)"
[[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$linkage" >&2; exit 1; }
linkage="$(ldd /usr/bin/prometheus-snmp-generator 2>&1)"
[[ "$linkage" == *libnetsnmp* && "$linkage" != *"not found"* ]] || { echo "$linkage" >&2; exit 1; }

echo "generator writes a configuration the exporter accepts"
generate new "$work/snmp.yml"
grep -qx '  freshdeb:' "$work/snmp.yml" || { cat "$work/snmp.yml" >&2; exit 1; }
prometheus-snmp-exporter --dry-run --config.file="$work/snmp.yml"
install -m 0644 "$work/snmp.yml" "$config"

echo "service starts as the prometheus user and scrapes snmpd"
id prometheus
start_snmpd
start_exporter
test "$(exporter_users)" = prometheus
expect_agent_metrics
metrics="$(curl -fsS http://127.0.0.1:9116/metrics)"
[[ "$metrics" == *"snmp_exporter_build_info{"*"version=\"${expected}\""* ]] \
    || { echo "build_info has no version ${expected}" >&2; exit 1; }

echo "configuration reloads"
curl -fsS -X POST http://127.0.0.1:9116/-/reload
expect_agent_metrics
stop_exporter
stop_snmpd

echo "man pages are packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L prometheus-snmp-exporter)"
for page in prometheus-snmp-exporter prometheus-snmp-generator; do
    grep -qxF "/usr/share/man/man1/$page.1.gz" <<<"$files" \
        || { echo "missing from the package: the man page $page" >&2; exit 1; }
done
echo "smoke test passed"
