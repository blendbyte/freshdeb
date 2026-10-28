#!/bin/bash
#
# Helpers for the Composer tests (sourced, not run): two local packages, one
# from a path repository and one from a git repository with a tag, and a
# project that requires both. Everything is local: the project disables
# Packagist. They work with the distributions' versions as well as with ours.

# Composer refuses root unless told otherwise; the tests run as root. The
# project has no version of its own (no git checkout to tell).
export COMPOSER_ALLOW_SUPERUSER=1 COMPOSER_NO_INTERACTION=1 COMPOSER_ROOT_VERSION=1.0.0

# git for the vcs repository; the test containers don't have it.
command -v git >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git >/dev/null

# make_packages <dir>: <dir>/greeting (path repository) and <dir>/vcs-lib.git
# (git repository, tagged 1.0.0).
make_packages() {
    local dir="$1"
    mkdir -p "$dir/greeting/src" "$dir/vcs-lib/src"
    cat > "$dir/greeting/composer.json" <<'JSON'
{
    "name": "freshdeb/greeting",
    "version": "1.2.0",
    "autoload": {"psr-4": {"Freshdeb\\Greeting\\": "src/"}}
}
JSON
    cat > "$dir/greeting/src/Hello.php" <<'PHP'
<?php
namespace Freshdeb\Greeting;

class Hello
{
    public static function world(): string
    {
        return 'hello from a path repository';
    }
}
PHP
    cat > "$dir/vcs-lib/composer.json" <<'JSON'
{
    "name": "freshdeb/vcs-lib",
    "autoload": {"psr-4": {"Freshdeb\\VcsLib\\": "src/"}}
}
JSON
    cat > "$dir/vcs-lib/src/Answer.php" <<'PHP'
<?php
namespace Freshdeb\VcsLib;

class Answer
{
    public static function get(): int
    {
        return 42;
    }
}
PHP
    git -C "$dir/vcs-lib" init -q
    git -C "$dir/vcs-lib" add -A
    git -C "$dir/vcs-lib" -c user.name=freshdeb -c user.email=test@freshdeb.invalid commit -q -m "vcs-lib 1.0.0"
    git -C "$dir/vcs-lib" tag 1.0.0
}

# make_project <dir> <packages-dir>: a project requiring both packages.
make_project() {
    local dir="$1" packages="$2"
    mkdir -p "$dir"
    cat > "$dir/composer.json" <<JSON
{
    "name": "freshdeb/project",
    "description": "freshdeb's Composer test project",
    "license": "MIT",
    "require": {
        "freshdeb/greeting": "^1.2",
        "freshdeb/vcs-lib": "^1.0"
    },
    "repositories": [
        {"type": "path", "url": "$packages/greeting"},
        {"type": "vcs", "url": "$packages/vcs-lib"},
        {"packagist.org": false}
    ]
}
JSON
}

# check_autoload <project>: the installed packages load through vendor/autoload.php.
check_autoload() {
    local out
    out="$(cd "$1" && php -r 'require "vendor/autoload.php"; echo Freshdeb\Greeting\Hello::world(), " ", Freshdeb\VcsLib\Answer::get(), "\n";')"
    [[ "$out" == "hello from a path repository 42" ]] || { echo "autoload: $out" >&2; return 1; }
}
