#!/bin/bash
#
# Smoke test for mysqld_exporter, run as root in a fresh container right
# after our package was installed (and again after an upgrade from the
# distro's version). It monitors the distribution's MariaDB through its init
# script as the prometheus user; nothing leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 0.21.0

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/prometheus-mysqld-exporter/tests/lib.sh
source "$(dirname "$0")/lib.sh"
trap 'kill "${direct_pid:-}" 2>/dev/null || true' EXIT

echo "binary reports the expected version"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
version="$(prometheus-mysqld-exporter --version 2>&1)"
[[ "$version" =~ ^"mysqld_exporter, version ${expected} (branch: HEAD, revision: "[0-9a-f]{40}")" ]] \
    || { echo "$version" >&2; exit 1; }

echo "static binary"
linkage="$(ldd /usr/bin/prometheus-mysqld-exporter 2>&1 || true)"
[[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$linkage" >&2; exit 1; }

echo "system user"
id prometheus

echo "DATA_SOURCE_NAME set: the service says it's no longer supported"
defaults=/etc/default/prometheus-mysqld-exporter
saved="$(mktemp)"
cp "$defaults" "$saved"
echo "DATA_SOURCE_NAME='prometheus:x@unix(/run/mysqld/mysqld.sock)/'" >> "$defaults"
service prometheus-mysqld-exporter start || true
log="$(cat /var/log/prometheus/prometheus-mysqld-exporter.log)"
[[ "$log" == *"DATA_SOURCE_NAME is no longer supported"* ]] || { tail -n 20 <<<"$log" >&2; exit 1; }
stop_exporter
cp "$saved" "$defaults"
rm -f "$saved"

echo "MariaDB with a monitoring user, credentials in ~prometheus/.my.cnf"
start_mariadb
write_my_cnf

echo "service starts as the prometheus user and monitors the server"
start_exporter 9104
test "$(exporter_users)" = prometheus
expect_metrics 9104 \
    'mysqld_exporter_build_info{branch="HEAD",' \
    'mysql_up 1' 'mysql_global_status_uptime ' 'mysql_version_info{' \
    'mysql_exporter_collector_success{collector="collect.global_status"} 1' \
    'mysql_exporter_collector_success{collector="collect.slave_status"} 1'
scrape="$(curl -fsS http://127.0.0.1:9104/metrics)"
[[ "$scrape" == *"version=\"${expected}\""* ]] || { echo "build_info has no version ${expected}" >&2; exit 1; }
stop_exporter

echo "without a file: --mysqld.username, --mysqld.address and MYSQLD_EXPORTER_PASSWORD"
runuser -u prometheus -- env MYSQLD_EXPORTER_PASSWORD="$DB_PASSWORD" \
    prometheus-mysqld-exporter --config.my-cnf=/nonexistent --mysqld.username=prometheus \
    --mysqld.address="unix://$DB_SOCKET" --web.listen-address=127.0.0.1:19104 \
    >/tmp/direct.log 2>&1 &
direct_pid=$!
wait_metrics 19104 || { cat /tmp/direct.log >&2; exit 1; }
expect_metrics 19104 'mysql_up 1' || { cat /tmp/direct.log >&2; exit 1; }
kill "$direct_pid"

echo "man page is packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L prometheus-mysqld-exporter)"
grep -qxF /usr/share/man/man1/prometheus-mysqld-exporter.1.gz <<<"$files" \
    || { echo "missing from the package: the man page" >&2; exit 1; }
echo "smoke test passed"
