#!/bin/bash
#
# Upgrade test for nano: "seed" gives the distribution's version a user
# ~/.nanorc and an edited /etc/nanorc (which dpkg then keeps); "check"
# makes sure ours reads both without errors.

set -euo pipefail

# shellcheck source=packages/nano/tests/lib.sh
source "$(dirname "$0")/lib.sh"
file=/root/freshdeb-nano.txt

# constantshow puts the cursor position in the status bar.
uses_settings() {
    local screen
    printf 'freshdeb\n' > "$file"
    # A cursor move, then the position shows; ^X.
    screen="$(nano_session nano "$file" "=freshdeb" '\e[C' "=col" '\x18')"
    no_rc_errors "$screen"
    [[ "$screen" == *col* ]] || { echo "the settings were not used" >&2; cat -v <<<"$screen" >&2; return 1; }
}

case "${1:?seed or check}" in
    seed)
        nano --version | head -n 1
        printf 'set constantshow\nset linenumbers\nset tabsize 4\n' > /root/.nanorc
        printf '\n# freshdeb upgrade test\nset autoindent\n' >> /etc/nanorc
        uses_settings
        ;;
    check)
        version="$(nano --version)"
        [[ "$version" == *"GNU nano, version ${FRESHDEB_UPSTREAM_VERSION:?}"* ]] || { echo "$version" >&2; exit 1; }
        grep -qxF '# freshdeb upgrade test' /etc/nanorc || { echo "/etc/nanorc was replaced" >&2; exit 1; }
        uses_settings
        ;;
esac
