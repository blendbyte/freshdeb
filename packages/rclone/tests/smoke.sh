#!/bin/bash
#
# Smoke test for rclone, run as root in a fresh container right after our
# package was installed (and again after an upgrade from the distro's
# version). Only local remotes; nothing leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 1.75.2

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
work="$(mktemp -d)"
trap 'kill "${serve_pid:-}" 2>/dev/null || true; rm -rf "$work"' EXIT
export RCLONE_CONFIG="$work/rclone.conf"

echo "binary reports the expected version"
version="$(rclone version)"
[[ "$version" == "rclone v${expected}"$'\n'* ]] || { echo "$version" >&2; exit 1; }

echo "static binary"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
linkage="$(ldd /usr/bin/rclone 2>&1 || true)"
[[ "$linkage" == *"not a dynamic executable"* ]]

echo "backends Debian has to leave out are there"
backends="$(rclone help backends)"
for backend in azureblob azurefiles oos storj cloudinary s3 sftp crypt; do
    [[ "$backends" == *"  $backend "* ]] || { echo "missing backend: $backend" >&2; exit 1; }
done
commands="$(rclone help)"
for command in mount "serve" archive; do
    [[ "$commands" == *"  $command "* ]] || { echo "missing command: $command" >&2; exit 1; }
done

echo "man page, completions, docs and mount helper are packaged"
files="$(dpkg -L rclone)"
for path in /usr/share/man/man1/rclone.1.gz /usr/sbin/mount.rclone \
            /usr/share/bash-completion/completions/rclone \
            /usr/share/zsh/vendor-completions/_rclone \
            /usr/share/fish/vendor_completions.d/rclone.fish \
            /usr/share/doc/rclone/MANUAL.html; do
    grep -qxF "$path" <<<"$files" || { echo "missing from the package: $path" >&2; exit 1; }
done
test "$(readlink -f /usr/sbin/mount.rclone)" = /usr/bin/rclone

echo "copy, sync and check between local directories"
mkdir -p "$work/src/sub"
head -c 2097152 /dev/urandom > "$work/src/random.bin"
echo "hello" > "$work/src/sub/hello.txt"
rclone copy "$work/src" "$work/copy"
rclone check "$work/src" "$work/copy"
echo "changed" > "$work/src/sub/hello.txt"
rclone sync "$work/src" "$work/copy"
rclone check "$work/src" "$work/copy"

echo "crypt remote encrypts and decrypts"
rclone config create secret crypt remote="$work/encrypted" password="$(rclone obscure freshdeb)" >/dev/null
rclone copy "$work/src" secret:
encrypted="$(ls "$work/encrypted")"
[[ "$encrypted" != *random.bin* ]] || { echo "file names are not encrypted" >&2; exit 1; }
rclone check "$work/src" secret:
rclone copy secret: "$work/decrypted"
diff -r "$work/src" "$work/decrypted"

echo "serve http works"
rclone serve http "$work/src" --addr 127.0.0.1:8099 &
serve_pid=$!
for _ in $(seq 1 50); do
    rclone lsf --http-url http://127.0.0.1:8099 :http: >/dev/null 2>&1 && break
    sleep 0.2
done
rclone copy --http-url http://127.0.0.1:8099 :http: "$work/fromhttp"
diff -r "$work/src" "$work/fromhttp"
echo "smoke test passed"
