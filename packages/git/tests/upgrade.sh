#!/bin/bash
#
# Upgrade test for git: "seed" creates a repository, a stash, a worktree and
# user configuration with the distribution's git, "check" makes sure our
# build works with all of it, then runs maintenance on the repository.

set -euo pipefail

state=/var/tmp/freshdeb-git
export HOME="$state/home"

case "${1:?seed or check}" in
    seed)
        rm -rf "$state"
        mkdir -p "$HOME"
        git --version
        git config --global user.name "distro git"
        git config --global user.email "seed@freshdeb.invalid"
        git config --global alias.last "log -1 --format=%s"
        git init -q "$state/repo"
        cd "$state/repo"
        for i in 1 2 3; do echo "$i" >> file.txt; git add file.txt; git commit -q -m "commit $i"; done
        echo stashed >> file.txt
        git stash -q
        git branch side
        git worktree add -q "$state/side" side
        git gc -q
        ;;
    check)
        version="$(git --version)"
        [[ "$version" == "git version ${FRESHDEB_UPSTREAM_VERSION:?}" ]]
        cd "$state/repo"
        git fsck --strict --no-progress
        test "$(git rev-list --count HEAD)" = 3
        test "$(git last)" = "commit 3"
        test "$(git stash list | wc -l)" = 1
        test "$(git -C "$state/side" rev-parse --abbrev-ref HEAD)" = side
        echo 4 >> file.txt
        git commit -q -am "commit 4"
        git maintenance run --task=gc --task=commit-graph
        git fsck --strict --no-progress
        ;;
esac
