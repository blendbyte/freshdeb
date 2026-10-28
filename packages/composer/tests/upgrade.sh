#!/bin/bash
#
# Upgrade test for Composer: "seed" has the distribution's Composer write a
# global setting and install a project (its composer.lock), "check" makes
# sure ours reads the setting and installs the project from that lock file.

set -euo pipefail

# shellcheck source=packages/composer/tests/lib.sh
source "$(dirname "$0")/lib.sh"
state=/var/tmp/freshdeb-composer

case "${1:?seed or check}" in
    seed)
        composer --version 2>&1 | head -n 1
        rm -rf "$state"
        make_packages "$state/packages"
        make_project "$state/project" "$state/packages"
        composer config --global process-timeout 4321
        (cd "$state/project" && composer install)
        test -f "$state/project/composer.lock"
        check_autoload "$state/project"
        ;;
    check)
        version="$(composer --version 2>&1)"
        [[ "$version" == "Composer version ${FRESHDEB_UPSTREAM_VERSION:?} "* ]] || { echo "$version" >&2; exit 1; }
        [[ "$(composer config --global process-timeout)" == 4321 ]]
        cd "$state/project"
        lock="$(sha256sum composer.lock)"
        rm -rf vendor
        composer install
        # Installed from the distribution's lock file, which stays as it was.
        [[ "$(sha256sum composer.lock)" == "$lock" ]] || { echo "composer.lock changed" >&2; exit 1; }
        check_autoload "$state/project"
        composer config --global --unset process-timeout
        rm -rf "$state"
        ;;
esac
