#!/bin/bash
#
# Upgrade test for VictoriaMetrics: "seed" runs the distribution's version
# with custom ARGS (another port, a longer retention) and writes samples, one
# of them a day old; "check" makes sure ours starts with the same settings on
# the same data directory, which it upgrades in place, and returns them.

set -euo pipefail

# shellcheck source=packages/victoriametrics/tests/lib.sh
source "$(dirname "$0")/lib.sh"
state=/var/tmp/freshdeb-victoriametrics
# Flags every version since 1.71 knows.
args='-storageDataPath=/var/lib/victoria-metrics -retentionPeriod=3 -httpListenAddr=127.0.0.1:8430'
VM=http://127.0.0.1:8430

case "${1:?seed or check}" in
    seed)
        mkdir -p "$state"
        version="$(victoria-metrics -version 2>/dev/null)"
        echo "${version##*$'\n'}"
        sed -i "s|^ARGS=.*|ARGS=\"$args\"|" "$DEFAULTS"
        # Ten minutes and a day back: older than the in-memory parts, so the
        # day-old sample sits in another daily partition of the index.
        recent=$(( $(date +%s) - 600 ))
        old=$(( $(date +%s) - 86400 ))
        echo "$recent $old" > "$state/times"
        start_vm
        write_samples "$VM" <<EOF
freshdeb_upgrade{written_by="distro"} 42 ${recent}000
freshdeb_upgrade_old{written_by="distro"} 7 ${old}000
EOF
        expect_value 'freshdeb_upgrade{written_by="distro"}' 42 "$recent"
        expect_value 'freshdeb_upgrade_old{written_by="distro"}' 7 "$old"
        stop_vm
        ;;
    check)
        version="$(victoria-metrics -version 2>/dev/null)"
        [[ "$version" == *"-tags-v${FRESHDEB_UPSTREAM_VERSION:?}-0-g"* ]] || { echo "$version" >&2; exit 1; }
        grep -qxF "ARGS=\"$args\"" "$DEFAULTS"
        read -r recent old < "$state/times"
        start_vm
        expect_value 'freshdeb_upgrade{written_by="distro"}' 42 "$recent"
        expect_value 'freshdeb_upgrade_old{written_by="distro"}' 7 "$old"
        # And it keeps taking new samples into the upgraded storage.
        new=$(( $(date +%s) - 60 ))
        write_samples "$VM" <<EOF
freshdeb_upgrade{written_by="freshdeb"} 43 ${new}000
EOF
        expect_value 'freshdeb_upgrade{written_by="freshdeb"}' 43 "$new"
        stop_vm
        # Back to Debian's defaults for the smoke test that follows.
        sed -i 's|^ARGS=.*|ARGS="-storageDataPath=/var/lib/victoria-metrics"|' "$DEFAULTS"
        ;;
esac
