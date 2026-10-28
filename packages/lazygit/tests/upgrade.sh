#!/bin/bash
#
# Upgrade test for lazygit (trixie and resolute ship it): "seed" puts a user
# configuration in place for the distribution's version (Dutch UI), "check"
# makes sure ours picks it up.

set -euo pipefail

# shellcheck source=packages/lazygit/tests/lib.sh
source "$(dirname "$0")/lib.sh"
config=/root/.config/lazygit/config.yml

dutch_ui() {
    local repo screen
    repo="$(new_repo)"
    screen="$(ui_screen "$repo" Bestanden)"
    rm -rf "$repo"
    # "Bestanden": the Files panel in Dutch.
    [[ "$screen" == *Bestanden* ]] || { echo "the configuration was not used" >&2; cat -v <<<"$screen" >&2; return 1; }
}

case "${1:?seed or check}" in
    seed)
        lazygit --version
        mkdir -p "$(dirname "$config")"
        printf 'gui:\n  language: nl\n' > "$config"
        dutch_ui
        ;;
    check)
        version="$(lazygit --version)"
        [[ "$version" == *"version=${FRESHDEB_UPSTREAM_VERSION:?},"* ]] || { echo "$version" >&2; exit 1; }
        dutch_ui
        # Back to the defaults for the smoke test that follows.
        rm -f "$config"
        ;;
esac
