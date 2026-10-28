#!/bin/bash
#
# Helpers for the blackbox_exporter tests (sourced, not run): starting and
# stopping the service through its init script, and probing through it.
# They work with the distributions' versions as well as with ours.

# shellcheck disable=SC2034  # used by the scripts that source this
CONFIG=/etc/prometheus/blackbox.yml

# curl comes from the distribution; the test containers don't have it.
command -v curl >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl >/dev/null

show_log() {
    tail -n 40 /var/log/prometheus/prometheus-blackbox-exporter.log >&2 || true
}

# The users running a blackbox exporter process, one per line. From /proc:
# the images have no procps.
exporter_users() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == prometheus-blac* ]] && stat -c %U "$proc"
    done
    return 0
}

# start_exporter <port>: start the service, wait until it answers.
start_exporter() {
    service prometheus-blackbox-exporter start
    wait_exporter "$1"
}

wait_exporter() {
    for _ in $(seq 1 100); do
        curl -fsS "http://127.0.0.1:$1/metrics" >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    echo "blackbox exporter does not answer on port $1" >&2
    show_log
    return 1
}

stop_exporter() {
    service prometheus-blackbox-exporter stop
    for _ in $(seq 1 100); do
        [[ -z "$(exporter_users)" ]] && return 0
        sleep 0.2
    done
    echo "blackbox exporter does not stop" >&2
    return 1
}

# add_module <name>: a TCP module, first in the configuration (the shipped
# files don't all end with a newline).
add_module() {
    sed -i "s/^modules:\$/modules:\n  $1:\n    prober: tcp\n    timeout: 5s/" "$CONFIG"
    grep -q "^  $1:\$" "$CONFIG"
}

# probe <port> <module> <target>: the metrics of one probe.
probe() {
    curl -fsS "http://127.0.0.1:$1/probe?module=$2&target=$3"
}

# expect_probe <port> <module> <target> <line>...: every line (a metric with
# its labels and value) must be in the probe's result.
expect_probe() {
    local port="$1" module="$2" target="$3" result line
    shift 3
    result="$(probe "$port" "$module" "$target")"
    for line in "$@"; do
        [[ $'\n'"$result"$'\n' == *$'\n'"$line"$'\n'* ]] || {
            echo "probe $module $target: missing $line" >&2
            echo "$result" >&2
            return 1
        }
    done
}
