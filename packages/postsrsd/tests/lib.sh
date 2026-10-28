#!/bin/bash
#
# Helpers for the postsrsd tests (sourced, not run): starting and stopping
# the service through its init script, and clients for the two lookup
# protocols Postfix uses: socketmap (2.x, a netstring on a unix socket) and
# tcp_table (1.x, ports 10001/10002). They work with the distributions'
# versions as well as with ours. Nothing leaves the container.

# shellcheck disable=SC2034  # used by the scripts that source this
CONF=/etc/postsrsd.conf
DEFAULTS=/etc/default/postsrsd
SECRET=/etc/postsrsd.secret
# socketmap's default: unix:/var/spool/postfix/srs, Postfix's queue directory.
SOCKET=/var/spool/postfix/srs

# python3 (for the protocol clients) comes from the distribution; the test
# containers don't have it.
command -v python3 >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 >/dev/null
# Without Postfix there is no queue directory for the socket.
mkdir -p /var/spool/postfix

# The users running a postsrsd process, one per line. From /proc: the images
# have no procps.
postsrsd_users() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == postsrsd ]] && stat -c %U "$proc"
    done
    return 0
}

# wait_for <what> <command...>: retry the command for up to 20 seconds.
wait_for() {
    local what="$1"
    shift
    for _ in $(seq 1 100); do
        "$@" >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    echo "timed out waiting for $what" >&2
    return 1
}

start_postsrsd() {
    service postsrsd start
}

stop_postsrsd() {
    service postsrsd stop || true
    wait_for "postsrsd to stop" test -z "$(postsrsd_users)"
}

# socketmap <map> <key>: postsrsd 2.x's reply to a Postfix socketmap lookup
# (forward or reverse), e.g. "OK SRS0=…" or "NOTFOUND ".
socketmap() {
    python3 - "$SOCKET" "$1 $2" <<'EOF'
import socket, sys
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(10)
sock.connect(sys.argv[1])
query = sys.argv[2].encode()
sock.sendall(str(len(query)).encode() + b":" + query + b",")
reply = b""
while True:
    head, colon, rest = reply.partition(b":")
    if colon and len(rest) > int(head):
        break
    chunk = sock.recv(4096)
    if not chunk:
        sys.exit("connection closed: %r" % reply)
    reply += chunk
print(rest[:int(head)].decode())
EOF
}

# worker_users: with a socketmap connection open (the worker that serves it
# stays for the keep-alive time), the user and root directory of every
# postsrsd worker (a postsrsd process whose parent is one), one per line,
# e.g. "postsrsd /var/lib/postsrsd".
worker_users() {
    python3 - "$SOCKET" <<'EOF'
import os, pwd, socket, sys
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(10)
sock.connect(sys.argv[1])
query = b"forward user@remote.test"
sock.sendall(str(len(query)).encode() + b":" + query + b",")
sock.recv(4096)
def postsrsd():
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            if open(f"/proc/{pid}/comm").read().strip() == "postsrsd":
                yield pid
        except OSError:
            pass
pids = set(postsrsd())
for pid in pids:
    if open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[1] in pids:
        print(pwd.getpwuid(os.stat(f"/proc/{pid}").st_uid).pw_name, os.readlink(f"/proc/{pid}/root"))
EOF
}

# tcp_table <port> <key>: postsrsd 1.x's reply to a Postfix tcp_table lookup,
# e.g. "200 SRS0=…" or "500 …", URL-decoded.
tcp_table() {
    python3 - "$1" "$2" <<'EOF'
import socket, sys, urllib.parse
sock = socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=10)
sock.sendall(b"get " + sys.argv[2].encode() + b"\n")
reply = b""
while not reply.endswith(b"\n"):
    chunk = sock.recv(4096)
    if not chunk:
        break
    reply += chunk
print(urllib.parse.unquote(reply.decode().strip()))
EOF
}

# expect <description> <pattern> <actual>: the actual reply must match the
# (bash) pattern.
expect() {
    # shellcheck disable=SC2053  # $2 is a pattern
    [[ "$3" == $2 ]] || { echo "$1: got '$3'" >&2; return 1; }
}
