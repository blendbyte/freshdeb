#!/bin/bash
#
# Upgrade test for blackbox_exporter: "seed" adds a module to
# /etc/prometheus/blackbox.yml and custom ARGS in
# /etc/default/prometheus-blackbox-exporter for the distribution's version,
# "check" makes sure ours keeps both: same port, same module, same result.

set -euo pipefail

# shellcheck source=packages/prometheus-blackbox-exporter/tests/lib.sh
source "$(dirname "$0")/lib.sh"
args="--config.file /etc/prometheus/blackbox.yml --web.listen-address=127.0.0.1:9116"

check_probe() {
    # The exporter probing its own port with the module from seed.
    expect_probe 9116 freshdeb_upgrade 127.0.0.1:9116 'probe_success 1'
}

case "${1:?seed or check}" in
    seed)
        version="$(prometheus-blackbox-exporter --version 2>&1)"
        echo "${version%%$'\n'*}"
        add_module freshdeb_upgrade
        sed -i "s|^ARGS=.*|ARGS=\"$args\"|" /etc/default/prometheus-blackbox-exporter
        # Not through the init script: the distributions' check the
        # configuration with promtool, so it can't start (see README.md).
        # shellcheck disable=SC2086  # ARGS is a list of flags
        runuser -u prometheus -- prometheus-blackbox-exporter $args \
            >/var/tmp/freshdeb-blackbox-seed.log 2>&1 &
        pid=$!
        wait_exporter 9116 || { cat /var/tmp/freshdeb-blackbox-seed.log >&2; exit 1; }
        check_probe
        kill "$pid"
        wait "$pid" || true
        ;;
    check)
        version="$(prometheus-blackbox-exporter --version 2>&1)"
        [[ "$version" == "blackbox_exporter, version ${FRESHDEB_UPSTREAM_VERSION:?} "* ]] || { echo "$version" >&2; exit 1; }
        grep -qxF "ARGS=\"$args\"" /etc/default/prometheus-blackbox-exporter
        start_exporter 9116
        check_probe
        stop_exporter
        # Back to the defaults for the smoke test that follows.
        sed -i 's|^ARGS=.*|ARGS="--config.file /etc/prometheus/blackbox.yml"|' /etc/default/prometheus-blackbox-exporter
        ;;
esac
