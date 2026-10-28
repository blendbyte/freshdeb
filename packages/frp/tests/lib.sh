#!/bin/bash
#
# Helpers for the frp tests (sourced, not run): running frps and frpc as the
# frp user against local configurations, and waiting for HTTP endpoints.

# curl and busybox (a static file server) come from the distribution; the
# test containers don't have them.
if ! command -v curl >/dev/null || ! command -v busybox >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl busybox >/dev/null
fi

# The users running frps or frpc processes, one per line. From /proc: the
# images have no procps.
frp_users() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == frp[cs] ]] && stat -c %U "$proc"
    done
    return 0
}

# wait_http <url> [curl args...]: wait until the URL answers with success.
wait_http() {
    local url="$1"
    shift
    for _ in $(seq 1 100); do
        curl -fsS -o /dev/null "$@" "$url" 2>/dev/null && return 0
        sleep 0.2
    done
    echo "no answer from $url" >&2
    return 1
}

# The systemd unit directory of this release (/lib or /usr/lib).
unit_dir() {
    local dir
    for dir in /usr/lib/systemd/system /lib/systemd/system; do
        [[ -e "$dir/$1" ]] && { echo "$dir"; return 0; }
    done
    return 1
}
