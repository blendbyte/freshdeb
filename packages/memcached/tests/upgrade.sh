#!/bin/bash
#
# Upgrade test for memcached. It's a cache, so there's no data to carry
# over; what must survive is the admin's configuration: postinst only
# creates /etc/memcached.conf when it doesn't exist. "seed" changes the
# memory limit on the distribution's package, "check" makes sure our build
# still runs with it.

set -euo pipefail

limit_bytes() {
    exec 3<>/dev/tcp/127.0.0.1/11211
    printf 'stats settings\r\n' >&3
    local line
    while IFS= read -r -t 5 line <&3; do
        line="${line%$'\r'}"
        [[ "$line" == "END" ]] && break
        [[ "$line" == "STAT maxbytes "* ]] && echo "${line#STAT maxbytes }"
    done
    exec 3<&-
}

wait_for_server() {
    for _ in $(seq 1 50); do
        [[ -n "$(limit_bytes 2>/dev/null)" ]] && return 0
        sleep 0.2
    done
    echo "memcached does not answer" >&2
    return 1
}

case "${1:?seed or check}" in
    seed)
        sed -i 's/^-m 64$/-m 128/' /etc/memcached.conf
        grep -qx -- '-m 128' /etc/memcached.conf
        service memcached restart
        wait_for_server
        test "$(limit_bytes)" = 134217728
        service memcached stop
        ;;
    check)
        grep -qx -- '-m 128' /etc/memcached.conf
        service memcached restart
        wait_for_server
        test "$(limit_bytes)" = 134217728
        memcached -V | grep -Fx "memcached ${FRESHDEB_UPSTREAM_VERSION:?}"
        service memcached stop
        ;;
esac
