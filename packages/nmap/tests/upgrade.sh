#!/bin/bash
#
# Upgrade test for nmap: "seed" scans a local web server with the
# distribution's nmap and keeps the XML, "check" makes sure ours reads it
# (ndiff against a new scan) and that its script database is in order.

set -euo pipefail

# shellcheck source=packages/nmap/tests/lib.sh
source "$(dirname "$0")/lib.sh"
trap 'stop_web; rm -rf "$WEB"' EXIT
state=/var/tmp/freshdeb-nmap

case "${1:?seed or check}" in
    seed)
        nmap --version | head -n 1
        mkdir -p "$state"
        start_web
        scan_xml "$state/distro.xml"
        grep -q "portid=\"$PORT\".*state=\"open\"" "$state/distro.xml" \
            || { cat "$state/distro.xml" >&2; exit 1; }
        ;;
    check)
        version="$(nmap --version)"
        [[ "$version" == "Nmap version ${FRESHDEB_UPSTREAM_VERSION:?} "* ]] || { echo "$version" >&2; exit 1; }
        nmap --script-updatedb >/dev/null
        start_web
        scan_xml "$WEB/ours.xml"
        # The old scan's XML reads fine and the port is open in both; ndiff
        # still differs (exit status 1) on the "Nmap <version> scan" lines
        # and on the service name 7.80 printed as "unknown".
        rc=0
        diff="$(ndiff "$state/distro.xml" "$WEB/ours.xml")" || rc=$?
        (( rc <= 1 )) || { echo "ndiff exit status $rc: $diff" >&2; exit 1; }
        if grep -E "^[-+]$PORT/tcp" <<<"$diff" | grep -vqE "^[-+]$PORT/tcp +open"; then
            echo "the port's state changed: $diff" >&2; exit 1
        fi
        grep -q "portid=\"$PORT\".*state=\"open\"" "$WEB/ours.xml" || { cat "$WEB/ours.xml" >&2; exit 1; }
        rm -rf "$state"
        ;;
esac
