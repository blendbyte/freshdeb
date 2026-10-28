#!/bin/bash
#
# Upgrade test for htop: "seed" gives the distribution's version a user
# htoprc with only three columns (PID, CPU%, Command), "check" makes sure
# ours shows the same columns.

set -euo pipefail

# shellcheck source=packages/htop/tests/lib.sh
source "$(dirname "$0")/lib.sh"
config=/root/.config/htop/htoprc

three_columns() {
    local screen
    screen="$(htop_screen Command)"
    [[ "$screen" == *PID* && "$screen" == *Command* ]] || { cat -v <<<"$screen" >&2; return 1; }
    # USER and VIRT are default columns; the configuration drops them.
    [[ "$screen" != *VIRT* && "$screen" != *USER* ]] \
        || { echo "the configuration was not used" >&2; cat -v <<<"$screen" >&2; return 1; }
}

case "${1:?seed or check}" in
    seed)
        htop --version
        mkdir -p "$(dirname "$config")"
        # Numeric field IDs, as every version since 2.x reads them.
        printf 'fields=0 46 1\n' > "$config"
        three_columns
        ;;
    check)
        version="$(htop --version)"
        [[ "$version" == "htop ${FRESHDEB_UPSTREAM_VERSION:?}"* ]] || { echo "$version" >&2; exit 1; }
        three_columns
        ;;
esac
