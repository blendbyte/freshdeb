# composer

[Composer](https://getcomposer.org), the dependency manager for PHP, as `composer.phar` built from source the way upstream builds it, using Debian's packaging (`salsa.debian.org/php-team/pear/composer`) for the rest: `/usr/bin/composer`, bash completion, the man page and the documentation. The package keeps Debian's name, so it replaces the distribution's on upgrade.

Upstream supports its latest release, so there is one track, `main`, which follows every release (release candidates are skipped). Its 2.2 LTS line is only for PHP older than 7.2.5; every distribution here has PHP 8.1 or newer.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/composer <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 2.5.5 |
| trixie | 2.8.8 |
| jammy | 2.2.6 |
| noble | 2.7.1 |
| resolute | 2.9.5 |

## How it's built

Debian installs Composer's PHP sources in `/usr/share/php` against ~20 Debian-packaged libraries (`php-composer-*`, `php-symfony-*`, `php-react-promise`, …), which the older releases don't have in the versions Composer needs. We build the phar instead, offline, from the release's source:

- **Dependencies:** `hooks/vendor-deps.py` (the prepare hook) takes the runtime dependencies from upstream's `composer.lock` (not the dev ones) and fetches each from its git repository by the locked commit, which git checks by content; `git archive` extracts it into `vendor/`, leaving out what the package marks `export-ignore`, the same files as the dist zips upstream's own build installs. It also records the release's commit date.
- **Autoloader:** `debian/freshdeb-bootstrap.php` records the dependencies in `vendor/composer/installed.json` and writes a temporary autoloader, enough to run Composer from its own source; that Composer then runs `composer install --no-dev`, which installs nothing (everything is there) and writes the real autoloader, as upstream's `bin/compile` does. No prebuilt Composer is involved.
- **Phar:** upstream's `bin/compile` builds `composer.phar` with the version and the release's commit date, so `composer --version` reads like upstream's (`Composer version 2.10.3 2026-08-27 13:34:23`); its file timestamps are the release date.

The result is reproducible: the phar is the same on every distribution, and without our self-update patch it is byte-for-byte upstream's released `composer.phar` (checked for 2.10.3: sha256 `7a2d379d…c8d6` both). With it, the two files that patch changes are the only difference.

## Changes to Debian's packaging

- **`overlays/phar-build.patch`** (all releases): the build above instead of Debian's (`dh_phpcomposer`, `phpab`); `/usr/bin/composer` is the phar. Build-dependencies are `php-cli`, `help2man` and bash-completion's debhelper sequence; the package depends on `php-cli` only, with Debian's Recommends (`git`, `unzip`, `php-curl`, `php-intl`, `php-mbstring`, `php-zip`, `xz-utils`) and Suggests. The bash completion (`composer completion bash`) and the man page (`help2man`) are generated as Debian does. Debian's autopkgtest runs upstream's PHPUnit suite against its `/usr/share/php` layout and is dropped, as are its `pkg-php-tools` mappings. A NEWS entry explains the changes.
- **`patches/main/`:** ours. `0001` lets `bin/compile` take the version and date from the environment instead of a git checkout. `0002` disables `self-update`, which would replace the packaged phar: two one-line changes make `self-update` and `diagnose` behave as upstream's code does for an installation that isn't a phar ("installed as a system package on your OS"; `diagnose` skips the self-update signature keys and the newest-version check). Debian's patches adapt Composer to its unbundled layout and its test setup, and are dropped.
- Of Composer's PHP classes, only its class loader is still installed in `/usr/share/php` (`Composer/Autoload/ClassLoader.php`): jammy's `civicrm-common` depends on `composer` and loads it from there. Software that used other Composer classes from there should require `composer/composer` itself. The distributions' `php-composer-*` libraries Debian's package depended on can be autoremoved.

## Tests

Upstream's PHPUnit suite needs its dev dependencies (PHPUnit), so the build only checks that the phar runs, reports the release and refuses `self-update`.

- **Smoke:** version and release date, `/usr/bin/composer` is the phar, `self-update` refuses (upstream's "system package" message), man page and bash completion, Composer's class loader loads from the include path, then a project that requires a package from a path repository and one from a local git repository (Packagist disabled): `validate`, `install`, the classes load through `vendor/autoload.php`, `show`, `dump-autoload --optimize` and a global `config` setting.
- **Upgrade:** the distribution's Composer (2.2.6 on jammy) sets a global setting and installs that project, writing its `composer.lock`. Ours has to read the setting and install the project again from the unchanged lock file.
