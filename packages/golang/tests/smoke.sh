#!/bin/bash
#
# Smoke test for Go, run as root in a fresh container right after our
# packages were installed (and again after an upgrade from the distro's
# version). Builds only local modules; nothing leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 1.27.3

set -euo pipefail

# shellcheck source=packages/golang/tests/lib.sh
source "$(dirname "$0")/lib.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "go and gofmt are this Go"
arch="$(dpkg --print-architecture)"
version="$(go version)"
[[ "$version" == "go version go${EXPECTED} linux/${arch}" ]] || { echo "$version" >&2; exit 1; }
for tool in go gofmt; do
    target="$(readlink -f "/usr/bin/$tool")"
    [[ "$target" == "$GOROOT_EXPECTED/bin/$tool" ]] || { echo "/usr/bin/$tool -> $target" >&2; exit 1; }
done
[[ "$(go env GOROOT)" == "$GOROOT_EXPECTED" ]] || { go env GOROOT >&2; exit 1; }

echo "upstream's defaults (go.env)"
# Not overridden by the packaging: module mirror, checksum database and
# toolchain switching as upstream ships them.
[[ "$(go env GOPROXY)" == "https://proxy.golang.org,direct" ]] || { go env GOPROXY >&2; exit 1; }
[[ "$(go env GOSUMDB)" == "sum.golang.org" ]] || { go env GOSUMDB >&2; exit 1; }
[[ "$(go env GOTOOLCHAIN)" == "auto" ]] || { go env GOTOOLCHAIN >&2; exit 1; }

echo "standard library sources and documentation"
test -f "$GOROOT_EXPECTED/src/fmt/print.go"
doc="$(go doc fmt.Println)"
[[ "$doc" == *"func Println("* ]] || { echo "$doc" >&2; exit 1; }

echo "a static program without cgo"
new_module "$work/hello" example.com/hello
cat > "$work/hello/main.go" <<'EOF'
package main

import "fmt"

func main() { fmt.Println("hello from go") }
EOF
(cd "$work/hello" && offline env CGO_ENABLED=0 go build -o hello . && go vet ./...)
[[ "$("$work/hello/hello")" == "hello from go" ]]
linkage="$(ldd "$work/hello/hello" 2>&1 || true)"
[[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$linkage" >&2; exit 1; }

echo "cgo"
new_module "$work/cgo" example.com/cgo
cat > "$work/cgo/main.go" <<'EOF'
package main

/*
static int twice(int x) { return 2 * x; }
*/
import "C"
import "fmt"

func main() { fmt.Println(int(C.twice(21))) }
EOF
(cd "$work/cgo" && offline env CGO_ENABLED=1 go build -o cgo .)
[[ "$("$work/cgo/cgo")" == "42" ]]

echo "go test with a local module (replace), and the race detector"
new_module "$work/lib" example.com/lib
cat > "$work/lib/lib.go" <<'EOF'
package lib

func Add(a, b int) int { return a + b }
EOF
new_module "$work/app" example.com/app
printf '\nrequire example.com/lib v0.0.0\n\nreplace example.com/lib => ../lib\n' >> "$work/app/go.mod"
cat > "$work/app/app_test.go" <<'EOF'
package app

import (
	"sync"
	"testing"

	"example.com/lib"
)

func TestAdd(t *testing.T) {
	var wg sync.WaitGroup
	var mu sync.Mutex
	sum := 0
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			mu.Lock()
			sum = lib.Add(sum, 1)
			mu.Unlock()
		}()
	}
	wg.Wait()
	if sum != 4 {
		t.Fatalf("sum = %d", sum)
	}
}
EOF
(cd "$work/app" && offline go test ./... && offline go test -race ./...)

echo "gofmt"
printf 'package x\nfunc  f( ) {}\n' > "$work/fmt.go"
formatted="$(gofmt "$work/fmt.go")"
[[ "$formatted" == $'package x\n\nfunc f() {}' ]] || { echo "$formatted" >&2; exit 1; }

echo "the default packages point to this Go"
for package in golang-go golang-src; do
    depends="$(dpkg-query -W -f='${Depends}' "$package")"
    [[ "$depends" == *"golang-$MINOR-"* ]] || { echo "$package: $depends" >&2; exit 1; }
done
echo "smoke test passed"
