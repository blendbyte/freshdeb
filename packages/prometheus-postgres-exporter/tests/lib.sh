#!/bin/bash
#
# Helpers for the postgres_exporter tests (sourced, not run): a local
# PostgreSQL from the distribution with a "prometheus" role, starting and
# stopping the exporter through its init script, and scraping it. They work
# with the distributions' versions as well as with ours.

DEFAULTS=/etc/default/prometheus-postgres-exporter
# The connection README.Debian recommends: the prometheus role over the
# local socket, with peer authentication.
# shellcheck disable=SC2034  # used by the scripts that source this
DSN='user=prometheus host=/run/postgresql dbname=postgres'

# curl and PostgreSQL come from the distribution; the test containers don't
# have them.
if ! command -v curl >/dev/null || ! command -v pg_ctlcluster >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl postgresql >/dev/null
fi

show_log() {
    tail -n 40 /var/log/prometheus/prometheus-postgres-exporter.log >&2 || true
}

# Start the distribution's cluster (if it isn't running) and create the
# prometheus role with the pg_monitor role, as README.Debian describes.
start_postgresql() {
    service postgresql start
    for _ in $(seq 1 100); do
        pg_isready -q -h /run/postgresql && break
        sleep 0.2
    done
    pg_isready -h /run/postgresql
    runuser -u postgres -- psql -q -v ON_ERROR_STOP=1 <<'SQL'
SELECT 'CREATE ROLE prometheus LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'prometheus') \gexec
GRANT pg_monitor TO prometheus;
SQL
}

# set_default <VAR> <value>: set a variable in the defaults file.
set_default() {
    sed -i "s|^$1=.*|$1='$2'|" "$DEFAULTS"
    grep -qxF "$1='$2'" "$DEFAULTS"
}

# The users running a postgres exporter process, one per line. From /proc:
# the images have no procps.
exporter_users() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == prometheus-post* ]] && stat -c %U "$proc"
    done
    return 0
}

# start_exporter <port>: start the service, wait until it serves metrics.
start_exporter() {
    service prometheus-postgres-exporter start
    wait_metrics "$1"
}

wait_metrics() {
    for _ in $(seq 1 100); do
        curl -fsS "http://127.0.0.1:$1/metrics" >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    echo "postgres exporter does not answer on port $1" >&2
    show_log
    return 1
}

stop_exporter() {
    service prometheus-postgres-exporter stop
    for _ in $(seq 1 100); do
        [[ -z "$(exporter_users)" ]] && return 0
        sleep 0.2
    done
    echo "postgres exporter does not stop" >&2
    return 1
}

# expect_metrics <port> <line>...: every line (a metric with its labels
# and value, or a prefix) must be in one scrape.
expect_metrics() {
    local port="$1" scrape line
    shift
    scrape="$(curl -fsS "http://127.0.0.1:$port/metrics")"
    for line in "$@"; do
        [[ $'\n'"$scrape" == *$'\n'"$line"* ]] || { echo "missing from the scrape: $line" >&2; show_log; return 1; }
    done
}
