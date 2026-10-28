#!/bin/bash
#
# Upgrade test for redis_exporter: "seed" points the distribution's version
# at another Redis server and port through ARGS in
# /etc/default/prometheus-redis-exporter, "check" makes sure ours keeps that
# configuration and scrapes the same server.

set -euo pipefail

# shellcheck source=packages/prometheus-redis-exporter/tests/lib.sh
source "$(dirname "$0")/lib.sh"
# Flags every version since 1.0 knows: another Redis server, another port.
args='--redis.addr=redis://localhost:6380 --web.listen-address=127.0.0.1:9122'

check_scrape() {
    start_redis 6380
    redis-cli -p 6380 mset freshdeb-a 1 freshdeb-b 2 >/dev/null
    start_exporter 9122
    expect_metrics 9122 'redis_up 1' 'redis_db_keys{db="db0"} 2' 'redis_connected_clients '
    stop_exporter
    stop_redis 6380
}

case "${1:?seed or check}" in
    seed)
        version="$(prometheus-redis-exporter --version 2>&1)"
        echo "${version%%$'\n'*}"
        sed -i "s|^ARGS=.*|ARGS=\"$args\"|" /etc/default/prometheus-redis-exporter
        check_scrape
        ;;
    check)
        version="$(prometheus-redis-exporter --version 2>&1)"
        [[ "$version" == *"Redis Metrics Exporter v${FRESHDEB_UPSTREAM_VERSION:?} "* ]] || { echo "$version" >&2; exit 1; }
        grep -qxF "ARGS=\"$args\"" /etc/default/prometheus-redis-exporter
        check_scrape
        # Back to the defaults for the smoke test that follows.
        sed -i 's|^ARGS=.*|ARGS=""|' /etc/default/prometheus-redis-exporter
        ;;
esac
