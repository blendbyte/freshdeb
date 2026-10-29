#!/bin/bash
#
# Upgrade test for postsrsd. "seed" configures the distribution's version and
# has it rewrite an address: 1.x (bookworm, noble, trixie) through
# /etc/default/postsrsd and Postfix's tcp_table protocol, 2.x (resolute)
# through /etc/postsrsd.conf and socketmap. "check" makes sure ours took the
# settings over (from /etc/default/postsrsd into /etc/postsrsd.conf for 1.x,
# unchanged for 2.x), kept the secret, reverses the address the old version
# made and rewrites new ones the same way.

set -euo pipefail

# shellcheck source=packages/postsrsd/tests/lib.sh
source "$(dirname "$0")/lib.sh"
state=/var/tmp/freshdeb-postsrsd
# 1.x's AppArmor profile, a conffile 2.x no longer has.
PROFILE=/etc/apparmor.d/usr.sbin.postsrsd

case "${1:?seed or check}" in
    seed)
        mkdir -p "$state"
        version="$(dpkg-query -W -f '${Version}' postsrsd)"
        echo "postsrsd $version"
        stop_postsrsd
        sha256sum "$SECRET" > "$state/secret.sha256"
        [[ ! -e "$PROFILE" ]] || touch "$state/apparmor"
        if dpkg --compare-versions "$version" lt 2~; then
            # 1.x: settings as shell variables. The SRS domain, two excluded
            # domains (with a leading dot: subdomains only) and a different
            # separator, which ours has to carry over.
            cat >> "$DEFAULTS" <<'EOF'
SRS_DOMAIN=example.org
SRS_EXCLUDE_DOMAINS=.example.net,example.com
SRS_SEPARATOR=+
EOF
            start_postsrsd
            wait_for "the forward port" tcp_table 10001 probe@remote.test
            forward="$(tcp_table 10001 user@remote.test)"
            expect "1.x forward" '200 SRS0+????=??=remote.test=user@example.org' "$forward"
            expect "1.x excluded subdomain" "500 *" "$(tcp_table 10001 user@mail.example.net)"
            echo "${forward#200 }" > "$state/srs"
            touch "$state/from-1.x"
        else
            # 2.x: our package's format already. Started directly: the
            # distribution's init script doesn't daemonize it (ours does,
            # see overlays/init-daemonize.patch).
            sed -i 's/^domains = .*/domains = { "example.org", ".example.net" }/' "$CONF"
            postsrsd -C "$CONF" -D
            wait_for "postsrsd to answer" socketmap forward probe@remote.test
            forward="$(socketmap forward user@remote.test)"
            expect "2.x forward" 'OK SRS0=????=??=remote.test=user@example.org' "$forward"
            echo "${forward#OK }" > "$state/srs"
            sha256sum "$CONF" > "$state/conf.sha256"
        fi
        echo "rewritten by $version: $(cat "$state/srs")"
        stop_postsrsd
        ;;
    check)
        version="$(postsrsd -v 2>&1)"
        [[ "$version" == *"${FRESHDEB_UPSTREAM_VERSION:?}"* ]] || { echo "$version" >&2; exit 1; }
        sha256sum -c --quiet "$state/secret.sha256"
        if [[ -e "$state/apparmor" ]]; then
            [[ ! -e "$PROFILE" ]] || { echo "$PROFILE is still there" >&2; exit 1; }
            if dpkg-query -W -f '${Conffiles}' postsrsd | grep -qF "$PROFILE"; then
                echo "$PROFILE is still a conffile of postsrsd" >&2
                exit 1
            fi
        fi
        if [[ -e "$state/from-1.x" ]]; then
            # The settings from /etc/default/postsrsd, in 2.x's format.
            for line in 'srs-domain = "example.org"' \
                        'domains = { "example.org", ".example.net", "example.com" }' \
                        'separator = "+"'; do
                grep -qxF "$line" "$CONF" || { echo "not in $CONF: $line" >&2; cat "$CONF" >&2; exit 1; }
            done
            separator='+'
        else
            sha256sum -c --quiet "$state/conf.sha256"
            separator='='
        fi
        stop_postsrsd
        start_postsrsd
        wait_for "postsrsd to answer" socketmap forward probe@remote.test
        srs="$(cat "$state/srs")"
        expect "reverse of the old version's address" "OK user@remote.test" "$(socketmap reverse "$srs")"
        expect "forward" "OK SRS0${separator}????=??=remote.test=user@example.org" \
            "$(socketmap forward user@remote.test)"
        expect "local subdomain" "NOTFOUND*" "$(socketmap forward user@mail.example.net)"
        stop_postsrsd
        ;;
esac
