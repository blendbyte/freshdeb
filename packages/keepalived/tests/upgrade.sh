#!/bin/bash
#
# Upgrade test for keepalived: "seed" configures one VRRP instance for the
# distribution's version (only keywords every version knows) and checks it
# takes its virtual IP; "check" makes sure ours keeps the configuration and
# the settings in /etc/default (DAEMON_ARGS) and does the same.

set -euo pipefail

# shellcheck source=packages/keepalived/tests/lib.sh
source "$(dirname "$0")/lib.sh"
saved=/var/tmp/freshdeb-keepalived.sha256
# An option every version knows (only the VRRP subsystem); jammy's 2.2.4 has
# no --log-file.
args='--vrrp'

master_with_vip() {
    start_keepalived
    wait_for "MASTER" is_state MASTER
    wait_for "192.0.2.20 on $IFACE" has_vip 192.0.2.20
    stop_keepalived
    wait_for "192.0.2.20 removed on stop" no_vip 192.0.2.20
}

case "${1:?seed or check}" in
    seed)
        version="$(keepalived --version 2>&1)"
        echo "${version%%$'\n'*}"
        setup_link
        install_scripts
        sed -i "s|^DAEMON_ARGS=.*|DAEMON_ARGS=\"$args\"|" /etc/default/keepalived
        write_conf "$CONF" 192.0.2.20
        sha256sum "$CONF" > "$saved"
        master_with_vip
        ;;
    check)
        version="$(keepalived --version 2>&1)"
        [[ "$version" == "Keepalived v${FRESHDEB_UPSTREAM_VERSION:?} "* ]] || { echo "$version" >&2; exit 1; }
        sha256sum -c --quiet "$saved"
        grep -qxF "DAEMON_ARGS=\"$args\"" /etc/default/keepalived
        setup_link
        master_with_vip
        ;;
esac
