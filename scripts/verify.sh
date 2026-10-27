#!/usr/bin/env bash
#
# verify.sh: test a build from scripts/build.sh in clean containers.
#
#   scripts/verify.sh <build-dir> [install|upgrade|all]
#
# install  Fresh install on the bare distribution image, then the package's
#          tests/smoke.sh and Debian's autopkgtests.
# upgrade  Install the distribution's own version (when it ships one), run
#          tests/upgrade.sh seed, upgrade to our build, run tests/upgrade.sh
#          check and the smoke test. This is the path real users take.
#
# A build the version guard skipped passes trivially.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$(cd "${1:?build directory required}" && pwd)"
MODES="${2:-all}"
[[ "$MODES" == "all" ]] && MODES="install upgrade"

field() {
    python3 -c 'import json,sys; v=json.load(open(sys.argv[1]))[sys.argv[2]]; print(" ".join(v) if isinstance(v,(list,dict)) else v)' \
        "$DIR/build-info.json" "$1"
}

if [[ "$(field skipped)" == "True" ]]; then
    echo "Skipped build: $(field skip_reason)"
    exit 0
fi

PACKAGE="$(field package)"
ARCH="$(field arch)"
IMAGE="$(field image)"

for mode in $MODES; do
    echo "=== verify $PACKAGE $(field version) on $IMAGE/$ARCH: $mode"
    # SYS_PTRACE: without it even root can't read /proc/<pid>/exe of a
    # daemon running as another user, so "start-stop-daemon --exec" (used
    # by init scripts to stop services) silently matches nothing.
    docker run --rm \
        --platform "linux/$ARCH" \
        --cap-add SYS_PTRACE \
        -v "$DIR:/debs:ro" \
        -v "$ROOT/packages/$PACKAGE/tests:/tests:ro" \
        -v "$ROOT/scripts/container-verify.sh:/usr/local/bin/container-verify.sh:ro" \
        -e FRESHDEB_VERSION="$(field version)" \
        -e FRESHDEB_UPSTREAM_VERSION="$(field upstream_version)" \
        -e FRESHDEB_BINARIES="$(field binaries)" \
        "$IMAGE" bash /usr/local/bin/container-verify.sh "$mode"
done
