#!/bin/bash
#
# Smoke test for htop: version, the UI in a pseudo-terminal, the man page.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 3.5.3

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/htop/tests/lib.sh
source "$(dirname "$0")/lib.sh"
export HOME
HOME="$(mktemp -d)"
trap 'rm -rf "$HOME"' EXIT

echo "binary reports the expected version"
version="$(htop --version)"
[[ "$version" == "htop ${expected}"* ]] || { echo "$version" >&2; exit 1; }

echo "the UI shows the process list and meters, and quits"
screen="$(htop_screen Command)"
# Single words: htop draws spaces as cursor movements.
for text in PID Command Mem Load; do
    [[ "$screen" == *"$text"* ]] || { echo "not on the screen: $text" >&2; cat -v <<<"$screen" >&2; exit 1; }
done

echo "it writes its configuration on exit"
grep -q "^htop_version=${expected}" "$HOME/.config/htop/htoprc" \
    || { cat "$HOME/.config/htop/htoprc" >&2; exit 1; }

echo "man page is packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L htop)"
grep -qxF /usr/share/man/man1/htop.1.gz <<<"$files" || { echo "missing from the package: the man page" >&2; exit 1; }
echo "smoke test passed"
