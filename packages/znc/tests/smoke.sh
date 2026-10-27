#!/bin/bash
#
# Smoke test for ZNC, run as root in a fresh container right after our
# packages were installed (and again after an upgrade from the distro's
# version). ZNC runs as the _znc user; the test logs in over IRC.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 1.10.3

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/znc/tests/lib.sh
source "$(dirname "$0")/lib.sh"
ZNC_DIR="$(mktemp -d)"
trap 'stop_znc; rm -rf "$ZNC_DIR"' EXIT

echo "binary reports the expected version and features"
version="$(znc --version)"
[[ "$version" == "ZNC ${expected}"* ]] || { echo "$version" >&2; exit 1; }
for feature in "IPv6: yes" "SSL: yes" "i18n: yes"; do
    [[ "$version" == *"$feature"* ]] || { echo "missing feature: $feature"$'\n'"$version" >&2; exit 1; }
done

echo "system user exists"
id _znc

echo "ZNC starts and accepts an IRC login"
write_config "${expected%.*}"
start_znc
session="$(irc 'PRIVMSG *status :Version')"
expect "$session" " 001 freshdeb " "ZNC ${expected}"

echo "core, scripting and bundled third-party modules load"
session="$(irc 'PRIVMSG *status :LoadMod --type=global modpython' \
               'PRIVMSG *status :LoadMod --type=global modperl' \
               'PRIVMSG *status :LoadMod --type=user push' \
               'PRIVMSG *status :LoadMod --type=network backlog' \
               'PRIVMSG *status :ListMods')"
expect "$session" "Loaded module modpython:" "Loaded module modperl:" "Loaded module push:" "Loaded module backlog:"
for module in modtcl.so modpython.so modperl.so push.so backlog.so webadmin.so; do
    test -f "/usr/lib/znc/$module" || { echo "missing module: $module" >&2; exit 1; }
done

stop_znc
echo "smoke test passed"
