#!/bin/bash
#
# Helpers for the ZNC tests (sourced, not run): a data directory with a
# minimal configuration, starting and stopping ZNC as the _znc user, and an
# IRC client session over /dev/tcp. ZNC answers on its own (welcome, *status)
# without any IRC network.

ZNC_DIR="${ZNC_DIR:-/var/lib/freshdeb-znc}"
ZNC_PORT=16667
ZNC_PASS=freshdeb-secret

# write_config <Version line>: a listener, one admin user with a network.
write_config() {
    local salt hash
    salt="freshdebsalt0000"
    hash="$(printf '%s%s' "$ZNC_PASS" "$salt" | sha256sum | cut -d' ' -f1)"
    mkdir -p "$ZNC_DIR/configs"
    cat > "$ZNC_DIR/configs/znc.conf" <<EOF
Version = $1
PidFile = $ZNC_DIR/znc.pid
<Listener l>
	Port = $ZNC_PORT
	IPv4 = true
	IPv6 = false
	SSL = false
	AllowIRC = true
	AllowWeb = true
</Listener>
LoadModule = webadmin

<User freshdeb>
	Pass = sha256#$hash#$salt#
	Admin = true
	Nick = freshdeb
	AltNick = freshdeb_
	Ident = freshdeb
	RealName = freshdeb test
	LoadModule = controlpanel
	<Network test>
		LoadModule = simple_away
	</Network>
</User>
EOF
    chown -R _znc:_znc "$ZNC_DIR"
}

start_znc() {
    runuser -u _znc -- znc --datadir="$ZNC_DIR"
    for _ in $(seq 1 50); do
        (exec 3<>"/dev/tcp/127.0.0.1/$ZNC_PORT") 2>/dev/null && return 0
        sleep 0.2
    done
    echo "ZNC does not listen on $ZNC_PORT" >&2
    return 1
}

stop_znc() {
    local pid
    pid="$(cat "$ZNC_DIR/znc.pid" 2>/dev/null || true)"
    [[ -n "$pid" ]] || return 0
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 50); do
        [[ -d "/proc/$pid" ]] || return 0
        sleep 0.2
    done
    kill -9 "$pid" 2>/dev/null || true
}

# irc <line>...: log in to network "test", send the lines, print every reply
# that arrives until ZNC has been quiet for 3 seconds.
irc() {
    local out="" line
    exec 3<>"/dev/tcp/127.0.0.1/$ZNC_PORT"
    printf 'PASS freshdeb/test:%s\r\nNICK freshdeb\r\nUSER freshdeb 0 * :freshdeb\r\n' "$ZNC_PASS" >&3
    for line in "$@"; do
        printf '%s\r\n' "$line" >&3
    done
    while IFS= read -r -t 3 line <&3; do
        out+="${line%$'\r'}"$'\n'
    done
    exec 3<&-
    printf '%s' "$out"
}

# expect <haystack> <needle>...: fail with the whole session if one is missing.
expect() {
    local session="$1" needle
    shift
    for needle in "$@"; do
        [[ "$session" == *"$needle"* ]] || { printf 'missing: %s\nsession:\n%s\n' "$needle" "$session" >&2; return 1; }
    done
}
