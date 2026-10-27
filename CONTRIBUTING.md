# Contributing

Bug fixes, packaging fixes, build improvements and docs are all welcome as pull requests. Open an issue first if you're unsure.

## Adding a package

freshdeb is for software that **doesn't publish its own APT repository** and where the Debian/Ubuntu versions are meaningfully behind upstream. If upstream already has a repository, use that.

The deal: **you maintain it.** That means:

- reviewing watch.yml's version bump PRs within two weeks
- looking at build failures for your package within a week
- watching upstream security advisories and making sure fixed releases land
- adding new release lines (tracks) when upstream starts them, and setting their end-of-life dates

If you want out, open a PR removing yourself. A package without a maintainer for 30 days stops being built. Published packages stay in the repository, so nothing breaks for existing users.

To add one:

1. Open a "New package" issue so we can agree it fits.
2. Add `packages/<name>/` following [docs/packages.md](docs/packages.md), based on Debian's packaging.
3. Make sure every CI job passes (each track × 5 codenames × amd64/arm64), including the upgrade test from the distribution's own version.
4. List yourself under `maintainers`. We add the package to the list on [freshdeb.com](https://www.freshdeb.com) when it ships.

"Please add X" issues without a maintainer get closed. Nothing personal, it's how a small project stays manageable.

## Changing a package

- Packaging changes (debian/, patches, overlays, build options) need a `revision` bump on every affected track. CI tells you if you forget.
- Keep `debian/` identical to what was imported from Debian. Changes go into `patches/<track>/` or `overlays/`, with a comment in package.yaml saying why.
- Never delete a track. Let its `eol` date pass instead.
