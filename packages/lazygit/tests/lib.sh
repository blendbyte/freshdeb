#!/bin/bash
#
# Helpers for the lazygit tests (sourced, not run): a scratch git repository
# and running lazygit's UI in a pseudo-terminal (script(1)) without a real
# one. They work with the distributions' versions as well as with ours.

# ui_screen <repo> <word> [lazygit args...]: start lazygit on <repo> in a
# 120x40 terminal, press q once <word> is on the screen (or after 30
# seconds), and print what it drew. Fails if lazygit doesn't quit cleanly.
ui_screen() {
    local repo="$1" word="$2" out fifo keys pid status=0
    shift 2
    out="$(mktemp)"
    fifo="$(mktemp -u)"
    mkfifo "$fifo"
    # The UI needs a terminal size and type; -f writes the screen out as it
    # is drawn. q quits from the main view.
    TERM=xterm-256color timeout 60 \
        script -qefc "stty cols 120 rows 40; lazygit -p '$repo' $*" "$out" <"$fifo" >/dev/null &
    pid=$!
    exec {keys}>"$fifo"
    for _ in $(seq 1 150); do
        grep -qF -- "$word" "$out" 2>/dev/null && break
        sleep 0.2
    done
    # Let it finish drawing that frame.
    sleep 0.5
    printf q >&"$keys"
    exec {keys}>&-
    wait "$pid" || status=$?
    rm -f "$fifo"
    if (( status != 0 )); then
        echo "lazygit did not quit cleanly" >&2
        cat -v "$out" >&2
        rm -f "$out"
        return 1
    fi
    cat "$out"
    rm -f "$out"
}

# new_repo: a repository with one commit and one changed file; prints its path.
new_repo() {
    local repo
    repo="$(mktemp -d)"
    git -C "$repo" init -q
    git -C "$repo" -c user.name=freshdeb -c user.email=test@freshdeb.invalid \
        commit -q --allow-empty -m "freshdeb test commit"
    echo changed > "$repo/freshdeb-file.txt"
    echo "$repo"
}
