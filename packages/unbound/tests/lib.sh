#!/bin/bash
#
# Helpers for the unbound tests (sourced, not run): a configuration snippet
# with a local zone, starting and stopping the service through its init
# script, and queries with dig. They work with the distributions' versions as
# well as with ours. Nothing is resolved beyond the local zone.

# shellcheck disable=SC2034  # used by the scripts that source this
SNIPPET=/etc/unbound/unbound.conf.d/freshdeb-test.conf

# dig comes from the distribution, and so does the root trust anchor
# (dns-root-data, which unbound recommends); the test containers have neither.
if ! command -v dig >/dev/null || [[ ! -e /usr/share/dns/root.key ]]; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq bind9-dnsutils dns-root-data >/dev/null
fi

# write_snippet TXT: a local zone with an A and a TXT record. Only options
# every version since 1.13 knows. 127.0.0.1 only: containers may lack ::1.
write_snippet() {
    cat > "$SNIPPET" <<EOF
server:
    interface: 127.0.0.1
    local-zone: "freshdeb.test." static
    local-data: "a.freshdeb.test. 300 IN A 192.0.2.53"
    local-data: 'a.freshdeb.test. 300 IN TXT "$1"'
EOF
}

# unbound_users: users running an unbound process, one per line (from /proc:
# the images have no procps).
unbound_users() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == unbound ]] && stat -c %U "$proc"
    done
    return 0
}

start_unbound() {
    service unbound start
    for _ in $(seq 1 100); do
        unbound-control status >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    echo "unbound does not answer unbound-control" >&2
    unbound-control status >&2 || true
    return 1
}

stop_unbound() {
    service unbound stop
    for _ in $(seq 1 100); do
        [[ -z "$(unbound_users)" ]] && return 0
        sleep 0.2
    done
    echo "unbound does not stop" >&2
    return 1
}

# answer TYPE: the local record of that type, through the server.
answer() {
    dig +short +time=2 +tries=1 @127.0.0.1 a.freshdeb.test. "$1"
}

# expect_txt VALUE: the TXT record becomes VALUE (after a reload).
expect_txt() {
    for _ in $(seq 1 50); do
        [[ "$(answer TXT)" == "\"$1\"" ]] && return 0
        sleep 0.2
    done
    echo "TXT is $(answer TXT), expected \"$1\"" >&2
    return 1
}
