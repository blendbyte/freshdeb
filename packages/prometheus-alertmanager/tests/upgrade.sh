#!/bin/bash
#
# Upgrade test for Alertmanager: "seed" runs the distribution's version with
# custom ARGS (including Debian's --web.ui-path) and a changed configuration
# and adds a silence; "check" makes sure ours starts with the same settings
# and still has the silence, which lives in /var/lib/prometheus/alertmanager.

set -euo pipefail

# shellcheck source=packages/prometheus-alertmanager/tests/lib.sh
source "$(dirname "$0")/lib.sh"
state=/var/tmp/freshdeb-alertmanager
config=/etc/prometheus/alertmanager.yml
# No cluster (one instance), and the flag of Debian's packages that ours only
# accepts.
args='--cluster.listen-address= --web.ui-path=/usr/share/prometheus/alertmanager/ui/'

case "${1:?seed or check}" in
    seed)
        mkdir -p "$state"
        version="$(prometheus-alertmanager --version 2>&1)"
        echo "${version%%$'\n'*}"
        sed -i "s|^ARGS=.*|ARGS=\"$args\"|" /etc/default/prometheus-alertmanager
        sed -i "s|^  group_wait: .*|  group_wait: 7s|" "$config"
        grep -qx '  group_wait: 7s' "$config"
        start_alertmanager
        post_alert FreshdebUpgrade
        wait_alert FreshdebUpgrade
        amtool silence add --comment='freshdeb upgrade test' --duration=24h alertname=FreshdebUpgrade \
            > "$state/silence"
        stop_alertmanager
        ;;
    check)
        version="$(prometheus-alertmanager --version 2>&1)"
        [[ "$version" == "alertmanager, version ${FRESHDEB_UPSTREAM_VERSION:?} "* ]] || { echo "$version" >&2; exit 1; }
        grep -qxF "ARGS=\"$args\"" /etc/default/prometheus-alertmanager
        start_alertmanager
        grep -q 'web.ui-path is ignored' /var/log/prometheus/prometheus-alertmanager.log \
            || { echo "no warning about --web.ui-path" >&2; show_log; exit 1; }
        shown="$(amtool config show)"
        [[ "$shown" == *"group_wait: 7s"* ]] || { echo "$shown" >&2; exit 1; }
        silence="$(cat "$state/silence")"
        silences="$(amtool silence query -q)"
        grep -qxF "$silence" <<<"$silences" || { echo "silence $silence is gone: $silences" >&2; exit 1; }
        amtool silence expire "$silence"
        stop_alertmanager
        # Back to the defaults for the smoke test that follows.
        sed -i 's|^ARGS=.*|ARGS=""|' /etc/default/prometheus-alertmanager
        ;;
esac
