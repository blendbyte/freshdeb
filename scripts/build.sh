#!/usr/bin/env bash
#
# build.sh: build one package for one track, codename and architecture.
#
#   scripts/build.sh <package> <track> <codename> <arch> [out-dir]
#
# Example (on an arm64 machine):
#   scripts/build.sh valkey 9.1 bookworm arm64
#
# Results land in out-dir (default: artifacts/<package>_<track>_<codename>_<arch>):
# the .debs, build-info.json, and debian-tests/ for verify.sh. If the
# distribution already ships an equal or newer version, build-info.json says
# "skipped" and there are no .debs.
#
# Env:
#   BUILDER_IMAGE   use this image instead of building builders/Dockerfile
#   PUBLISH         "true" marks the result publishable (CI sets this)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGE="${1:?package required}"
TRACK="${2:?track required}"
CODENAME="${3:?codename required}"
ARCH="${4:?arch required}"
OUT="${5:-$ROOT/artifacts/${PACKAGE}_${TRACK}_${CODENAME}_${ARCH}}"

image_for() {
    python3 - "$ROOT/config.yaml" "$1" <<'EOF'
import re, sys
# Tiny reader so the host needs no PyYAML: find distributions.<codename>.image
text = open(sys.argv[1]).read()
block = re.search(r"^  " + re.escape(sys.argv[2]) + r":\n((?:    .*\n)+)", text, re.M)
image = block and re.search(r"^    image:\s*(\S+)", block.group(1), re.M)
if not image:
    sys.exit(f"unknown codename {sys.argv[2]}")
print(image.group(1))
EOF
}

BASE_IMAGE="$(image_for "$CODENAME")"
IMAGE="${BUILDER_IMAGE:-freshdeb-builder:${CODENAME}-${ARCH}}"
if [[ -z "${BUILDER_IMAGE:-}" ]]; then
    docker build --quiet --platform "linux/$ARCH" --build-arg "BASE_IMAGE=$BASE_IMAGE" \
        -t "$IMAGE" "$ROOT/builders" >/dev/null
fi

rm -rf "$OUT"
mkdir -p "$OUT"
chmod 0777 "$OUT"  # the container's build steps write as root

# Changelog date: the commit time of this checkout, so rebuilding the same
# commit gives the same changelog.
SOURCE_DATE_EPOCH="$(git -C "$ROOT" log -1 --format=%ct 2>/dev/null || date +%s)"

docker run --rm \
    --platform "linux/$ARCH" \
    -v "$ROOT:/workspace:ro" \
    -v "$OUT:/out" \
    -e PACKAGE="$PACKAGE" -e TRACK="$TRACK" -e CODENAME="$CODENAME" -e ARCH="$ARCH" \
    -e PUBLISH="${PUBLISH:-false}" -e SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" \
    "$IMAGE" bash /workspace/scripts/container-build.sh
