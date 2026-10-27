#!/bin/bash
#
# Upgrade test for valkey: "seed" runs against the distribution's own
# package, "check" after upgrading to ours. The dataset written by the old
# version must still be there and readable by the new one.

set -euo pipefail

wait_for_server() {
    for _ in $(seq 1 50); do
        [[ "$(valkey-cli ping 2>/dev/null)" == "PONG" ]] && return 0
        sleep 0.2
    done
    echo "valkey-server did not start" >&2
    tail -n 30 /var/log/valkey/valkey-server.log >&2 || true
    return 1
}

case "${1:?seed or check}" in
    seed)
        service valkey-server restart
        wait_for_server
        valkey-cli info server | grep -E '^(valkey|redis)_version:'
        valkey-cli set freshdeb:upgrade "written-by-distro-version"
        valkey-cli rpush freshdeb:list a b c
        valkey-cli hset freshdeb:hash field value
        valkey-cli save
        service valkey-server stop
        ;;
    check)
        service valkey-server restart
        wait_for_server
        valkey-cli info server | grep -F "valkey_version:${FRESHDEB_UPSTREAM_VERSION:?}"
        test "$(valkey-cli get freshdeb:upgrade)" = "written-by-distro-version"
        test "$(valkey-cli lrange freshdeb:list 0 -1 | tr '\n' ' ')" = "a b c "
        test "$(valkey-cli hget freshdeb:hash field)" = value
        service valkey-server stop
        ;;
esac
