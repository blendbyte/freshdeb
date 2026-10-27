#!/bin/bash
#
# Upgrade test for neovim: "seed" puts a user configuration in place on the
# distribution's (older) neovim, "check" makes sure our build picks it up and
# still owns the editor/vi alternatives after the upgrade.

set -euo pipefail

config=/root/.config/nvim/init.lua

case "${1:?seed or check}" in
    seed)
        version="$(nvim --version)"
        echo "${version%%$'\n'*}"
        mkdir -p "$(dirname "$config")"
        echo 'vim.g.freshdeb_seed = "kept"' > "$config"
        test "$(nvim --headless -c 'lua io.write(vim.g.freshdeb_seed)' -c 'qa!' 2>&1)" = kept
        ;;
    check)
        version="$(nvim --version)"
        [[ "$version" == "NVIM v${FRESHDEB_UPSTREAM_VERSION:?}"* ]]
        test "$(nvim --headless -c 'lua io.write(vim.g.freshdeb_seed)' -c 'qa!' 2>&1)" = kept
        test "$(readlink -f /usr/bin/editor)" = /usr/bin/nvim
        ;;
esac
