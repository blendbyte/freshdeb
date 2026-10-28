#!/bin/bash
#
# Helpers for the tindra tests (sourced, not run): the distribution's
# PostgreSQL, the CLI and the server as the tindra user with the packaged
# configuration, and the HTTP API.

# shellcheck disable=SC2034  # used by the scripts that source this
CONF=/etc/tindra/tindra.env
URL=http://127.0.0.1:8080
WORK="$(mktemp -d)"
chmod 0755 "$WORK"
# No .env here: tindra reads /etc/tindra/tindra.env, as an admin's commands do.

# curl and PostgreSQL come from the distribution; the test containers don't
# have them.
if ! command -v curl >/dev/null || ! command -v pg_lsclusters >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl postgresql >/dev/null
fi

start_postgresql() {
    service postgresql start >/dev/null
    for _ in $(seq 1 100); do
        pg_isready -q && return 0
        sleep 0.2
    done
    pg_isready
}

psql_value() {
    (cd / && runuser -u postgres -- psql -X -Atc "$1")
}

# tindra_cli ARGS: the CLI as the tindra user with the configuration.
tindra_cli() {
    (cd "$WORK" && runuser -u tindra -- tindra "$@")
}

# conf_value KEY: a key's value in the configuration.
conf_value() {
    sed -n "s/^$1=//p" "$CONF" | tail -n 1
}

# reconfigure KEY TYPE VALUE ...: preseed answers, then dpkg-reconfigure.
reconfigure() {
    while (( $# )); do
        printf 'tindra tindra/%s %s %s\n' "$1" "$2" "$3"
        shift 3
    done | debconf-set-selections
    dpkg-reconfigure -fnoninteractive tindra
}

start_tindra() {
    (cd "$WORK" && exec runuser -u tindra -- tindra serve) >"$WORK/serve.log" 2>&1 &
    TINDRA_PID=$!
    for _ in $(seq 1 150); do
        curl -fsS "$URL/healthz" >/dev/null 2>&1 && return 0
        kill -0 "$TINDRA_PID" 2>/dev/null || break
        sleep 0.2
    done
    echo "tindra serve does not answer" >&2
    tail -n 40 "$WORK/serve.log" >&2
    return 1
}

# tindra_pid: the server's process (start_tindra's PID is runuser's).
tindra_pid() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == tindra && "$(stat -c %U "$proc")" == tindra ]] \
            && { basename "$proc"; return 0; }
    done
    return 1
}

stop_tindra() {
    [[ -n "${TINDRA_PID:-}" ]] || return 0
    kill "$TINDRA_PID" 2>/dev/null || true
    wait "$TINDRA_PID" 2>/dev/null || true
    TINDRA_PID=
}
