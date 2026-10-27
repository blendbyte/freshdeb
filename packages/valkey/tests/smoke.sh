#!/bin/bash
#
# Smoke test for valkey, run as root in a fresh container right after our
# packages were installed (and again after an upgrade from the distro's
# version). There is no systemd in the container, so services start through
# their init scripts, the way Debian's autopkgtests do it.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 9.1.3

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"

wait_for() {
    local port="$1"
    for _ in $(seq 1 50); do
        [[ "$(valkey-cli -p "$port" ping 2>/dev/null)" == "PONG" ]] && return 0
        sleep 0.2
    done
    echo "nothing answers on port $port" >&2
    tail -n 30 /var/log/valkey/*.log >&2 || true
    return 1
}

owner_of() {
    stat -c %U "/proc/$(cat "$1")"
}

echo "binaries report the expected version"
valkey-server --version | grep -F "v=${expected} "
valkey-cli --version | grep -F "${expected}"

echo "system user, directories and config exist"
id valkey
test "$(stat -c %U:%G /var/lib/valkey)" = valkey:valkey
test -f /etc/valkey/valkey.conf
test -f /etc/valkey/sentinel.conf

echo "server starts as the valkey user"
service valkey-server restart
wait_for 6379
test "$(owner_of /run/valkey/valkey-server.pid)" = valkey
valkey-cli info server | grep -F "valkey_version:${expected}"

echo "basic commands, persistence and the bundled Lua libraries work"
valkey-cli set freshdeb:smoke ok
test "$(valkey-cli get freshdeb:smoke)" = ok
test "$(valkey-cli eval 'return cjson.encode({1,2})' 0)" = "[1,2]"
test "$(valkey-cli eval 'return bit.band(6, 3)' 0)" = "2"
valkey-cli save
valkey-check-rdb /var/lib/valkey/dump.rdb

echo "TLS support is compiled in"
# Capture first: "cmd | grep -q" trips pipefail when grep exits early.
cli_help="$(valkey-cli --help 2>&1 || true)"
[[ "$cli_help" == *--tls* ]]

echo "sentinel starts"
service valkey-sentinel restart
wait_for 26379
valkey-cli -p 26379 sentinel masters >/dev/null

service valkey-sentinel stop
service valkey-server stop
echo "smoke test passed"
