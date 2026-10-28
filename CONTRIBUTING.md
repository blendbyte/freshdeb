# Contributing

Bug fixes, packaging fixes, build improvements and docs are welcome as pull requests. Open an issue first if you're unsure.

## Adding a package

freshdeb is for software that **doesn't publish its own APT repository** and where the Debian/Ubuntu versions are meaningfully behind upstream. If upstream already has a repository, use that.

1. Open a "New package" issue so we can agree it fits.
2. Add `packages/<name>/` following [docs/packages.md](docs/packages.md), based on Debian's packaging when there is one.
3. Build and verify it locally (`scripts/build.sh` + `scripts/verify.sh`, see [docs/packages.md](docs/packages.md#locally)). Pull requests only run checks; packages are built after merge. Maintainers can run the full build on a branch by dispatching `ci.yml` on it.

## Changing a package

- Packaging changes (debian/, patches, overlays, build options) need a `revision` bump on every affected track. CI tells you if you forget.
- Keep imported `debian/` identical to Debian's. Changes go into `patches/<track>/` or `overlays/`, with a one-line comment in package.yaml saying why.
- Never delete a track. Let its `eol` date pass instead.
