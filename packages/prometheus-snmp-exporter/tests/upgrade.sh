#!/bin/bash
#
# Upgrade test for snmp_exporter: "seed" writes /etc/prometheus/snmp.yml
# with the distribution's generator and scrapes snmpd with the
# distribution's exporter. "check" upgrades to ours: a configuration from
# 0.23 or newer (noble, trixie, resolute) has to keep working as it is. One
# in the format before 0.23 (jammy's 0.20, bookworm's 0.21) is refused, as
# upstream and our NEWS entry say; regenerated with our generator, the same
# metrics come back.

set -euo pipefail

# shellcheck source=packages/prometheus-snmp-exporter/tests/lib.sh
source "$(dirname "$0")/lib.sh"
config=/etc/prometheus/snmp.yml

case "${1:?seed or check}" in
    seed)
        # From dpkg: jammy's 0.20 has no --version.
        version="$(dpkg-query -W -f='${Version}' prometheus-snmp-exporter)"
        echo "distribution's prometheus-snmp-exporter $version"
        format=new
        dpkg --compare-versions "$version" lt 0.23~ && format=old
        mkdir -p "$SNMP_DIR"
        echo "$format" > "$SNMP_DIR/format"
        echo "configuration in the $format format"
        generate "$format" "$config"
        sha256sum "$config" > "$SNMP_DIR/snmp.yml.sha256"
        # The distributions' packages don't create the init script's log
        # directory (ours does); only other Prometheus packages would.
        mkdir -p /var/log/prometheus
        start_snmpd
        start_exporter
        expect_agent_metrics
        stop_exporter
        stop_snmpd
        ;;
    check)
        version="$(prometheus-snmp-exporter --version 2>&1)"
        [[ "$version" == "snmp_exporter, version ${FRESHDEB_UPSTREAM_VERSION:?} "* ]] || { echo "$version" >&2; exit 1; }
        sha256sum -c "$SNMP_DIR/snmp.yml.sha256"
        if [[ "$(cat "$SNMP_DIR/format")" == old ]]; then
            echo "the configuration from before 0.23 is refused, pointing to the migration guide"
            if output="$(prometheus-snmp-exporter --dry-run 2>&1)"; then
                echo "an old-format snmp.yml was accepted" >&2
                exit 1
            fi
            [[ "$output" == *auth-split-migration.md* ]] || { echo "$output" >&2; exit 1; }
            echo "regenerated in the new format, it works"
            generate new "$config"
        fi
        prometheus-snmp-exporter --dry-run
        start_snmpd
        start_exporter
        expect_agent_metrics
        stop_exporter
        stop_snmpd
        ;;
esac
