#!/bin/bash
#
# Smoke test for UnrealIRCd, run as root in a fresh container right after our
# package was installed (and again after an upgrade from our previous one).
# The containers have no systemd: the server runs as its user, in the
# directories the unit's RuntimeDirectory= etc. would set up, and the test
# logs in over IRC. Nothing leaves the container (no GeoIP downloads).
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 6.2.7

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
CONF=/etc/unrealircd/unrealircd.conf
OUT=/var/tmp/unrealircd-test.out
PID=""
cleanup() {
    [[ -z "$PID" ]] || kill "$PID" 2>/dev/null || true
    rm -f "$CONF"
}
trap cleanup EXIT

# irc <port> <line>...: register as "smoke" (answering the server's PING
# cookie), send the lines once welcomed, print every reply until the server
# closes the connection or has been quiet for 5 seconds.
irc() {
    local port="$1" out="" line sent=""
    shift
    exec 3<>"/dev/tcp/127.0.0.1/$port"
    printf 'NICK smoke\r\nUSER smoke 0 * :freshdeb smoke test\r\n' >&3
    while IFS= read -r -t 5 line <&3; do
        line="${line%$'\r'}"
        out+="$line"$'\n'
        if [[ "$line" == "PING "* ]]; then
            printf 'PONG %s\r\n' "${line#PING }" >&3
        elif [[ -z "$sent" && "$line" == *" 001 smoke "* ]]; then
            printf '%s\r\n' "$@" >&3
            sent=1
        fi
    done
    exec 3<&-
    printf '%s' "$out"
}

# expect <output> <text>...: each text appears in output.
expect() {
    local output="$1" text
    shift
    for text in "$@"; do
        [[ "$output" == *"$text"* ]] || { echo "expected: $text"$'\n'"got:"$'\n'"$output" >&2; exit 1; }
    done
}

echo "version"
version="$(runuser -u unrealircd -- unrealircd -v)"
[[ "$version" == "UnrealIRCd-${expected}" ]] || { echo "$version" >&2; exit 1; }

echo "system user, and a configuration only the server can read"
id unrealircd
[[ "$(stat -c '%U:%G %a' /etc/unrealircd)" == "root:unrealircd 750" ]]
[[ "$(stat -c '%U:%G %a' /etc/unrealircd/tls/server.key.pem)" == "root:unrealircd 640" ]]
openssl x509 -noout -in /etc/unrealircd/tls/server.cert.pem
for dir in /var/lib/unrealircd /var/lib/unrealircd/tmp /var/cache/unrealircd /var/log/unrealircd; do
    [[ "$(stat -c '%U:%G' "$dir")" == "unrealircd:unrealircd" ]] || { echo "$dir: $(stat -c '%U:%G' "$dir")" >&2; exit 1; }
done

echo "upstream's default configuration files, linked from /etc/unrealircd"
for file in modules.default.conf operclass.default.conf snomasks.default.conf help/help.conf \
        aliases/anope.conf examples/example.conf; do
    [[ -f "/etc/unrealircd/$file" ]] || { echo "missing: /etc/unrealircd/$file" >&2; exit 1; }
done
[[ "$(readlink /etc/unrealircd/modules.default.conf)" == /usr/share/unrealircd/conf/modules.default.conf ]]
# Outgoing TLS trusts the system's CAs.
[[ "$(readlink -f /etc/unrealircd/tls/curl-ca-bundle.crt)" == "$(readlink -f /etc/ssl/certs/ca-certificates.crt)" ]]
conffiles="$(dpkg-query -W -f='${Conffiles}' unrealircd)"
for file in badwords.conf dccallow.conf spamfilter.conf tls/tls.cnf; do
    [[ "$conffiles" == *" /etc/unrealircd/$file "* ]] || { echo "not a conffile: $file"$'\n'"$conffiles" >&2; exit 1; }
done

