#!/bin/bash
#
# Smoke test for redis_exporter, run as root in a fresh container right after
# our package was installed (and again after an upgrade from the distro's
# version). It scrapes the distribution's redis-server through its init
# script as the prometheus user; nothing leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 1.96.0

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/prometheus-redis-exporter/tests/lib.sh
source "$(dirname "$0")/lib.sh"
work="$(mktemp -d)"
trap 'kill "${auth_pid:-}" "${noauth_pid:-}" 2>/dev/null || true; stop_redis 6379; stop_redis 6390 freshdeb-secret; rm -rf "$work"' EXIT

echo "binary reports the expected version"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
version="$(prometheus-redis-exporter --version 2>&1)"
pattern="Redis Metrics Exporter v${expected//./\\.} +build date: [^ ]+ +sha1: [0-9a-f]{40} "
[[ "$version" =~ $pattern ]] || { echo "$version" >&2; exit 1; }

echo "static binary"
linkage="$(ldd /usr/bin/prometheus-redis-exporter 2>&1 || true)"
[[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$linkage" >&2; exit 1; }

echo "system user"
id prometheus

echo "service starts as the prometheus user and scrapes Redis"
start_redis 6379
redis-cli -p 6379 set freshdeb-smoke 1 >/dev/null
start_exporter 9121
test "$(exporter_users)" = prometheus
expect_metrics 9121 'redis_up 1' 'redis_connected_clients ' 'redis_db_keys{db="db0"} 1' \
    'redis_commands_processed_total ' 'redis_instance_info{'
scrape="$(curl -fsS http://127.0.0.1:9121/metrics)"
info="$(grep '^redis_exporter_build_info{' <<<"$scrape" || true)"
[[ "$info" == *"version=\"v${expected}\""* && "$info" =~ commit_sha=\"[0-9a-f]{40}\" ]] \
    || { echo "build_info has no commit or version v${expected}: $info" >&2; exit 1; }

echo "no reload (SIGHUP would stop it); force-reload restarts"
if service prometheus-redis-exporter reload >/dev/null 2>&1; then
    echo "service prometheus-redis-exporter reload succeeded" >&2; exit 1
fi
service prometheus-redis-exporter force-reload
wait_metrics 9121
stop_exporter

echo "password from --redis.password-file"
start_redis 6390 freshdeb-secret
printf '{"redis://localhost:6390": "freshdeb-secret"}\n' > "$work/passwords.json"
chgrp prometheus "$work/passwords.json"
chmod 0640 "$work/passwords.json"
chmod 0755 "$work"
runuser -u prometheus -- prometheus-redis-exporter --redis.addr=redis://localhost:6390 \
    --redis.password-file="$work/passwords.json" --web.listen-address=127.0.0.1:19121 >"$work/auth.log" 2>&1 &
auth_pid=$!
# The same server without it: the exporter has to report it down.
runuser -u prometheus -- prometheus-redis-exporter --redis.addr=redis://localhost:6390 \
    --web.listen-address=127.0.0.1:19122 >"$work/noauth.log" 2>&1 &
noauth_pid=$!
wait_metrics 19121 || { cat "$work/auth.log" >&2; exit 1; }
wait_metrics 19122 || { cat "$work/noauth.log" >&2; exit 1; }
expect_metrics 19121 'redis_up 1' || { cat "$work/auth.log" >&2; exit 1; }
expect_metrics 19122 'redis_up 0'
kill "$auth_pid" "$noauth_pid"

echo "man page is packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L prometheus-redis-exporter)"
grep -qxF /usr/share/man/man1/prometheus-redis-exporter.1.gz <<<"$files" \
    || { echo "missing from the package: the man page" >&2; exit 1; }
echo "smoke test passed"
