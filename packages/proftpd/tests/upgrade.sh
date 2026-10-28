#!/bin/bash
#
# Upgrade test for proftpd. "seed" edits the distribution's modules.conf the
# way users do, loading mod_vroot and mod_case from the distribution's
# module packages, and checks that apt would upgrade every installed
# proftpd package we build to ours (none kept back, none removed). "check" makes sure
# the edit survived, the modules 1.3.9 needs were added, and the server
# serves the same.

set -euo pipefail

# shellcheck source=packages/proftpd/tests/lib.sh
source "$(dirname "$0")/lib.sh"
state=/var/tmp/freshdeb-proftpd
conf=/etc/proftpd/modules.conf

case "${1:?seed or check}" in
    seed)
        mkdir -p "$state"
        proftpd -v
        sed -i 's/^LoadModule mod_ifsession\.c/LoadModule mod_vroot.c\nLoadModule mod_case.c\n&/' "$conf"
        grep -qx 'LoadModule mod_vroot.c' "$conf" || { cat "$conf" >&2; exit 1; }
        add_ftp_user
        : > "$SNIPPET"
        write_snippet
        proftpd -t
        start_proftpd
        check_modules
        stop_proftpd
        dpkg-query -W -f='${db:Status-Abbrev}${Package}\n' 'proftpd*' \
            | sed -n 's/^ii *//p' > "$state/packages"
        echo "installed: $(tr '\n' ' ' < "$state/packages")"

        # What "apt upgrade" would do with our packages in a repository.
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq apt-utils >/dev/null
        mkdir -p "$state/repo"
        ln -sf /debs/*.deb "$state/repo/"
        (cd "$state/repo" && apt-ftparchive packages . > Packages)
        echo "deb [trusted=yes] file:$state/repo ./" > /etc/apt/sources.list.d/freshdeb-test.list
        apt-get update -qq
        plan="$(apt-get -s upgrade --with-new-pkgs)"
        rm -f /etc/apt/sources.list.d/freshdeb-test.list
        apt-get update -qq
        ours="$(sed -n 's/^Package: //p' "$state/repo/Packages")"
        failed=0
        while read -r package; do
            # Distribution-only packages (jammy's transitional proftpd-basic) stay.
            grep -qx "$package" <<<"$ours" || continue
            if ! grep -q "^Inst $package " <<<"$plan"; then
                echo "apt upgrade does not upgrade $package" >&2
                failed=1
            fi
        done < "$state/packages"
        if grep -E '^Remv proftpd' <<<"$plan" >&2; then
            failed=1
        fi
        (( failed == 0 )) || { echo "$plan" >&2; exit 1; }
        ;;
    check)
        version="$(proftpd -v 2>&1)"
        [[ "$version" == "ProFTPD Version ${FRESHDEB_UPSTREAM_VERSION:?}"* ]] || { echo "$version" >&2; exit 1; }
        for line in 'LoadModule mod_vroot.c' 'LoadModule mod_case.c' 'LoadModule mod_ls.c' 'LoadModule mod_xfer.c'; do
            grep -qx "$line" "$conf" || { echo "modules.conf lacks: $line" >&2; cat "$conf" >&2; exit 1; }
        done
        ours="$(sed -n 's/^Package: //p' "$state/repo/Packages")"
        while read -r package; do
            status="$(dpkg-query -W -f='${db:Status-Abbrev}' "$package" 2>/dev/null || true)"
            [[ "$status" == "ii " ]] || { echo "$package is no longer installed ($status)" >&2; exit 1; }
            grep -qx "$package" <<<"$ours" || continue
            installed="$(dpkg-query -W -f='${Version}' "$package")"
            [[ "$installed" == "${FRESHDEB_VERSION:?}" || "$installed" == *"+$FRESHDEB_VERSION" ]] \
                || { echo "$package is at $installed" >&2; exit 1; }
        done < "$state/packages"
        proftpd -t
        start_proftpd
        check_modules
        stop_proftpd
        rm -f "$SNIPPET"
        ;;
esac
