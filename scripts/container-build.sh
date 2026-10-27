#!/usr/bin/env bash
#
# container-build.sh: build one package/track/codename/arch inside the
# builder image. Invoked by scripts/build.sh; not meant to run on a host.
#
# Mounts:  /workspace  repository checkout (read-only)
#          /out        results: .debs, build-info.json, debian-tests/
#
# Env:     PACKAGE TRACK CODENAME ARCH  required
#          PUBLISH                      "true" marks the result publishable
#          SOURCE_DATE_EPOCH            changelog date (reproducibility)
#          NOCHECK                      "true" skips upstream's test suite
#
# Runs as root for apt; the package itself builds as the "builder" user.

set -euo pipefail

: "${PACKAGE:?}" "${TRACK:?}" "${CODENAME:?}" "${ARCH:?}"
PUBLISH="${PUBLISH:-false}"
FRESHDEB=(python3 /workspace/scripts/freshdeb.py --root /workspace)
WORK=/build/work
OUT=/out

export DEBIAN_FRONTEND=noninteractive

[[ "$(dpkg --print-architecture)" == "$ARCH" ]] || {
    echo "ERROR: container is $(dpkg --print-architecture), asked to build $ARCH" >&2
    exit 1
}

publish_flag=()
[[ "$PUBLISH" == "true" ]] && publish_flag=(--publish)

echo "::group::Prepare source"
"${FRESHDEB[@]}" prepare --package "$PACKAGE" --track "$TRACK" --codename "$CODENAME" \
    --arch "$ARCH" --out "$WORK" "${publish_flag[@]}"
echo "::endgroup::"

info() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$WORK/build-info.json" "$1"; }
info_list() { python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))[sys.argv[2]]))' "$WORK/build-info.json" "$1"; }

echo "::group::Version guard"
apt-get update -qq
"${FRESHDEB[@]}" guard --info "$WORK/build-info.json"
echo "::endgroup::"

if [[ "$(info skipped)" == "True" ]]; then
    cp "$WORK/build-info.json" "$OUT/"
    exit 0
fi

SRC="$WORK/$(info source_dir)"
profiles="$(info_list build_profiles)"
options="$(info_list build_options)"
if [[ "${NOCHECK:-false}" == "true" ]]; then
    # Canary and test-only builds: upstream's code didn't change, so skip its
    # test suite (and the build-dependencies only it needs).
    [[ " $options " == *" nocheck "* ]] || options="${options:+$options }nocheck"
    [[ " $profiles " == *" nocheck "* ]] || profiles="${profiles:+$profiles }nocheck"
    echo "Skipping upstream's test suite (NOCHECK)"
fi

echo "::group::Install build dependencies"
profile_args=()
[[ -n "$profiles" ]] && profile_args=(-P "${profiles// /,}")
apt-get build-dep -y --no-install-recommends "${profile_args[@]}" "$SRC"
echo "::endgroup::"

build_type=any
[[ "$(info build_arch_indep)" == "True" ]] && build_type=any,all
# Packages without architecture-dependent binaries are only planned on the
# primary architecture, so "any" alone never comes up empty there.
if ! python3 - "$WORK/build-info.json" <<'EOF'
import json, sys
info = json.load(open(sys.argv[1]))
sys.exit(0 if any(a != "all" for a in info["binaries"].values()) else 1)
EOF
then
    build_type=all
fi

# Extra environment from prepare, e.g. a pinned Go toolchain in offline mode.
mapfile -t build_env < <(python3 -c 'import json,sys
for k, v in json.load(open(sys.argv[1])).get("build_env", {}).items(): print(f"{k}={v}")' "$WORK/build-info.json")

echo "::group::Build ($build_type)"
chown -R builder: "$WORK"
runuser -u builder -- env \
    DEB_BUILD_OPTIONS="$options noautodbgsym noddebs parallel=$(nproc)" \
    ${profiles:+DEB_BUILD_PROFILES="$profiles"} \
    "${build_env[@]}" \
    bash -c "cd '$SRC' && dpkg-buildpackage --build=$build_type -us -uc"
echo "::endgroup::"

shopt -s nullglob
debs=("$WORK"/*.deb)
(( ${#debs[@]} )) || { echo "ERROR: build produced no .deb files" >&2; exit 1; }
cp "${debs[@]}" "$OUT/"
if [[ -d "$SRC/debian/tests" ]]; then
    mkdir -p "$OUT/debian-tests/debian"
    cp -r "$SRC/debian/tests" "$OUT/debian-tests/debian/tests"
    "${FRESHDEB[@]}" autopkgtest-plan "$SRC/debian/tests" "$OUT/debian-tests/plan.tsv"
fi

python3 - "$WORK/build-info.json" "$OUT" <<'EOF'
import json, sys
from pathlib import Path
info = json.load(open(sys.argv[1]))
out = Path(sys.argv[2])
info["debs"] = sorted(p.name for p in out.glob("*.deb"))
(out / "build-info.json").write_text(json.dumps(info, indent=2) + "\n")
EOF

echo "Built:"
for deb in "$OUT"/*.deb; do
    echo "  $(dpkg-deb -f "$deb" Package) $(dpkg-deb -f "$deb" Version) $(dpkg-deb -f "$deb" Architecture)"
done
