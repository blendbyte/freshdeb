#!/bin/bash
#
# Smoke test for keepalived, run as root in a fresh container (with
# NET_ADMIN) right after our package was installed (and again after an
# upgrade from the distro's version): configuration checks, then one VRRP
# instance on a veth pair through the init script, which has to take its
# virtual IP, give it up while its tracking script fails, pick up a second
# one on reload and remove them on stop. Nothing leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 2.4.3

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/keepalived/tests/lib.sh
source "$(dirname "$0")/lib.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "binary reports the expected version"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
version="$(keepalived --version 2>&1)"
[[ "$version" == "Keepalived v${expected} "* ]] || { echo "$version" >&2; exit 1; }

echo "configuration test accepts a valid file, with auth_hmac (2.4), and refuses a broken one"
# The test also checks that the interfaces exist.
setup_link
install_scripts
write_conf "$work/good.conf" 192.0.2.10 -- "    auth_hmac {
        key 1 hex:000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
        active_key 1
    }"
keepalived -t -f "$work/good.conf" || { echo "valid configuration refused" >&2; exit 1; }
write_conf "$work/broken.conf" 192.0.2.10 -- "    virtual_router_id 999"
if keepalived -t -f "$work/broken.conf" >"$work/broken.out" 2>&1; then
    echo "broken configuration accepted" >&2; cat "$work/broken.out" >&2; exit 1
fi

echo "the service takes its virtual IP as MASTER, with HMAC-authenticated adverts"
log_to_file
cp "$work/good.conf" "$CONF"
start_keepalived
wait_for "MASTER" is_state MASTER
wait_for "192.0.2.10 on $IFACE" has_vip 192.0.2.10

echo "a failing tracking script moves it to FAULT and back"
touch "$STATE_DIR/fault"
wait_for "FAULT" is_state FAULT
wait_for "192.0.2.10 removed" no_vip 192.0.2.10
rm "$STATE_DIR/fault"
wait_for "MASTER again" is_state MASTER
wait_for "192.0.2.10 back" has_vip 192.0.2.10

echo "reload applies a changed configuration"
write_conf "$CONF" 192.0.2.10 192.0.2.11
service keepalived reload
wait_for "192.0.2.11 after reload" has_vip 192.0.2.11

echo "stop removes the virtual IPs"
stop_keepalived
wait_for "192.0.2.10 removed on stop" no_vip 192.0.2.10
wait_for "192.0.2.11 removed on stop" no_vip 192.0.2.11

echo "smoke test passed"
