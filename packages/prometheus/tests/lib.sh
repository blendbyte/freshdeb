#!/bin/bash
#
# Helpers for the Prometheus tests (sourced, not run): starting and stopping
# the service through its init script, and queries over the HTTP API. They
# work with the distributions' Prometheus 2 as well as with ours.

PROM=http://127.0.0.1:9090

# curl comes from the distribution; the test containers don't have it.
command -v curl >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl >/dev/null

show_log() {
    tail -n 40 /var/log/prometheus/prometheus.log >&2 || true
}

start_prometheus() {
    service prometheus start
    for _ in $(seq 1 150); do
        curl -fsS "$PROM/-/ready" >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    echo "Prometheus does not get ready" >&2
    show_log
    return 1
}

# The users running a prometheus process, one per line. From /proc: the
# images have no procps.
prometheus_users() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == prometheus ]] && stat -c %U "$proc"
    done
    return 0
}

stop_prometheus() {
    service prometheus stop
    for _ in $(seq 1 100); do
        [[ -z "$(prometheus_users)" ]] && return 0
        sleep 0.2
    done
    echo "Prometheus does not stop" >&2
    return 1
}

# query <expr> [<time>]: the API's JSON answer for an instant query.
query() {
    local args=(--data-urlencode "query=$1")
    [[ -n "${2:-}" ]] && args+=(--data-urlencode "time=$2")
    curl -fsS "$PROM/api/v1/query" "${args[@]}"
}

# value <expr> [<time>]: the value of the first result, empty without one.
value() {
    local out
    out="$(query "$@")"
    [[ "$out" =~ \"value\":\[[0-9.]+,\"([^\"]*)\"\] ]] && echo "${BASH_REMATCH[1]}"
    return 0
}

# expect_value <expr> <value> [<time>]: wait up to 30s for that value.
expect_value() {
    local got=""
    for _ in $(seq 1 60); do
        got="$(value "$1" "${3:-}")"
        [[ "$got" == "$2" ]] && return 0
        sleep 0.5
    done
    echo "$1${3:+ at $3}: got '$got', expected '$2'" >&2
    query "$1" "${3:-}" >&2 || true
    show_log
    return 1
}
