#!/bin/bash
#
# Upgrade test for unbound: "seed" gives the distribution's version a local
# zone in /etc/unbound/unbound.conf.d/, "check" makes sure ours keeps that
# configuration and answers the same.

set -euo pipefail

# shellcheck source=packages/unbound/tests/lib.sh
source "$(dirname "$0")/lib.sh"
state=/var/tmp/freshdeb-unbound

case "${1:?seed or check}" in
    seed)
        mkdir -p "$state"
        version="$(unbound -V)"
        echo "${version%%$'\n'*}"
        write_snippet seeded
        sha256sum "$SNIPPET" > "$state/snippet.sha256"
        start_unbound
        [[ "$(answer A)" == 192.0.2.53 ]]
        expect_txt seeded
        stop_unbound
        ;;
    check)
        version="$(unbound -V)"
        [[ "$version" == "Version ${FRESHDEB_UPSTREAM_VERSION:?}"$'\n'* ]] || { echo "$version" >&2; exit 1; }
        sha256sum -c --quiet "$state/snippet.sha256"
        unbound-checkconf
        start_unbound
        [[ "$(answer A)" == 192.0.2.53 ]]
        expect_txt seeded
        stop_unbound
        rm -f "$SNIPPET"
        ;;
esac
