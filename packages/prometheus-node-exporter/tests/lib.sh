#!/bin/bash
#
# Helpers for the node_exporter tests (sourced, not run): starting and
# stopping the service through its init script, and scraping it. They work
# with the distributions' versions as well as with ours.

# shellcheck disable=SC2034  # used by the scripts that source this
TEXTFILE_DIR=/var/lib/prometheus/node-exporter

# curl comes from the distribution; the test containers don't have it.
command -v curl >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl >/dev/null

show_log() {
    tail -n 40 /var/log/prometheus/prometheus-node-exporter.log >&2 || true
}

# The users running a node exporter process, one per line. From /proc: the
# images have no procps.
exporter_users() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == prometheus-node* ]] && stat -c %U "$proc"
    done
    return 0
}

# start_exporter <port>: start the service, wait until it serves metrics.
start_exporter() {
    service prometheus-node-exporter start
    wait_metrics "$1"
}

wait_metrics() {
    for _ in $(seq 1 100); do
        curl -fsS "http://127.0.0.1:$1/metrics" >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    echo "node exporter does not answer on port $1" >&2
    show_log
    return 1
}

stop_exporter() {
    service prometheus-node-exporter stop
    for _ in $(seq 1 100); do
        [[ -z "$(exporter_users)" ]] && return 0
        sleep 0.2
    done
    echo "node exporter does not stop" >&2
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
