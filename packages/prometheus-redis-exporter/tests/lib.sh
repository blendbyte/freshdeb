#!/bin/bash
#
# Helpers for the redis_exporter tests (sourced, not run): Redis servers to
# scrape, starting and stopping the exporter through its init script, and
# scraping it. They work with the distributions' versions as well as with
# ours.

# curl and a Redis server come from the distribution; the test containers
# don't have them.
command -v curl >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl >/dev/null
command -v redis-server >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq redis-server >/dev/null

show_log() {
    tail -n 40 /var/log/prometheus/prometheus-redis-exporter.log >&2 || true
}

# start_redis <port> [password]: a throwaway Redis server on localhost,
# without persistence. One already on the port (the package's own, if its
# postinst started it) is shut down first.
start_redis() {
    local port="$1" password="${2:-}" auth=()
    [[ -n "$password" ]] && auth=(-a "$password" --no-auth-warning)
    redis-cli -p "$port" "${auth[@]}" shutdown nosave >/dev/null 2>&1 || true
    redis-server --port "$port" --bind 127.0.0.1 --save '' --appendonly no \
        --dir /tmp --daemonize yes ${password:+--requirepass "$password"} >/dev/null
    for _ in $(seq 1 50); do
        [[ "$(redis-cli -p "$port" "${auth[@]}" ping 2>/dev/null)" == PONG ]] && return 0
        sleep 0.2
    done
    echo "redis-server does not answer on port $port" >&2
    return 1
}

# stop_redis <port> [password]
stop_redis() {
    local port="$1" password="${2:-}" auth=()
    [[ -n "$password" ]] && auth=(-a "$password" --no-auth-warning)
    redis-cli -p "$port" "${auth[@]}" shutdown nosave >/dev/null 2>&1 || true
}

# The users running a redis exporter process, one per line. From /proc: the
# images have no procps. (comm is cut to 15 characters.)
exporter_users() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == prometheus-redi* ]] && stat -c %U "$proc"
    done
    return 0
}

# start_exporter <port>: start the service, wait until it serves metrics.
start_exporter() {
    service prometheus-redis-exporter start
    wait_metrics "$1"
}

wait_metrics() {
    for _ in $(seq 1 100); do
        curl -fsS "http://127.0.0.1:$1/metrics" >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    echo "redis exporter does not answer on port $1" >&2
    show_log
    return 1
}

stop_exporter() {
    service prometheus-redis-exporter stop
    for _ in $(seq 1 100); do
        [[ -z "$(exporter_users)" ]] && return 0
        sleep 0.2
    done
    echo "redis exporter does not stop" >&2
    return 1
}

# expect_metrics <port> <line>...: every line (a metric with its labels
# and value, or a prefix ending in a space) must be in one scrape.
expect_metrics() {
    local port="$1" scrape line
    shift
    scrape="$(curl -fsS "http://127.0.0.1:$port/metrics")"
    for line in "$@"; do
        [[ $'\n'"$scrape" == *$'\n'"$line"* ]] || { echo "missing from the scrape: $line" >&2; return 1; }
    done
}
