#!/bin/bash
#
# Upgrade test for postgres_exporter: "seed" sets the connection
# (DATA_SOURCE_NAME) and custom ARGS in /etc/default/prometheus-postgres-exporter
# for the distribution's version, "check" makes sure ours keeps both: same
# port, still connected to PostgreSQL.

set -euo pipefail

# shellcheck source=packages/prometheus-postgres-exporter/tests/lib.sh
source "$(dirname "$0")/lib.sh"
# A flag every version since 0.10 knows: another port.
args='--web.listen-address=127.0.0.1:9188'

case "${1:?seed or check}" in
    seed)
        version="$(prometheus-postgres-exporter --version 2>&1)"
        echo "${version%%$'\n'*}"
        start_postgresql
        set_default DATA_SOURCE_NAME "$DSN"
        sed -i "s|^ARGS=.*|ARGS=\"$args\"|" "$DEFAULTS"
        # Started directly with the defaults file's settings: the
        # distributions' init script doesn't export DATA_SOURCE_NAME (ours
        # does, see overlays/init-export-environment.patch).
        (
            set -a
            # shellcheck source=/dev/null
            . "$DEFAULTS"
            set +a
            # shellcheck disable=SC2086,SC2153  # ARGS (from the defaults file) holds several flags
            exec runuser -u prometheus -- prometheus-postgres-exporter $ARGS \
                >/var/log/prometheus/prometheus-postgres-exporter.log 2>&1
        ) &
        seeded=$!
        wait_metrics 9188
        expect_metrics 9188 'pg_up 1' 'pg_stat_database_xact_commit{'
        kill "$seeded"
        wait "$seeded" || true
        [[ -z "$(exporter_users)" ]] || { echo "the distribution's exporter is still running" >&2; exit 1; }
        ;;
    check)
        version="$(prometheus-postgres-exporter --version 2>&1)"
        [[ "$version" == "postgres_exporter, version ${FRESHDEB_UPSTREAM_VERSION:?} "* ]] || { echo "$version" >&2; exit 1; }
        grep -qxF "DATA_SOURCE_NAME='$DSN'" "$DEFAULTS"
        grep -qxF "ARGS=\"$args\"" "$DEFAULTS"
        start_exporter 9188
        expect_metrics 9188 'pg_up 1' 'pg_stat_database_xact_commit{'
        stop_exporter
        # Back to the default port for the smoke test that follows.
        sed -i 's|^ARGS=.*|ARGS=""|' "$DEFAULTS"
        ;;
esac
