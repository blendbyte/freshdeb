#!/bin/bash
#
# Helpers for the Forgejo tests (sourced, not run). The containers have no
# systemd, so Forgejo runs directly as git, with the environment the unit
# sets. Nothing leaves the container: OFFLINE_MODE, no update checker.

CONFIG=/etc/forgejo/app.ini
WORK=/var/lib/forgejo
URL=http://127.0.0.1:3000
LOG=/var/tmp/forgejo-test.log

# curl comes from the distribution; the images don't have it.
command -v curl >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl >/dev/null

# as_git <command...>: run as git with the unit's environment.
as_git() {
    runuser -u git -- env HOME=/home/git USER=git FORGEJO_WORK_DIR="$WORK" "$@"
}

# write_config: what the install wizard would write, for SQLite on
# localhost, with the installation locked.
write_config() {
    local secret token jwt
    secret="$(forgejo generate secret SECRET_KEY)"
    token="$(forgejo generate secret INTERNAL_TOKEN)"
    jwt="$(forgejo generate secret JWT_SECRET)"
    cat > "$CONFIG" <<INI
APP_NAME = freshdeb test
RUN_USER = git
WORK_PATH = $WORK
RUN_MODE = prod

[database]
DB_TYPE = sqlite3
PATH = $WORK/data/forgejo.db

[repository]
ROOT = $WORK/data/forgejo-repositories

[server]
HTTP_ADDR = 127.0.0.1
HTTP_PORT = 3000
ROOT_URL = $URL/
DISABLE_SSH = true
OFFLINE_MODE = true

[security]
INSTALL_LOCK = true
SECRET_KEY = $secret
INTERNAL_TOKEN = $token

[oauth2]
JWT_SECRET = $jwt

[service]
DISABLE_REGISTRATION = true

[cron.update_checker]
ENABLED = false

[log]
MODE = console
LEVEL = info
INI
    chown root:git "$CONFIG"
    chmod 0640 "$CONFIG"
}

# start_forgejo: "forgejo web" in the background, until /api/healthz is ok.
start_forgejo() {
    as_git forgejo web >"$LOG" 2>&1 &
    FORGEJO_PID=$!
    for _ in $(seq 1 150); do
        curl -fsS "$URL/api/healthz" >/dev/null 2>&1 && return 0
        kill -0 "$FORGEJO_PID" 2>/dev/null || break
        sleep 0.2
    done
    echo "Forgejo does not answer" >&2
    tail -n 60 "$LOG" >&2
    return 1
}

stop_forgejo() {
    kill "$FORGEJO_PID"
    wait "$FORGEJO_PID" || true
}
