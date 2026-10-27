#!/bin/bash
#
# Upgrade test for Prometheus: the distribution's Prometheus 2 scrapes
# itself ("seed") and gets a block backfilled with its own promtool, then
# ours ("check") has to start on the same configuration and data directory
# and still answer for both: the samples in the WAL and the persisted block.

set -euo pipefail

# shellcheck source=packages/prometheus/tests/lib.sh
source "$(dirname "$0")/lib.sh"
state=/var/tmp/freshdeb-prometheus

case "${1:?seed or check}" in
    seed)
        mkdir -p "$state"
        version="$(prometheus --version 2>&1)"
        echo "${version%%$'\n'*}"

        # A block an hour back, written by the distribution's promtool.
        backfilled=$(( $(date +%s) - 3600 ))
        echo "$backfilled" > "$state/backfilled"
        printf '# TYPE freshdeb_seed gauge\nfreshdeb_seed{written_by="distro"} 42 %s\n# EOF\n' \
            "$backfilled" > "$state/seed.om"
        chmod 0644 "$state/seed.om"
        runuser -u prometheus -- promtool tsdb create-blocks-from openmetrics \
            "$state/seed.om" /var/lib/prometheus/metrics2

        start_prometheus
        expect_value 'freshdeb_seed' 42 "$backfilled"
        expect_value 'up{job="prometheus"}' 1
        # A second later than now: date +%s rounds down, and the only sample
        # so far may be from within the current second.
        echo $(( $(date +%s) + 1 )) > "$state/scraped"
        sleep 1
        expect_value 'up{job="prometheus"}' 1 "$(cat "$state/scraped")"
        stop_prometheus
        ;;
    check)
        version="$(prometheus --version 2>&1)"
        [[ "$version" == "prometheus, version ${FRESHDEB_UPSTREAM_VERSION:?} "* ]] || { echo "$version" >&2; exit 1; }
        start_prometheus
        expect_value 'freshdeb_seed' 42 "$(cat "$state/backfilled")"
        expect_value 'up{job="prometheus"}' 1 "$(cat "$state/scraped")"
        expect_value 'up{job="prometheus"}' 1
        stop_prometheus
        ;;
esac
