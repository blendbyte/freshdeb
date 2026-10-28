#!/bin/bash
#
# Smoke test for Composer, run as root in a fresh container right after our
# package was installed (and again after an upgrade from the distro's
# version). Nothing leaves the container: the packages come from local
# repositories.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 2.10.3

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/composer/tests/lib.sh
source "$(dirname "$0")/lib.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "composer reports the expected version and release date"
version="$(composer --version 2>&1)"
[[ "${version%%$'\n'*}" =~ ^"Composer version ${expected} "[0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}:[0-9]{2}$ ]] \
    || { echo "$version" >&2; exit 1; }

echo "/usr/bin/composer is the phar"
head -c 2000 /usr/bin/composer | grep -aqF "Phar::mapPhar('composer.phar');"

echo "no self-update: a system package"
if update="$(composer self-update 2>&1)"; then
    echo "self-update succeeded: $update" >&2; exit 1
fi
[[ "$update" == *"installed as a system package"* ]] || { echo "$update" >&2; exit 1; }

echo "man page and bash completion are packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L composer)"
for file in /usr/share/man/man1/composer.1.gz /usr/share/bash-completion/completions/composer; do
    grep -qxF "$file" <<<"$files" || { echo "missing from the package: $file" >&2; exit 1; }
done

echo "Composer's class loader is still in /usr/share/php"
# As jammy's civicrm-common loads it: from the include path.
loader="$(php -r 'require_once "Composer/Autoload/ClassLoader.php"; echo get_class(new Composer\Autoload\ClassLoader());')"
[[ "$loader" == 'Composer\Autoload\ClassLoader' ]] || { echo "$loader" >&2; exit 1; }

echo "install from a path and a git repository, autoload"
make_packages "$work/packages"
make_project "$work/project" "$work/packages"
cd "$work/project"
composer validate --no-check-publish --no-check-lock
composer install
check_autoload "$work/project"
shown="$(composer show)"
[[ "$shown" == *"freshdeb/greeting"*"1.2.0"* && "$shown" == *"freshdeb/vcs-lib"*"1.0.0"* ]] \
    || { echo "$shown" >&2; exit 1; }
composer validate --no-check-publish

echo "optimized autoloader"
composer dump-autoload --optimize
grep -qF 'Freshdeb\\Greeting\\Hello' vendor/composer/autoload_classmap.php
check_autoload "$work/project"

echo "global configuration"
composer config --global process-timeout 1234
[[ "$(composer config --global process-timeout)" == 1234 ]]
composer config --global --unset process-timeout
echo "smoke test passed"
