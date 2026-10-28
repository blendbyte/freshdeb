#!/bin/bash
#
# Helpers for the keepalived tests (sourced, not run): a veth pair for VRRP
# (the veth driver is always there where Docker runs; lo has no multicast),
# a configuration with one VRRP instance, its tracking and notify scripts,
# and starting and stopping the service through its init script. They work
# with the distributions' versions as well as with ours. The container needs
# NET_ADMIN (package.yaml tests.capabilities).

# shellcheck disable=SC2034  # used by the scripts that source this
CONF=/etc/keepalived/keepalived.conf
STATE_DIR=/run/freshdeb-keepalived
LOG=/var/log/keepalived-test.log
IFACE=ka0

# ip comes from the distribution; the test containers don't have it.
command -v ip >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iproute2 >/dev/null

# setup_link: ka0 (with 198.51.100.1/24) and its peer ka1, both up.
setup_link() {
    ip link show "$IFACE" >/dev/null 2>&1 || ip link add "$IFACE" type veth peer name ka1
    ip link set "$IFACE" up
    ip link set ka1 up
    ip -4 addr show dev "$IFACE" | grep -q '198.51.100.1/' || ip addr add 198.51.100.1/24 dev "$IFACE"
}

# install_scripts: the tracking script (fails while $STATE_DIR/fault exists)
# and the notify script (records the instance's state in $STATE_DIR/state).
# Root-owned and not writable by others, as enable_script_security wants.
install_scripts() {
    mkdir -p "$STATE_DIR"
    cat > /usr/local/sbin/freshdeb-ka-check <<EOF
#!/bin/sh
test ! -e $STATE_DIR/fault
EOF
    cat > /usr/local/sbin/freshdeb-ka-notify <<EOF
#!/bin/sh
# Arguments: GROUP|INSTANCE, name, state, priority
echo "\$3" > $STATE_DIR/state
EOF
    chmod 0755 /usr/local/sbin/freshdeb-ka-check /usr/local/sbin/freshdeb-ka-notify
}

# write_conf <file> <vip>... [-- <extra instance lines>]: one VRRP instance on
# ka0 holding the given virtual IPs (192.0.2.0/24), tracked by the check
# script and reporting through the notify script.
write_conf() {
    local file="$1" vips="" extra=""
    shift
    while (( $# )); do
        if [[ "$1" == -- ]]; then
            shift
            extra="$*"
            break
        fi
        vips+="        $1/32 dev $IFACE"$'\n'
        shift
    done
    cat > "$file" <<EOF
global_defs {
    router_id freshdeb_test
    script_user root
    enable_script_security
}

vrrp_script chk_flag {
    script "/usr/local/sbin/freshdeb-ka-check"
    interval 1
    fall 1
    rise 1
}

vrrp_instance VI_1 {
    state BACKUP
    interface $IFACE
    virtual_router_id 51
    priority 150
    advert_int 1
    virtual_ipaddress {
$vips    }
    track_script {
        chk_flag
    }
    notify "/usr/local/sbin/freshdeb-ka-notify"
$extra
}
EOF
}

show_log() {
    tail -n 40 "$LOG" >&2 || true
}

has_vip() {
    ip -4 -o addr show dev "$IFACE" | grep -qF " $1/32 "
}

state() {
    cat "$STATE_DIR/state" 2>/dev/null || true
}

# wait_for <description> <command...>: up to 20 seconds.
wait_for() {
    local what="$1"
    shift
    for _ in $(seq 1 100); do
        "$@" && return 0
        sleep 0.2
    done
    echo "timed out waiting for: $what (state: $(state))" >&2
    ip -4 addr show dev "$IFACE" >&2
    show_log
    return 1
}

is_state() {
    [[ "$(state)" == "$1" ]]
}

no_vip() {
    ! has_vip "$1"
}

# keepalived processes, from /proc: the images have no procps.
keepalived_running() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == keepalived ]] && return 0
    done
    return 1
}

not_running() {
    ! keepalived_running
}

# Logs to a file: the containers have no syslog.
log_to_file() {
    sed -i "s|^DAEMON_ARGS=.*|DAEMON_ARGS=\"--log-file=$LOG\"|" /etc/default/keepalived
    grep -qxF "DAEMON_ARGS=\"--log-file=$LOG\"" /etc/default/keepalived
}

start_keepalived() {
    rm -f "$STATE_DIR/state"
    service keepalived start
    wait_for "keepalived running" keepalived_running
}

stop_keepalived() {
    service keepalived stop
    wait_for "keepalived stopped" not_running
}
