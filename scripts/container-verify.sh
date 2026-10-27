#!/usr/bin/env bash
#
# container-verify.sh: test built packages inside a pristine distribution
# container (no build tools). Invoked by scripts/verify.sh.
#
#   container-verify.sh install   fresh install, smoke test, autopkgtests
#   container-verify.sh upgrade   install the distribution's own version first,
#                                 seed data, upgrade to ours, check data and
#                                 run the smoke test again
#
# Mounts:  /debs   build output (debs, build-info.json, debian-tests/)
#          /tests  packages/<name>/tests
# Env:     FRESHDEB_VERSION FRESHDEB_UPSTREAM_VERSION FRESHDEB_BINARIES

set -euo pipefail

MODE="${1:?install or upgrade}"
: "${FRESHDEB_VERSION:?}" "${FRESHDEB_UPSTREAM_VERSION:?}" "${FRESHDEB_BINARIES:?}"
export FRESHDEB_VERSION FRESHDEB_UPSTREAM_VERSION
export DEBIAN_FRONTEND=noninteractive
APT=(apt-get -y -q -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)

section() { echo "::group::$*"; }
endsection() { echo "::endgroup::"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

section "apt-get update"
apt-get update -qq
endsection

install_ours() {
    section "Install freshdeb packages ($FRESHDEB_VERSION)"
    "${APT[@]}" install /debs/*.deb
    local binary installed
    for binary in $FRESHDEB_BINARIES; do
        installed="$(dpkg-query -W -f='${Version}' "$binary" 2>/dev/null || true)"
        [[ "$installed" == "$FRESHDEB_VERSION" ]] || fail "$binary is at '$installed', expected $FRESHDEB_VERSION"
    done
    endsection
}

run_smoke() {
    section "Smoke test"
    bash -euo pipefail /tests/smoke.sh </dev/null || fail "tests/smoke.sh"
    endsection
}

# Minimal autopkgtest runner: Debian's tests against the installed packages.
run_autopkgtests() {
    local plan=/debs/debian-tests/plan.tsv
    [[ -s "$plan" ]] || { echo "No autopkgtests."; return 0; }
    rm -rf /tmp/autopkgtest && cp -r /debs/debian-tests /tmp/autopkgtest
    chmod -R +x /tmp/autopkgtest/debian/tests
    local name allow_stderr skip deps command failed=0
    while IFS=$'\t' read -r name allow_stderr skip deps command; do
        section "autopkgtest $name"
        if [[ "$skip" != "-" ]]; then
            echo "SKIP: needs $skip"
            endsection
            continue
        fi
        if [[ "$deps" != "-" ]]; then
            # shellcheck disable=SC2086
            "${APT[@]}" install $deps </dev/null
        fi
        AUTOPKGTEST_TMP="$(mktemp -d)" AUTOPKGTEST_ARTIFACTS="$(mktemp -d)"
        export AUTOPKGTEST_TMP AUTOPKGTEST_ARTIFACTS
        if (cd /tmp/autopkgtest && bash -c "$command") </dev/null 2>/tmp/stderr; then
            if [[ -s /tmp/stderr && "$allow_stderr" != "1" ]]; then
                cat /tmp/stderr >&2
                echo "FAIL: $name wrote to stderr (no allow-stderr restriction)" >&2
                failed=1
            else
                cat /tmp/stderr >&2
                echo "PASS: $name"
            fi
        else
            cat /tmp/stderr >&2
            echo "FAIL: $name" >&2
            failed=1
        fi
        endsection
    done < "$plan"
    (( failed == 0 )) || fail "autopkgtests"
}

case "$MODE" in
    install)
        install_ours
        run_smoke
        run_autopkgtests
        ;;
    upgrade)
        distro=()
        for binary in $FRESHDEB_BINARIES; do
            if [[ -n "$(apt-cache madison "$binary" 2>/dev/null)" ]]; then
                distro+=("$binary")
            fi
        done
        if (( ${#distro[@]} == 0 )); then
            echo "The distribution does not ship any of: $FRESHDEB_BINARIES. Nothing to upgrade from."
            exit 0
        fi
        section "Install distribution packages: ${distro[*]}"
        "${APT[@]}" install "${distro[@]}"
        for binary in "${distro[@]}"; do
            echo "  $binary $(dpkg-query -W -f='${Version}' "$binary")"
        done
        endsection
        if [[ -f /tests/upgrade.sh ]]; then
            section "Seed data on the distribution version"
            bash -euo pipefail /tests/upgrade.sh seed </dev/null || fail "tests/upgrade.sh seed"
            endsection
        fi
        install_ours
        if [[ -f /tests/upgrade.sh ]]; then
            section "Check data after the upgrade"
            bash -euo pipefail /tests/upgrade.sh check </dev/null || fail "tests/upgrade.sh check"
            endsection
        fi
        run_smoke
        ;;
    *)
        fail "unknown mode $MODE"
        ;;
esac

echo "OK: $MODE"
