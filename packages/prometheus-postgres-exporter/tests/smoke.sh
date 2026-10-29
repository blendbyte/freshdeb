#!/bin/bash
#
# Smoke test for postgres_exporter, run as root in a fresh container right
# after our package was installed (and again after an upgrade from the
# distro's version). It runs through its init script as the prometheus user
# against the distribution's PostgreSQL; nothing leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 0.20.1

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/prometheus-postgres-exporter/tests/lib.sh
source "$(dirname "$0")/lib.sh"

echo "binary reports the expected version"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
version="$(prometheus-postgres-exporter --version 2>&1)"
[[ "$version" =~ ^"postgres_exporter, version ${expected} (branch: HEAD, revision: "[0-9a-f]{40}")" ]] \
    || { echo "$version" >&2; exit 1; }

echo "static binary"
linkage="$(ldd /usr/bin/prometheus-postgres-exporter 2>&1 || true)"
[[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$linkage" >&2; exit 1; }

echo "system user and protected defaults file"
id prometheus
test "$(stat -c '%U %a' "$DEFAULTS")" = "prometheus 640"

echo "service starts as the prometheus user and serves PostgreSQL metrics"
start_postgresql
set_default DATA_SOURCE_NAME "$DSN"
start_exporter 9187
test "$(exporter_users)" = prometheus
expect_metrics 9187 \
    'pg_up 1' \
    'postgres_exporter_build_info{branch="HEAD",' \
    'pg_database_size_bytes{datname="postgres"} ' \
    'pg_stat_database_xact_commit{datid=' \
    'pg_settings_max_connections ' \
    'pg_scrape_collector_success{collector="database"} 1'
scrape="$(curl -fsS http://127.0.0.1:9187/metrics)"
[[ "$scrape" == *"version=\"${expected}\""* ]] || { echo "build_info has no version ${expected}" >&2; exit 1; }
stop_exporter

echo "the init script takes DATA_SOURCE_URI and DATA_SOURCE_USER instead"
set_default DATA_SOURCE_NAME ''
printf "DATA_SOURCE_URI='%s'\nDATA_SOURCE_USER='prometheus'\n" '/postgres?host=/run/postgresql' >>"$DEFAULTS"
start_exporter 9187
expect_metrics 9187 'pg_up 1'
stop_exporter

echo "without a connection, the init script fails to start it"
sed -i '/^DATA_SOURCE_URI=/d; /^DATA_SOURCE_USER=/d' "$DEFAULTS"
if service prometheus-postgres-exporter start; then
    echo "started without DATA_SOURCE_NAME or DATA_SOURCE_URI" >&2
    exit 1
fi
test -z "$(exporter_users)"

echo "man page is packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L prometheus-postgres-exporter)"
grep -qxF /usr/share/man/man1/prometheus-postgres-exporter.1.gz <<<"$files" \
    || { echo "missing from the package: the man page" >&2; exit 1; }
echo "smoke test passed"
