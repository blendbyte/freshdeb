#!/bin/bash
#
# Upgrade test for rclone: "seed" uses the distribution's (much older)
# rclone to configure an encrypted crypt remote, with its obscured password
# in rclone.conf, and to write data through it. "check" makes sure our build
# reads that configuration and decrypts the data: what users rely on when
# they upgrade with existing remotes.

set -euo pipefail

state=/var/tmp/freshdeb-rclone
export RCLONE_CONFIG="$state/rclone.conf"

case "${1:?seed or check}" in
    seed)
        rm -rf "$state"
        mkdir -p "$state/data/sub"
        head -c 524288 /dev/urandom > "$state/data/random.bin"
        echo "written by the distro's rclone" > "$state/data/sub/note.txt"
        rclone version
        # Written by hand: "config create key=value" postdates jammy's 1.53,
        # the config file format doesn't.
        printf '[secret]\ntype = crypt\nremote = %s\npassword = %s\n' \
            "$state/encrypted" "$(rclone obscure freshdeb)" > "$RCLONE_CONFIG"
        rclone copy "$state/data" secret:
        rclone check "$state/data" secret:
        ;;
    check)
        version="$(rclone version)"
        [[ "$version" == "rclone v${FRESHDEB_UPSTREAM_VERSION:?}"$'\n'* ]]
        rclone check "$state/data" secret:
        rclone copy secret: "$state/restored"
        diff -r "$state/data" "$state/restored"
        echo "added after the upgrade" > "$state/data/new.txt"
        rclone copy "$state/data" secret:
        rclone check "$state/data" secret:
        ;;
esac
