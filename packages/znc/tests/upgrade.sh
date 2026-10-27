#!/bin/bash
#
# Upgrade test for ZNC: "seed" writes a configuration for the distribution's
# (older) ZNC and runs it, "check" makes sure our build starts with that same
# configuration and data directory and accepts the login.

set -euo pipefail

# shellcheck source=packages/znc/tests/lib.sh
source "$(dirname "$0")/lib.sh"

case "${1:?seed or check}" in
    seed)
        version="$(znc --version)"
        echo "${version%%$'\n'*}"
        old="${version#ZNC }"
        old="${old%%[!0-9.]*}"
        rm -rf "$ZNC_DIR"
        write_config "${old%.*}"
        start_znc
        expect "$(irc 'PRIVMSG *status :Version')" " 001 freshdeb " "ZNC ${old}"
        stop_znc
        ;;
    check)
        start_znc
        expect "$(irc 'PRIVMSG *status :Version')" " 001 freshdeb " "ZNC ${FRESHDEB_UPSTREAM_VERSION:?}"
        stop_znc
        ;;
esac
