#!/bin/bash
#
# Helpers for the Alertmanager tests (sourced, not run): starting and
# stopping the service through its init script, and talking to it. They work
# with the distributions' versions as well as with ours.

# shellcheck disable=SC2034  # used by the scripts that source this
AM=http://127.0.0.1:9093

# curl comes from the distribution; the test containers don't have it.
command -v curl >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl >/dev/null

show_log() {
    tail -n 40 /var/log/prometheus/prometheus-alertmanager.log >&2 || true
}

# The users running an Alertmanager process, one per line. From /proc: the
# images have no procps. (comm is cut to 15 characters.)
alertmanager_users() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == prometheus-ale* ]] && stat -c %U "$proc"
    done
    return 0
}

# Start the service, wait until it answers.
start_alertmanager() {
    service prometheus-alertmanager start
    for _ in $(seq 1 150); do
        curl -fsS "$AM/-/ready" >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    echo "Alertmanager does not answer" >&2
    show_log
    return 1
}

stop_alertmanager() {
    service prometheus-alertmanager stop
    for _ in $(seq 1 150); do
        [[ -z "$(alertmanager_users)" ]] && return 0
        sleep 0.2
    done
    echo "Alertmanager does not stop" >&2
    return 1
}

# post_alert <alertname>: send a firing alert through API v2, as Prometheus
# does.
post_alert() {
    curl -fsS -H 'Content-Type: application/json' -d "[{\"labels\": {\"alertname\": \"$1\", \"severity\": \"none\"}}]" \
        "$AM/api/v2/alerts" >/dev/null
}

# wait_alert <alertname>: until amtool lists the alert (it goes through
# grouping first).
wait_alert() {
    local alerts
    for _ in $(seq 1 50); do
        alerts="$(amtool --alertmanager.url="$AM" alert query -o simple "alertname=$1" 2>&1 || true)"
        [[ "$alerts" == *"$1"* ]] && return 0
        sleep 0.2
    done
    echo "alert $1 is not listed: $alerts" >&2
    return 1
}
