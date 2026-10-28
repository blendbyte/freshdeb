#!/bin/bash
#
# Smoke test for lazygit, run as root in a fresh container right after our
# package was installed (and again after an upgrade from the distro's
# version). lazygit is interactive, so the UI runs in a pseudo-terminal.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 0.66.2

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/lazygit/tests/lib.sh
source "$(dirname "$0")/lib.sh"

echo "binary reports the expected version and commit"
version="$(lazygit --version)"
[[ "$version" =~ ^commit=[0-9a-f]{40},\ build\ date=[0-9-]+T[0-9:]+Z,\ build\ source=freshdeb,\ version=${expected//./\\.}, ]] \
    || { echo "$version" >&2; exit 1; }

echo "static binary"
linkage="$(ldd /usr/bin/lazygit 2>&1 || true)"
[[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$linkage" >&2; exit 1; }

echo "configuration"
test "$(lazygit --print-config-dir)" = /root/.config/lazygit
config="$(lazygit --config)"
[[ "$config" == *"gui:"* ]] || { echo "$config" >&2; exit 1; }

echo "the UI starts on a repository, shows its changes and quits"
repo="$(new_repo)"
# The diff is drawn last.
screen="$(ui_screen "$repo" +changed)"
# Single words: lazygit draws spaces as cursor movements. The file is listed
# and its diff shown.
for text in Files freshdeb-file.txt +changed; do
    [[ "$screen" == *"$text"* ]] || { echo "not on the screen: $text" >&2; cat -v <<<"$screen" >&2; exit 1; }
done
rm -rf "$repo"

echo "man page is packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L lazygit)"
grep -qxF /usr/share/man/man1/lazygit.1.gz <<<"$files" \
    || { echo "missing from the package: the man page" >&2; exit 1; }
echo "smoke test passed"
