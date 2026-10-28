#!/bin/bash
#
# Smoke test for nano: versions, editing a file in a pseudo-terminal with
# nano and nano-tiny, the system nanorc and syntax files, the alternatives.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 9.3

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/nano/tests/lib.sh
source "$(dirname "$0")/lib.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "binaries report the expected version"
for binary in nano nano-tiny; do
    version="$("$binary" --version)"
    [[ "$version" == *"GNU nano, version ${expected}"* ]] || { echo "$binary: $version" >&2; exit 1; }
done

echo "nano and nano-tiny write a file: type, ^O, Enter, ^X"
for binary in nano nano-tiny; do
    file="$work/$binary.txt"
    screen="$(nano_session "$binary" "$file" "freshdeb $binary test" "=test" '\x0f' "=Write" '\r' "=Wrote" '\x18')"
    [[ "$(cat "$file" 2>/dev/null)" == "freshdeb $binary test" ]] \
        || { echo "$binary did not write the file" >&2; cat -v <<<"$screen" >&2; exit 1; }
done

echo "the system nanorc and the syntax files load without errors"
# A shell script, so the sh syntax from /usr/share/nano is used too;
# debian.nanorc is the sources.list syntax.
printf '#!/bin/sh\necho freshdeb\n' > "$work/script.sh"
screen="$(nano_session nano "$work/script.sh" "=freshdeb" '\x18')"
no_rc_errors "$screen"
syntaxes="$(find /usr/share/nano -maxdepth 1 -name '*.nanorc' | wc -l)"
(( syntaxes >= 30 )) || { echo "only $syntaxes syntax files" >&2; exit 1; }
test -e /usr/share/nano/debian.nanorc
listed="$(nano --listsyntaxes)"
[[ "$listed" == *" sh "* && "$listed" == *sources.list* ]] || { echo "$listed" >&2; exit 1; }

echo "nano is still an editor alternative"
editors="$(update-alternatives --query editor)"
grep -qE '^Alternative: /(usr/)?bin/nano$' <<<"$editors" || { echo "$editors" >&2; exit 1; }

echo "man page is packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L nano)"
grep -qxF /usr/share/man/man1/nano.1.gz <<<"$files" || { echo "missing from the package: the man page" >&2; exit 1; }
echo "smoke test passed"
