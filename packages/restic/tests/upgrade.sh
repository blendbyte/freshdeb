#!/bin/bash
#
# Upgrade test for restic: "seed" writes a repository with the distribution's
# (older) restic, "check" reads, verifies and extends it with ours, the way
# users keep using their existing backup repositories after upgrading.

set -euo pipefail

state=/var/tmp/freshdeb-restic
export RESTIC_REPOSITORY="$state/repo" RESTIC_PASSWORD=freshdeb

case "${1:?seed or check}" in
    seed)
        rm -rf "$state"
        mkdir -p "$state/data"
        head -c 524288 /dev/urandom > "$state/data/random.bin"
        echo "written by the distro's restic" > "$state/data/note.txt"
        restic version
        restic init
        restic backup "$state/data" --tag seed
        ;;
    check)
        restic version | grep -E "^restic ${FRESHDEB_UPSTREAM_VERSION:?}"
        restic check --read-data
        snapshots="$(restic snapshots --tag seed --json)"
        [[ "$snapshots" == *'"tags":["seed"]'* ]]
        restic restore latest --tag seed --target "$state/restore"
        diff -r "$state/data" "$state/restore$state/data"
        restic backup "$state/data" --tag upgraded
        restic check
        ;;
esac
