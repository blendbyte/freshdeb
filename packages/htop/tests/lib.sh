#!/bin/bash
#
# Helpers for the htop tests (sourced, not run): htop in a pseudo-terminal
# (script(1)), for the distributions' versions as well as ours.

# htop_screen <word> [htop args...]: run htop in a 200x50 terminal, press q
# once <word> is on the screen (or after 30 seconds), and print what it drew.
# Fails if htop doesn't quit cleanly.
htop_screen() {
    local word="$1" out fifo keys pid status=0
    shift
    out="$(mktemp)"
    fifo="$(mktemp -u)"
    mkfifo "$fifo"
    TERM=xterm-256color timeout 60 \
        script -qefc "stty cols 200 rows 50; htop $*" "$out" <"$fifo" >/dev/null &
    pid=$!
    exec {keys}>"$fifo"
    for _ in $(seq 1 150); do
        grep -qF -- "$word" "$out" 2>/dev/null && break
        sleep 0.2
    done
    sleep 0.5
    printf q >&"$keys"
    exec {keys}>&-
    wait "$pid" || status=$?
    rm -f "$fifo"
    if (( status != 0 )); then
        echo "htop did not quit cleanly" >&2
        cat -v "$out" >&2
        rm -f "$out"
        return 1
    fi
    cat "$out"
    rm -f "$out"
}