echo "the unit waits for a configuration"
unit="$(dpkg -L unrealircd | grep -E '^/(usr/)?lib/systemd/system/unrealircd\.service$')"
grep -qx 'ConditionPathExists=/etc/unrealircd/unrealircd.conf' "$unit"
grep -qx 'User=unrealircd' "$unit"

echo "the module manager says it isn't available"
if refused="$(runuser -u unrealircd -- unrealircd -m list 2>&1)"; then
    echo "the module manager ran: $refused" >&2; exit 1
fi
expect "$refused" "not available in the Debian package" "/usr/share/doc/unrealircd/README.Debian"

echo "the services MOTD is in the data directory (0001)"
grep -aqF /var/lib/unrealircd/ircd.svsmotd /usr/sbin/unrealircd

echo "configtest passes on a minimal configuration"
mapfile -t keys < <(unrealircdctl gencloak | grep -oE '"[A-Za-z0-9]{50,}"')
[[ "${#keys[@]}" == 3 ]] || { echo "gencloak gave ${#keys[@]} keys" >&2; exit 1; }
cat > "$CONF" <<EOF
@define \$NO_DEFAULT_GEOIP "yes"
include "modules.default.conf";
include "help/help.conf";
include "badwords.conf";
include "operclass.default.conf";
include "snomasks.default.conf";
loadmodule "cloak_sha256";
me { name "irc.freshdeb.test"; info "freshdeb smoke test"; sid "001"; }
admin { "freshdeb"; }
class clients { pingfreq 90; maxclients 100; sendq 200k; recvq 8000; }
allow { mask *; class clients; maxperip 10; }
listen { ip 127.0.0.1; port 6667; }
listen { ip 127.0.0.1; port 6697; options { tls; } }
log { source { all; !debug; } destination { file "ircd.log" { maxsize 10M; } } }
set {
	network-name "freshdeb";
	default-server "irc.freshdeb.test";
	services-server "services.freshdeb.test";
	help-channel "#help";
	cloak-keys { ${keys[0]}; ${keys[1]}; ${keys[2]}; }
	kline-address "admin@freshdeb.test";
}
EOF
chgrp unrealircd "$CONF"
chmod 0640 "$CONF"
runuser -u unrealircd -- unrealircd -c

echo "the server starts and accepts a client, with a cloaked host"
install -d -o unrealircd -g unrealircd -m 0755 /run/unrealircd
runuser -u unrealircd -- unrealircd -F >"$OUT" 2>&1 &
PID=$!
for _ in $(seq 1 50); do
    (exec 3<>/dev/tcp/127.0.0.1/6667) 2>/dev/null && break
    kill -0 "$PID" 2>/dev/null || { cat "$OUT" >&2; exit 1; }
    sleep 0.2
done
session="$(irc 6667 VERSION 'JOIN #smoke' 'QUIT :done')"
expect "$session" " 001 smoke :Welcome to the freshdeb IRC Network" " 351 smoke UnrealIRCd-${expected}." \
    ":smoke!smoke@Clk-" " JOIN :#smoke" "ERROR :Closing Link: smoke"

echo "TLS with the certificate made on install"
served="$(openssl s_client -connect 127.0.0.1:6697 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256)"
[[ "$served" == "$(openssl x509 -noout -fingerprint -sha256 -in /etc/unrealircd/tls/server.cert.pem)" ]]

echo "unrealircdctl over the control socket"
status="$(unrealircdctl status)"
expect "$status" "unrealircd_version UnrealIRCd-${expected}" "UnrealIRCd is up and running."
expect "$(unrealircdctl rehash)" "Rehashed successfully."

echo "it writes its data and logs where the package put them"
grep -q "UnrealIRCd started" /var/log/unrealircd/ircd.log
kill "$PID"
wait "$PID" || true
PID=""
[[ -f /var/lib/unrealircd/channel.db && -f /var/lib/unrealircd/tkl.db ]] \
    || { ls -la /var/lib/unrealircd >&2; exit 1; }

echo "smoke test passed"
