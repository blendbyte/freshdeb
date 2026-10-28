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
#          check and the smoke test. This is the path real users take. When
#          the distributions ship none, the package can name an upstream
#          .deb to start from instead (tests.upgrade_from): downloaded here
#          and checked against its pinned sha256.
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
# Capabilities the package's tests need (package.yaml tests.capabilities),
# e.g. NET_ADMIN to add addresses. Older build-info.json files have none.
caps=()
for cap in $(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1])).get("test_capabilities", [])))' \
        "$DIR/build-info.json"); do
    caps+=(--cap-add "$cap")
done

# The upstream package the upgrade test starts from (tests.upgrade_from),
# downloaded and verified here, so the container only installs it.
upgrade_from=()
from_dir="$(mktemp -d)"
trap 'rm -rf "$from_dir"' EXIT
from_deb="$(python3 - "$DIR/build-info.json" "$from_dir" <<'EOF'
import hashlib, json, os, sys, urllib.request
info = json.load(open(sys.argv[1])).get("test_upgrade_from")
if info:
    path = os.path.join(sys.argv[2], os.path.basename(info["url"]))
    request = urllib.request.Request(info["url"], headers={"User-Agent": "freshdeb"})
    with urllib.request.urlopen(request, timeout=300) as response, open(path, "wb") as out:
        out.write(response.read())
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if digest != info["sha256"]:
        sys.exit(f"{info['url']}: sha256 {digest}, expected {info['sha256']}")
    print(os.path.basename(path))
EOF
)"
if [[ -n "$from_deb" ]]; then
    upgrade_from=(-v "$from_dir:/upgrade-from:ro" -e FRESHDEB_UPGRADE_FROM="/upgrade-from/$from_deb")
fi

for mode in $MODES; do
    echo "=== verify $PACKAGE $(field version) on $IMAGE/$ARCH: $mode"
    # SYS_PTRACE: without it even root can't read /proc/<pid>/exe of a
    # daemon running as another user, so "start-stop-daemon --exec" (used
    # by init scripts to stop services) silently matches nothing.
    docker run --rm \
        --platform "linux/$ARCH" \
        --cap-add SYS_PTRACE \
        ${caps[@]+"${caps[@]}"} \
        ${upgrade_from[@]+"${upgrade_from[@]}"} \
        -v "$DIR:/debs:ro" \
        -v "$ROOT/packages/$PACKAGE/tests:/tests:ro" \
        -v "$ROOT/scripts/container-verify.sh:/usr/local/bin/container-verify.sh:ro" \
        -e FRESHDEB_VERSION="$(field version)" \
        -e FRESHDEB_UPSTREAM_VERSION="$(field upstream_version)" \
        -e FRESHDEB_BINARIES="$(field binaries)" \
        "$IMAGE" bash /usr/local/bin/container-verify.sh "$mode"
done
