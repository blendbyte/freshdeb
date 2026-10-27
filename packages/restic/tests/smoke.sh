#!/bin/bash
#
# Smoke test for restic, run as root in a fresh container right after our
# package was installed (and again after an upgrade from the distro's
# version).
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 0.19.1

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "binary reports the expected version"
restic version | grep -E "^restic ${expected//./\\.} compiled with go"

echo "static binary, no self-update"
help="$(restic --help 2>&1 || true)"
[[ "$help" != *self-update* ]]
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
linkage="$(ldd /usr/bin/restic 2>&1 || true)"
[[ "$linkage" == *"not a dynamic executable"* ]]

echo "man pages, shell completions and docs are packaged"
# From the package's file list: Ubuntu's minimized images tell dpkg to skip
# /usr/share/man and /usr/share/doc, so they're not on disk here.
files="$(dpkg -L restic)"
for path in /usr/share/man/man1/restic.1.gz \
            /usr/share/bash-completion/completions/restic \
            /usr/share/zsh/vendor-completions/_restic \
            /usr/share/fish/vendor_completions.d/restic.fish \
            /usr/share/doc/restic/html/index.html; do
    grep -qxF "$path" <<<"$files" || { echo "missing from the package: $path" >&2; exit 1; }
done

echo "backup, check and restore round-trip"
export RESTIC_REPOSITORY="$work/repo" RESTIC_PASSWORD=freshdeb
mkdir -p "$work/data/sub"
head -c 1048576 /dev/urandom > "$work/data/random.bin"
echo "hello" > "$work/data/sub/hello.txt"
restic init --repository-version 2
restic backup "$work/data" --tag smoke
restic check --read-data
snapshots="$(restic snapshots --tag smoke --json)"
[[ "$snapshots" == *'"tags":["smoke"]'* ]]
restic restore latest --target "$work/restore"
diff -r "$work/data" "$work/restore$work/data"

echo "wrong password is refused"
if RESTIC_PASSWORD=wrong restic snapshots >/dev/null 2>&1; then
    echo "restic accepted a wrong password" >&2
    exit 1
fi
echo "smoke test passed"
