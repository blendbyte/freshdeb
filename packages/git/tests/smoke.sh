#!/bin/bash
#
# Smoke test for git, run as root in a fresh container right after our
# packages were installed (and again after an upgrade from the distro's
# version). Only local repositories; nothing leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 2.56.0

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
work="$(mktemp -d)"
trap 'kill "${daemon_pid:-}" 2>/dev/null || true; rm -rf "$work"' EXIT
export HOME="$work/home" GIT_CONFIG_NOSYSTEM=1
mkdir -p "$HOME"
git config --global user.name "freshdeb"
git config --global user.email "test@freshdeb.invalid"
git config --global init.defaultBranch main

echo "binary reports the expected version"
test "$(git --version)" = "git version ${expected}"

echo "commit, branch, merge, tag and fsck"
git init -q "$work/repo"
cd "$work/repo"
echo one > file.txt
git add file.txt
git commit -q -m one
git switch -q -c feature
echo two >> file.txt
git commit -q -am two
git switch -q main
git merge -q --no-ff -m merge feature
git tag -a v1 -m "tag v1"
test "$(git rev-list --count HEAD)" = 3
test "$(git describe)" = v1
git fsck --strict --no-progress

echo "PCRE2 grep works"
# (?:...) is PCRE-only syntax: this fails without a PCRE-enabled build.
test "$(git grep -P -c '^(?:one|two)$' -- file.txt)" = "file.txt:2"

echo "clone over file:// and git://"
git clone -q "file://$work/repo" "$work/clone-file"
test "$(git -C "$work/clone-file" rev-parse HEAD)" = "$(git rev-parse HEAD)"
git daemon --base-path="$work" --export-all --reuseaddr --listen=127.0.0.1 --port=9418 "$work" &
daemon_pid=$!
for _ in $(seq 1 50); do
    git ls-remote git://127.0.0.1/repo >/dev/null 2>&1 && break
    sleep 0.2
done
git clone -q git://127.0.0.1/repo "$work/clone-git"
test "$(git -C "$work/clone-git" rev-parse HEAD)" = "$(git rev-parse HEAD)"

echo "helpers and add-on commands work"
test -x /usr/lib/git-core/git-remote-https
# Captured first: "cmd | grep -q" trips pipefail when grep exits early.
subtree="$(git subtree -h 2>&1 || true)"
[[ "$subtree" == *"git subtree"* ]] || { echo "$subtree" >&2; exit 1; }
git send-email --dump-aliases >/dev/null
git svn --version
test -x /usr/lib/git-core/git-cvsimport

echo "man pages and docs are packaged"
# From the file lists: Ubuntu-style minimized images skip /usr/share/man.
files="$(dpkg -L git git-man git-doc)"
for path in /usr/share/man/man1/git.1.gz /usr/share/man/man1/git-commit.1.gz \
            /usr/share/doc/git-doc/git.html /usr/share/bash-completion/completions/git; do
    grep -qxF "$path" <<<"$files" || { echo "missing from the packages: $path" >&2; exit 1; }
done
echo "smoke test passed"
