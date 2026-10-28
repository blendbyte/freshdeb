#!/bin/bash
#
# Helpers for the headscale tests (sourced, not run). The containers have no
# systemd, so headscale runs directly as its user, with the directories the
# unit's RuntimeDirectory and StateDirectory would set up. Nothing leaves the
# container: the configuration uses a local DERP map instead of Tailscale's.
# They work with upstream's 0.28 package as well as with ours.

CONFIG=/etc/headscale/config.yaml
DERP_MAP=/etc/headscale/derp-test.yaml
LOG=/var/tmp/headscale-test.log

# curl, jq and sqlite3 come from the distribution; the images don't have them.
if ! command -v curl >/dev/null || ! command -v jq >/dev/null || ! command -v sqlite3 >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl jq sqlite3 >/dev/null
fi

# configure_offline: what an admin edits in /etc/headscale/config.yaml,
# keeping everything inside the container: a local DERP map (one region,
# from upstream's derp-example.yaml) instead of downloading Tailscale's
# (headscale doesn't start without one), and no update check.
configure_offline() {
    cat > "$DERP_MAP" <<'YAML'
regions:
  900:
    regionid: 900
    regioncode: test
    regionname: freshdeb test
    nodes:
      - name: 900a
        regionid: 900
        hostname: derp.invalid
        ipv4: 198.51.100.1
        stunport: 0
        stunonly: false
        derpport: 0
YAML
    # The line after "  urls:" is Tailscale's DERP map URL.
    sed -i '/^  urls:$/{n;d}' "$CONFIG"
    sed -i -e 's|^  urls:$|  urls: []|' -e "s|^  paths: \[\]$|  paths: [$DERP_MAP]|" "$CONFIG"
    sed -i 's|^disable_check_updates: false$|disable_check_updates: true|' "$CONFIG"
    grep -qx '  urls: \[\]' "$CONFIG"
    grep -qxF "  paths: [$DERP_MAP]" "$CONFIG"
    grep -qx 'disable_check_updates: true' "$CONFIG"
}

# What the unit's RuntimeDirectory= and StateDirectory= do.
prepare_dirs() {
    install -d -o headscale -g headscale -m 0750 /run/headscale
    chown -R headscale:headscale /var/lib/headscale
}

start_headscale() {
    prepare_dirs
    runuser -u headscale -- headscale serve >"$LOG" 2>&1 &
    HEADSCALE_PID=$!
    for _ in $(seq 1 100); do
        curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1 && return 0
        kill -0 "$HEADSCALE_PID" 2>/dev/null || break
        sleep 0.2
    done
    echo "headscale does not answer on 127.0.0.1:8080" >&2
    tail -n 30 "$LOG" >&2
    return 1
}

stop_headscale() {
    kill "$HEADSCALE_PID"
    wait "$HEADSCALE_PID" || true
}

# user_id <name>: the id of a headscale user.
user_id() {
    headscale users list -o json | jq -r --arg name "$1" '.[] | select(.name == $name) | .id'
}
