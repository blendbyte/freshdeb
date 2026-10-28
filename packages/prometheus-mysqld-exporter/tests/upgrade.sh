#!/bin/bash
#
# Upgrade test for mysqld_exporter: "seed" sets up the distribution's
# version the way its users would, with the credentials in the prometheus
# user's ~/.my.cnf (before 0.15 the default file, later through
# --config.my-cnf) and another port in ARGS. "check" makes sure ours keeps
# monitoring the server with the configuration unchanged.

set -euo pipefail

# shellcheck source=packages/prometheus-mysqld-exporter/tests/lib.sh
source "$(dirname "$0")/lib.sh"
args='--web.listen-address=127.0.0.1:9105'

case "${1:?seed or check}" in
    seed)
        version="$(prometheus-mysqld-exporter --version 2>&1)"
        version="${version%%$'\n'*}"
        echo "$version"
        number="${version#mysqld_exporter, version }"
        if dpkg --compare-versions "${number%% *}" ge 0.15; then
            # Its default is .my.cnf in the working directory.
            args="$args --config.my-cnf=$MY_CNF"
        fi
        start_mariadb
        write_my_cnf
        sed -i "s|^ARGS=.*|ARGS=\"$args\"|" /etc/default/prometheus-mysqld-exporter
        start_exporter 9105
        expect_metrics 9105 'mysql_up 1'
        stop_exporter
        ;;
    check)
        version="$(prometheus-mysqld-exporter --version 2>&1)"
        [[ "$version" == "mysqld_exporter, version ${FRESHDEB_UPSTREAM_VERSION:?} "* ]] || { echo "$version" >&2; exit 1; }
        grep -q "^ARGS=\"$args" /etc/default/prometheus-mysqld-exporter
        start_mariadb
        start_exporter 9105
        expect_metrics 9105 'mysql_up 1' 'mysql_global_status_uptime '
        stop_exporter
        # Back to the defaults for the smoke test that follows.
        sed -i 's|^ARGS=.*|ARGS=""|' /etc/default/prometheus-mysqld-exporter
        ;;
esac
