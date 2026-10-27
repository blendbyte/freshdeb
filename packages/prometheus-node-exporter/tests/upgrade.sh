#!/bin/bash
#
# Upgrade test for node_exporter: "seed" sets custom ARGS in
# /etc/default/prometheus-node-exporter and a textfile metric for the
# distribution's version, "check" makes sure ours keeps both: same port,
# same collectors, same textfile metric.

set -euo pipefail

# shellcheck source=packages/prometheus-node-exporter/tests/lib.sh
source "$(dirname "$0")/lib.sh"
textfile="$TEXTFILE_DIR/freshdeb-upgrade.prom"
# Flags every version since 1.1 knows: another port, and only the cpu and
# textfile collectors.
args='--web.listen-address=127.0.0.1:9101 --collector.disable-defaults --collector.cpu --collector.textfile'

check_scrape() {
    expect_metrics 9101 'freshdeb_upgrade{written_by="distro"} 42' 'node_cpu_seconds_total{'
    local scrape
    scrape="$(curl -fsS http://127.0.0.1:9101/metrics)"
    [[ "$scrape" != *node_memory_MemTotal_bytes* ]] || { echo "disabled collector memory is running" >&2; return 1; }
}

case "${1:?seed or check}" in
    seed)
        version="$(prometheus-node-exporter --version 2>&1)"
        echo "${version%%$'\n'*}"
        sed -i "s|^ARGS=.*|ARGS=\"$args\"|" /etc/default/prometheus-node-exporter
        printf '# TYPE freshdeb_upgrade gauge\nfreshdeb_upgrade{written_by="distro"} 42\n' > "$textfile"
        start_exporter 9101
        check_scrape
        stop_exporter
        ;;
    check)
        version="$(prometheus-node-exporter --version 2>&1)"
        [[ "$version" == "node_exporter, version ${FRESHDEB_UPSTREAM_VERSION:?} "* ]] || { echo "$version" >&2; exit 1; }
        grep -qxF "ARGS=\"$args\"" /etc/default/prometheus-node-exporter
        start_exporter 9101
        check_scrape
        stop_exporter
        # Back to the defaults for the smoke test that follows.
        sed -i 's|^ARGS=.*|ARGS=""|' /etc/default/prometheus-node-exporter
        rm -f "$textfile"
        ;;
esac
