#!/bin/bash
#
# Helpers for the Go tests (sourced, not run): the expected version and
# paths, and running go without the network. They work with the
# distributions' versions as well as with ours.

# shellcheck disable=SC2034  # used by the scripts that source this
EXPECTED="${FRESHDEB_UPSTREAM_VERSION:?}"
MINOR="${EXPECTED%.*}"
# shellcheck disable=SC2034
GOROOT_EXPECTED="/usr/lib/go-$MINOR"

# A C compiler for cgo; the test containers don't have one.
command -v gcc >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gcc libc6-dev >/dev/null

# offline <command...>: run with nothing leaving the container: no module
# proxy, no toolchain downloads.
offline() {
    GOPROXY=off GOTOOLCHAIN=local GOFLAGS=-mod=mod "$@"
}

# new_module <dir> <module path> [go version]: an empty module.
new_module() {
    mkdir -p "$1"
    printf 'module %s\n\ngo %s\n' "$2" "${3:-$MINOR}" > "$1/go.mod"
}
