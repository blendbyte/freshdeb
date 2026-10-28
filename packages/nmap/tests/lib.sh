#!/bin/bash
#
# Helpers for the nmap tests (sourced, not run): a small web server on
# localhost to scan. Nothing outside the container is scanned.

# shellcheck disable=SC2034  # used by the scripts that source this
PORT=18080
WEB="$(mktemp -d)"

# python3 serves the page; the test containers don't have it.
command -v python3 >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 >/dev/null

# start_web: a page titled "freshdeb nmap test" on 127.0.0.1:$PORT.
start_web() {
    printf '<html><head><title>freshdeb nmap test</title></head><body>ok</body></html>\n' >"$WEB/index.html"
    (cd "$WEB" && exec python3 -m http.server --bind 127.0.0.1 "$PORT") >"$WEB/server.log" 2>&1 &
    WEB_PID=$!
    for _ in $(seq 1 100); do
        python3 -c "import socket; socket.create_connection(('127.0.0.1', $PORT), 1).close()" 2>/dev/null && return 0
        sleep 0.1
    done
    echo "the web server does not start" >&2
    cat "$WEB/server.log" >&2
    return 1
}

stop_web() {
    [[ -n "${WEB_PID:-}" ]] || return 0
    kill "$WEB_PID" 2>/dev/null || true
    wait "$WEB_PID" 2>/dev/null || true
    WEB_PID=
}

# scan_xml FILE: a connect scan of the web server's port, as XML.
scan_xml() {
    nmap -sT -Pn -n -p "$PORT" -oX "$1" 127.0.0.1 >/dev/null
}
