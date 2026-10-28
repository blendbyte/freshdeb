#!/bin/bash
#
# Helpers for the proftpd tests (sourced, not run): a local FTP user, the
# service through its init script, and transfers with curl. They work with
# the distributions' versions as well as with ours; everything stays on
# 127.0.0.1.

# proftpd warns about world-writable configuration files.
umask 022

# shellcheck disable=SC2034  # used by the scripts that source this
SNIPPET=/etc/proftpd/conf.d/freshdeb-test.conf
FTP_USER=freshdeb
FTP_PASS=freshdeb-test-pw
FTP_HOME=/home/$FTP_USER
SHARED=/srv/freshdeb-shared

# curl and openssl come from the distribution; the test containers have
# neither.
if ! command -v curl >/dev/null || ! command -v openssl >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl openssl >/dev/null
fi

# add_ftp_user: a local user with a password (PAM, as Debian configures it)
# and files for the vroot and case checks.
add_ftp_user() {
    if ! getent passwd "$FTP_USER" >/dev/null; then
        useradd --create-home --shell /bin/bash "$FTP_USER"
    fi
    echo "$FTP_USER:$FTP_PASS" | chpasswd
    echo "mixed case" > "$FTP_HOME/MixedCase.txt"
    mkdir -p "$SHARED"
    echo "shared file" > "$SHARED/shared.txt"
    chown -R "$FTP_USER": "$FTP_HOME"
}

# write_snippet: appends vroot (a shared directory aliased into the chroot)
# and case (case-insensitive names) settings, in directives every version
# since 1.3.7 knows; the modules are loaded before.
write_snippet() {
    cat >> "$SNIPPET" <<EOF
<IfModule mod_vroot.c>
  VRootEngine on
  DefaultRoot ~
  VRootAlias $SHARED ~/shared
</IfModule>
<IfModule mod_case.c>
  CaseEngine on
  CaseIgnore on
</IfModule>
EOF
}

# proftpd_pids: pids of proftpd processes, one per line (from /proc: the
# images have no procps).
proftpd_pids() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == proftpd ]] && basename "$proc"
    done
    return 0
}

# ftp URL ARGS...: curl as the test user.
ftp() {
    local url=$1
    shift
    curl -sS --fail --max-time 20 -u "$FTP_USER:$FTP_PASS" "$@" "$url"
}

start_proftpd() {
    service proftpd start
    for _ in $(seq 1 100); do
        ftp ftp://127.0.0.1/ --list-only >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    echo "proftpd does not answer on port 21" >&2
    tail -n 30 /var/log/proftpd/proftpd.log >&2 || true
    return 1
}

stop_proftpd() {
    service proftpd stop
    for _ in $(seq 1 100); do
        [[ -z "$(proftpd_pids)" ]] && return 0
        sleep 0.2
    done
    echo "proftpd does not stop" >&2
    return 1
}

# check_modules: vroot maps the shared directory into the chroot, case finds
# MixedCase.txt under another case.
check_modules() {
    local listing got
    listing="$(ftp ftp://127.0.0.1/ --list-only)"
    grep -qx shared <<<"${listing//$'\r'/}" || { echo "no vroot alias in: $listing" >&2; return 1; }
    got="$(ftp ftp://127.0.0.1/shared/shared.txt)"
    [[ "$got" == "shared file" ]] || { echo "vroot alias: $got" >&2; return 1; }
    got="$(ftp ftp://127.0.0.1/MIXEDCASE.TXT)"
    [[ "$got" == "mixed case" ]] || { echo "case: $got" >&2; return 1; }
}
