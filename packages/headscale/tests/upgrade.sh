#!/bin/bash
#
# Upgrade test for headscale. No distribution ships it, so the upgrade starts
# from upstream's own package of the previous minor (tests.upgrade_from in
# package.yaml). "seed" sets it up the way its users would (edits in
# /etc/headscale/config.yaml, a user and a pre-auth key). "check" follows
# upstream's upgrade steps: dpkg keeps the edited configuration; if the new
# version refuses it (keys removed between minors), the admin compares it
# with the new one and carries the edits over. Then the data has to be all
# there, migrated, with our version recorded in the database.

set -euo pipefail

# shellcheck source=packages/headscale/tests/lib.sh
source "$(dirname "$0")/lib.sh"
state=/var/tmp/freshdeb-headscale

case "${1:?seed or check}" in
    seed)
        mkdir -p "$state"
        headscale version | head -n1
        configure_offline
        sha256sum "$CONFIG" > "$state/config.sha256"
        start_headscale
        headscale users create upgrade-user >/dev/null
        id="$(user_id upgrade-user)"
        headscale preauthkeys create --user "$id" --reusable --expiration 24h -o json | jq -r .key > "$state/key"
        [[ -s "$state/key" ]]
        stop_headscale
        ;;
    check)
        version="$(headscale version)"
        [[ "$version" == "headscale version v${FRESHDEB_UPSTREAM_VERSION:?}"$'\n'* ]] || { echo "$version" >&2; exit 1; }
        # The configuration as upstream's package left it, edits included,
        # and the new one next to it.
        sha256sum -c --quiet "$state/config.sha256"
        test -f "$CONFIG.dpkg-dist"
        prepare_dirs
        if ! refused="$(runuser -u headscale -- headscale configtest 2>&1)"; then
            # It has to say which key, as upstream's changelog does.
            [[ "$refused" == *"has been removed"* ]] || { echo "$refused" >&2; exit 1; }
            echo "the kept configuration is refused, as expected: $(grep -m1 FATAL <<<"$refused")"
            cp "$CONFIG.dpkg-dist" "$CONFIG"
            configure_offline
        fi
        start_headscale
        [[ -n "$(user_id upgrade-user)" ]] || { echo "upgrade-user is gone" >&2; exit 1; }
        [[ "$(headscale preauthkeys list -o json | jq 'length')" -ge 1 ]] || { echo "the pre-auth key is gone" >&2; exit 1; }
        stop_headscale
        stored="$(sqlite3 /var/lib/headscale/db.sqlite 'SELECT version FROM database_versions WHERE id = 1')"
        [[ "$stored" == "v${FRESHDEB_UPSTREAM_VERSION}" ]] || { echo "database version: '$stored'" >&2; exit 1; }
        ;;
esac
