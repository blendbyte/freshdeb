#!/bin/bash
#
# Upgrade test for Go: with the distribution's Go ("seed"), a user sets go env
# -w settings and builds a module; after the upgrade ("check") ours is the
# default go, keeps the settings and builds the module again. The
# distribution's own minor, if it is another one, stays installed and usable
# next to ours.

set -euo pipefail

# shellcheck source=packages/golang/tests/lib.sh
source "$(dirname "$0")/lib.sh"
state=/var/tmp/freshdeb-go
module="$state/module"

build_module() {
    (cd "$module" && offline go build -o "$state/app" . )
    [[ "$("$state/app")" == "built by $(go env GOVERSION)" ]]
}

case "${1:?seed or check}" in
    seed)
        mkdir -p "$state"
        go version
        readlink -f /usr/bin/go > "$state/distro-go"
        # A user setting, in ~/.config/go/env.
        go env -w GOPRIVATE=example.internal
        new_module "$module" example.com/app "$(go env GOVERSION | sed -E 's/^go([0-9]+\.[0-9]+).*/\1/')"
        cat > "$module/main.go" <<'EOF'
package main

import (
	"fmt"
	"runtime"
)

func main() { fmt.Println("built by " + runtime.Version()) }
EOF
        build_module
        ;;
    check)
        version="$(go version)"
        [[ "$version" == "go version go${EXPECTED} "* ]] || { echo "$version" >&2; exit 1; }
        [[ "$(go env GOPRIVATE)" == "example.internal" ]] || { echo "go env -w setting lost" >&2; exit 1; }
        build_module
        # The distribution's minor stays installed (golang-go no longer
        # depends on it) and still works, unless ours replaced that package.
        old="$(cat "$state/distro-go")"
        if [[ "$old" != "$GOROOT_EXPECTED/bin/go" ]]; then
            old_version="$("$old" version)"
            echo "still installed: $old_version"
            [[ "$old_version" == "go version go"* ]]
        fi
        ;;
esac
