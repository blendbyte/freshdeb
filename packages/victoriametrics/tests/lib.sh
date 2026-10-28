#!/bin/bash
#
# Helpers for the VictoriaMetrics tests (sourced, not run): starting and
# stopping the service through its init script, writing samples and querying
# them back. They work with the distributions' versions as well as with ours.

# Where the service listens; the upgrade test moves it to another port.
VM=http://127.0.0.1:8428
# shellcheck disable=SC2034  # used by the scripts that source this
DEFAULTS=/etc/default/victoria-metrics

# curl comes from the distribution; the test containers don't have it.
command -v curl >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl >/dev/null

show_log() {
    tail -n 40 /var/log/victoria-metrics/victoria-metrics.log >&2 || true
}

# The users running a VictoriaMetrics process, one per line. From /proc: the
# images have no procps. (comm is cut to 15 characters.)
vm_users() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == victoria-metric* ]] && stat -c %U "$proc"
    done
    return 0
}

# wait_healthy <url>: until a VictoriaMetrics process answers there.
wait_healthy() {
    for _ in $(seq 1 150); do
        [[ "$(curl -fsS "$1/health" 2>/dev/null)" == OK ]] && return 0
        sleep 0.2
    done
    echo "VictoriaMetrics does not answer at $1" >&2
    show_log
    return 1
}

start_vm() {
    service victoria-metrics start
    wait_healthy "$VM"
}

stop_vm() {
    service victoria-metrics stop
    for _ in $(seq 1 150); do
        [[ -z "$(vm_users)" ]] && return 0
        sleep 0.2
    done
    echo "VictoriaMetrics does not stop" >&2
    return 1
}

# write_samples [url] < text: samples in Prometheus' text format (timestamps
# in milliseconds), made searchable right away.
write_samples() {
    local url="${1:-$VM}"
    curl -fsS --data-binary @- "$url/api/v1/import/prometheus"
    curl -fsS "$url/internal/force_flush" >/dev/null
}

# value <query> [time] [url]: the instant query's single value, or nothing.
value() {
    local reply
    reply="$(curl -fsS -G "${3:-$VM}/api/v1/query" --data-urlencode "query=$1" \
        ${2:+--data-urlencode "time=$2"} --data-urlencode nocache=1)"
    sed -n 's/.*"value":\[[^,]*,"\([^"]*\)"\].*/\1/p' <<<"$reply"
}

# expect_value <query> <value> [time] [url]: until the query returns the
# value (new samples take a moment to become searchable).
expect_value() {
    local got
    for _ in $(seq 1 60); do
        got="$(value "$1" "${3:-}" "${4:-$VM}")"
        [[ "$got" == "$2" ]] && return 0
        curl -fsS "${4:-$VM}/internal/force_flush" >/dev/null 2>&1 || true
        sleep 0.5
    done
    echo "$1${3:+ at $3}: expected $2, got '${got}'" >&2
    show_log
    return 1
}
