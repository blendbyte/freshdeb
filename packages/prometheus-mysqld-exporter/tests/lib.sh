#!/bin/bash
#
# Helpers for the mysqld_exporter tests (sourced, not run): a MariaDB server
# with a monitoring user, the exporter's credentials file, starting and
# stopping the exporter through its init script, and scraping it. They work
# with the distributions' versions as well as with ours.

# shellcheck disable=SC2034  # used by the scripts that source this
MY_CNF=/var/lib/prometheus/.my.cnf
DB_PASSWORD=freshdeb-secret
DB_SOCKET=/run/mysqld/mysqld.sock

# curl comes from the distribution; the test containers don't have it.
command -v curl >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl >/dev/null

show_log() {
    tail -n 40 /var/log/prometheus/prometheus-mysqld-exporter.log >&2 || true
}

# The users running a mysqld exporter process, one per line. From /proc: the
# images have no procps. comm is cut to 15 characters.
exporter_users() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == prometheus-mysq* ]] && stat -c %U "$proc"
    done
    return 0
}

# start_mariadb: the distribution's MariaDB, running, with the monitoring
# user the defaults file asks for (every MariaDB here is 10.5 or later).
start_mariadb() {
    command -v mariadb-admin >/dev/null \
        || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mariadb-server >/dev/null
    if ! mariadb-admin ping >/dev/null 2>&1; then
        local init
        for init in mariadb mysql; do
            [[ -x "/etc/init.d/$init" ]] && break
        done
        service "$init" start
    fi
    for _ in $(seq 1 100); do
        mariadb-admin ping >/dev/null 2>&1 && break
        sleep 0.2
    done
    mariadb <<EOF
CREATE USER IF NOT EXISTS 'prometheus'@'localhost' IDENTIFIED BY '$DB_PASSWORD';
GRANT PROCESS, REPLICATION CLIENT, SELECT ON *.* TO 'prometheus'@'localhost';
GRANT SLAVE MONITOR ON *.* TO 'prometheus'@'localhost';
EOF
}

# write_my_cnf: the credentials, where the package documents them. (Not
# "install /dev/stdin": resolute's coreutils can't replace a file that way.)
write_my_cnf() {
    (umask 077 && cat > "$MY_CNF") <<EOF
[client]
user = prometheus
password = $DB_PASSWORD
socket = $DB_SOCKET
EOF
    chown prometheus:prometheus "$MY_CNF"
}

# start_exporter <port>: start the service, wait until it serves metrics.
start_exporter() {
    service prometheus-mysqld-exporter start
    wait_metrics "$1"
}

wait_metrics() {
    for _ in $(seq 1 100); do
        curl -fsS "http://127.0.0.1:$1/metrics" >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    echo "mysqld exporter does not answer on port $1" >&2
    show_log
    return 1
}

stop_exporter() {
    service prometheus-mysqld-exporter stop
    for _ in $(seq 1 100); do
        [[ -z "$(exporter_users)" ]] && return 0
        sleep 0.2
    done
    echo "mysqld exporter does not stop" >&2
    return 1
}

# expect_metrics <port> <line>...: every line (a metric with its labels
# and value, or a prefix ending in a space) must be in one scrape.
expect_metrics() {
    local port="$1" scrape line
    shift
    scrape="$(curl -fsS "http://127.0.0.1:$port/metrics")"
    for line in "$@"; do
        [[ $'\n'"$scrape" == *$'\n'"$line"* ]] || { echo "missing from the scrape: $line" >&2; show_log; return 1; }
    done
}
