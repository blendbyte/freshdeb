#!/bin/bash
#
# Smoke test for nmap, run as root in a fresh container right after our
# packages were installed (and again after an upgrade from the distro's
# version). It scans a web server on localhost only.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 7.992

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/nmap/tests/lib.sh
source "$(dirname "$0")/lib.sh"
trap 'stop_web; rm -rf "$WEB"' EXIT

echo "nmap reports the version and the system libraries it uses"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
version="$(nmap --version)"
[[ "$version" == "Nmap version ${expected} "* ]] || { echo "$version" >&2; exit 1; }
compiled="$(grep '^Compiled with:' <<<"$version")"
# Upstream's bundled copies call themselves nmap-<lib>; libdnet is bundled in
# Debian too. libssh2 only where the system's is older than 1.11 (jammy,
# bookworm): nmap needs libssh2_userauth_banner and then uses its own copy.
allowed=nmap-libdnet
ssh2="$(apt-cache policy libssh2-1t64 libssh2-1 2>/dev/null | awk '/Candidate:/ && $2 != "(none)" { print $2; exit }')"
if [[ -n "$ssh2" ]] && dpkg --compare-versions "$ssh2" lt 1.11; then
    allowed="$allowed|nmap-libssh2"
fi
for lib in liblua-5.4 openssl- libz- libpcre2- libpcap- libssh2-; do
    [[ "$compiled" == *" $lib"* || ( "$lib" == libssh2- && "$allowed" == *nmap-libssh2* && "$compiled" == *" nmap-libssh2-"* ) ]] \
        || { echo "not built with the system's $lib: $compiled" >&2; exit 1; }
done
bundled="$(grep -o 'nmap-lib[a-z0-9]*' <<<"$compiled" | grep -vxE "$allowed" || true)"
[[ -z "$bundled" ]] || { echo "built with bundled libraries: $bundled" >&2; exit 1; }

start_web

echo "a connect scan finds the open port"
scan="$(nmap -sT -Pn -n -p "$PORT" 127.0.0.1)"
[[ "$scan" == *"$PORT/tcp open"* ]] || { echo "$scan" >&2; exit 1; }

echo "version detection identifies the HTTP server"
scan="$(nmap -sT -sV -Pn -n -p "$PORT" 127.0.0.1)"
[[ "$scan" == *"$PORT/tcp open  http"* ]] || { echo "$scan" >&2; exit 1; }

echo "an NSE script reads the page title"
# -sV: http-title runs on ports detected as HTTP.
scan="$(nmap -sT -sV -Pn -n -p "$PORT" --script http-title 127.0.0.1)"
[[ "$scan" == *"http-title: freshdeb nmap test"* ]] || { echo "$scan" >&2; exit 1; }

echo "ndiff compares two scans"
scan_xml "$WEB/before.xml"
stop_web
scan_xml "$WEB/after.xml"
rc=0
diff="$(ndiff "$WEB/before.xml" "$WEB/after.xml")" || rc=$?
# Exit status 1: the scans differ.
(( rc == 1 )) || { echo "ndiff exit status $rc: $diff" >&2; exit 1; }
[[ "$diff" == *"-$PORT/tcp open"* ]] || { echo "$diff" >&2; exit 1; }

echo "ncat carries data from client to listener"
ncat -l 127.0.0.1 18081 >"$WEB/received" &
listener=$!
for _ in $(seq 1 50); do
    printf 'freshdeb\n' | ncat --send-only 127.0.0.1 18081 2>/dev/null && break
    sleep 0.1
done
wait "$listener"
[[ "$(cat "$WEB/received")" == freshdeb ]] || { echo "received: $(cat "$WEB/received")" >&2; exit 1; }
ncat --version 2>&1 | grep -q "Ncat: Version ${expected}" || { ncat --version >&2; exit 1; }

echo "zenmap is installed and knows its version"
zenmap_version="$(python3 -c 'import zenmapCore.Version as v; print(v.VERSION)')"
[[ "$zenmap_version" == "$expected" ]] || { echo "zenmap: $zenmap_version" >&2; exit 1; }

echo "man page is packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L nmap)"
grep -qxF /usr/share/man/man1/nmap.1.gz <<<"$files" \
    || { echo "missing from the package: the man page" >&2; exit 1; }
echo "smoke test passed"
