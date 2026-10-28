#!/bin/bash
#
# Smoke test for bgpq4, run as root in a fresh container right after our
# package was installed (and again after an upgrade from the distro's
# version). bgpq4 queries a small IRR server on localhost that speaks the
# IRRd protocol with fixed data; nothing leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 1.16

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
work="$(mktemp -d)"
trap 'kill "${irrd_pid:-}" 2>/dev/null || true; rm -rf "$work"' EXIT

# python3 comes from the distribution; the test containers don't have it.
command -v python3 >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 >/dev/null

echo "binary reports the expected version"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
version="$(bgpq4 -v 2>&1)"
[[ "$version" == "bgpq4 - a versatile utility to generate BGP filters"*"version: ${expected}"* ]] \
    || { echo "$version" >&2; exit 1; }

echo "man page is packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L bgpq4)"
grep -qxF /usr/share/man/man8/bgpq4.8.gz <<<"$files" \
    || { echo "missing from the package: the man page" >&2; exit 1; }

# The IRR data: AS112 and AS3333 in the as-set AS-FRESHDEB, with route
# and route6 objects in source FRESHDEB (documentation prefixes).
cat > "$work/irrd.py" <<'EOF'
import socketserver
import sys

SETS = {"AS-FRESHDEB": "AS112 AS3333"}
ROUTES = {
    ("g", "AS112"): "192.0.2.0/25 192.0.2.128/25",
    ("g", "AS3333"): "198.51.100.0/24",
    ("6", "AS112"): "2001:db8::/48",
    ("6", "AS3333"): "2001:db8:1::/48",
}


def answer(data):
    return f"A{len(data) + 1}\n{data}\nC\n"


class IRRd(socketserver.StreamRequestHandler):
    def handle(self):
        for raw in self.rfile:
            line = raw.decode().strip()
            if line == "!!":
                continue  # multiple-command mode: no answer
            if line.startswith("!n") or line.startswith("!s") and line != "!s-lc":
                reply = "C\n"
            elif line == "!s-lc":
                reply = answer("FRESHDEB")
            elif line == "!a":
                reply = "D\n"  # no A queries: bgpq4 expands sets itself
            elif line.startswith("!i"):
                name = line[2:].split(",")[0].upper()
                reply = answer(SETS[name]) if name in SETS else "D\n"
            elif line[:2] in ("!g", "!6"):
                key = (line[1], line[2:].upper())
                reply = answer(ROUTES[key]) if key in ROUTES else "D\n"
            elif line == "!q":
                return
            else:
                reply = "D\n"
            self.wfile.write(reply.encode())
            self.wfile.flush()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


with Server(("127.0.0.1", int(sys.argv[1])), IRRd) as server:
    server.serve_forever()
EOF
python3 "$work/irrd.py" 4343 &
irrd_pid=$!
for _ in $(seq 1 50); do
    (exec 3<>/dev/tcp/127.0.0.1/4343) 2>/dev/null && break
    sleep 0.1
done

# expect <description> <expected output> <bgpq4 arguments...>
expect() {
    local what="$1" want="$2" got
    shift 2
    echo "$what"
    got="$(bgpq4 -h 127.0.0.1:4343 "$@" 2>&1)" || { echo "$got" >&2; return 1; }
    [[ "$got" == "$want" ]] || { printf 'expected:\n%s\ngot:\n%s\n' "$want" "$got" >&2; return 1; }
}

expect "Cisco prefix list for an AS" \
"no ip prefix-list freshdeb
ip prefix-list freshdeb permit 192.0.2.0/25
ip prefix-list freshdeb permit 192.0.2.128/25" \
    -S FRESHDEB -l freshdeb AS112

expect "aggregated prefix list for an as-set, with the server's sources" \
"no ip prefix-list freshdeb
ip prefix-list freshdeb permit 192.0.2.0/24 ge 25 le 25
ip prefix-list freshdeb permit 198.51.100.0/24" \
    -A -l freshdeb AS-FRESHDEB

expect "Juniper IPv6 prefix list" \
"policy-options {
replace:
 prefix-list freshdeb {
    2001:db8::/48;
    2001:db8:1::/48;
 }
}" \
    -S FRESHDEB -6 -J -l freshdeb AS-FRESHDEB

expect "BIRD prefix set" \
"freshdeb = [
    192.0.2.0/24{25,25},
    198.51.100.0/24
];" \
    -S FRESHDEB -A -b -l freshdeb AS-FRESHDEB

expect "JSON" \
'{ "freshdeb": [
    { "prefix": "192.0.2.0\/24", "exact": false,
      "greater-equal": 25, "less-equal": 25 },
    { "prefix": "198.51.100.0\/24", "exact": true }
] }' \
    -S FRESHDEB -A -j -l freshdeb AS-FRESHDEB

expect "Nokia SR Linux prefix set (since 1.10)" \
'/routing-policy
delete prefix-set "freshdeb"
prefix-set "freshdeb" {
    prefix 192.0.2.0/24 mask-length-range 25..25 { }
    prefix 198.51.100.0/24 mask-length-range exact { }
}' \
    -S FRESHDEB -A -n2 -l freshdeb AS-FRESHDEB

expect "Cisco AS-path access list" \
'no ip as-path access-list freshdeb
ip as-path access-list freshdeb permit ^112(_112)*$
ip as-path access-list freshdeb permit ^112(_[0-9]+)*_(3333)$' \
    -S FRESHDEB -f 112 -l freshdeb AS-FRESHDEB

echo "smoke test passed"
