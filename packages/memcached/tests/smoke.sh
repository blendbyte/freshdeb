#!/bin/bash
#
# Smoke test for memcached, run as root in a fresh container right after our
# package was installed (and again after an upgrade from the distro's
# version). There is no systemd in the container, so the service starts
# through its init script.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 1.6.45

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"

# One request over memcached's text protocol; prints the reply up to END,
# STORED, a VERSION line or an error.
ask() {
    exec 3<>/dev/tcp/127.0.0.1/11211
    printf '%b' "$1" >&3
    local line
    while IFS= read -r -t 5 line <&3; do
        line="${line%$'\r'}"
        echo "$line"
        case "$line" in END|STORED|VERSION\ *|ERROR*|CLIENT_ERROR*|SERVER_ERROR*) break ;; esac
    done
    exec 3<&-
}

wait_for_server() {
    for _ in $(seq 1 50); do
        # Capture first: "cmd | grep -q" trips pipefail when grep exits early.
        [[ "$(ask 'version\r\n' 2>/dev/null || true)" == VERSION\ * ]] && return 0
        sleep 0.2
    done
    echo "memcached does not answer" >&2
    tail -n 30 /var/log/memcached.log >&2 || true
    return 1
}

echo "binary reports the expected version"
memcached -V | grep -Fx "memcached ${expected}"

echo "system user and config exist"
id memcache
test -f /etc/memcached.conf

echo "TLS, SASL and the proxy are compiled in"
help="$(memcached -h 2>&1 || true)"
[[ "$help" == *enable-ssl* ]]
[[ "$help" == *enable-sasl* ]]
[[ "$help" == *proxy_config* ]]

echo "server starts as the memcache user"
service memcached restart
wait_for_server
# Not via the pid file: /run/memcached comes from tmpfiles.d, which only
# runs where systemd does (not in this container).
owners=""
for proc in /proc/[0-9]*; do
    [[ "$(cat "$proc/comm" 2>/dev/null)" == memcached ]] && owners+="$(stat -c %U "$proc") "
done
test "$owners" = "memcache " 
ask 'version\r\n' | grep -Fx "VERSION ${expected}"

echo "set, get and stats work"
ask 'set freshdeb:smoke 0 0 2\r\nok\r\n' | grep -Fx STORED
ask 'get freshdeb:smoke\r\n' | grep -Fx ok
ask 'stats\r\n' | grep -E '^STAT curr_items 1$'

echo "memcached-tool works"
# Its URI::Escape comes from liburi-perl, which Debian only Suggests.
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq liburi-perl >/dev/null
/usr/share/memcached/scripts/memcached-tool 127.0.0.1:11211 stats | grep -E 'curr_items +1'

service memcached stop
echo "smoke test passed"
