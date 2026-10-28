#!/bin/bash
#
# Helpers for the nano tests (sourced, not run): nano in a pseudo-terminal
# (script(1)), for the distributions' versions as well as ours.

# nano_session <binary> <file> <step>...: open <file> with <binary> in an
# 80x24 terminal and print what it drew. Each step is either "=WORD" (wait
# until WORD is on the screen, up to 10 seconds) or keys to type (printf
# escapes); the last keys must end the session, e.g. with ^X. Fails if it
# doesn't exit by itself.
nano_session() {
    local binary="$1" file="$2" out fifo fd pid step status=0
    shift 2
    out="$(mktemp)"
    fifo="$(mktemp -u)"
    mkfifo "$fifo"
    TERM=xterm-256color timeout 30 \
        script -qefc "stty cols 80 rows 24; $binary '$file'" "$out" <"$fifo" >/dev/null &
    pid=$!
    exec {fd}>"$fifo"
    # The title bar names the program; then it's ready for keys.
    wait_for_screen "$out" 'GNU nano|nano-tiny|nano [0-9]'
    for step in "$@"; do
        if [[ "$step" == =* ]]; then
            wait_for_screen "$out" "${step#=}"
        else
            # shellcheck disable=SC2059  # steps carry printf escapes
            printf "$step" >&"$fd"
        fi
    done
    exec {fd}>&-
    wait "$pid" || status=$?
    rm -f "$fifo"
    if (( status != 0 )); then
        echo "$binary did not exit cleanly (status $status)" >&2
        cat -v "$out" >&2
        rm -f "$out"
        return 1
    fi
    cat "$out"
    rm -f "$out"
}

# wait_for_screen <file> <regex>: until the regex shows up, then a moment
# for the frame to finish.
wait_for_screen() {
    for _ in $(seq 1 100); do
        grep -qE -- "$2" "$1" 2>/dev/null && break
        sleep 0.1
    done
    sleep 0.3
}

# no_rc_errors <screen>: nano reports problems in its rc files on screen.
no_rc_errors() {
    [[ "$1" != *"Error in"* && "$1" != *"rror in "* ]] || { echo "nanorc errors:" >&2; cat -v <<<"$1" >&2; return 1; }
}
