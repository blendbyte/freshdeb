# freshdeb

Current upstream releases as Debian and Ubuntu packages, for software that doesn't run its own APT repository and relies on the distributions, which lag behind. Built from Debian's own packaging, so packages install and upgrade exactly like the distribution's. Supports Debian 12/13 and Ubuntu 22.04/24.04/26.04 on amd64 and arm64.

Available packages, release lines, and install instructions: [freshdeb.com](https://www.freshdeb.com)

## Contributing

PRs are welcome. Bug fixes, packaging fixes, build improvements, version bumps, and docs are all fair game. Open an issue first if you're unsure.

**Adding a package** comes with a deal: you maintain it. See [CONTRIBUTING.md](CONTRIBUTING.md) for what that means and [docs/packages.md](docs/packages.md) for the package format.

## Security

Found something? Report it privately through [GitHub's vulnerability reporting form](https://github.com/blendbyte/freshdeb/security/advisories/new) or email `security@blendbyte.com`. Full policy, the signing key, and supply chain details are in [SECURITY.md](SECURITY.md).

## License

Build scripts, CI, and docs in this repo are [BSD-2-Clause](LICENSE). Packaging in `packages/*/debian` comes from Debian and keeps its license, as does the packaged software. See each package's `debian/copyright`.

## Maintained by Blendbyte

<br>

<p align="center">
  <a href="https://www.blendbyte.com">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://www.blendbyte.com/logo_horizontal_light.png">
      <img src="https://www.blendbyte.com/logo_horizontal.png" alt="Blendbyte" width="360">
    </picture>
  </a>
</p>

<p align="center">
  <strong><a href="https://www.blendbyte.com">Blendbyte</a></strong> builds cloud infrastructure, web apps, and developer tools.<br>
  We've been shipping software to production for 20+ years.
</p>

<p align="center">
  These packages run in our own stack, which is why we keep them maintained.<br>
  Issues and PRs get read. Good ones get merged.
</p>

<br>

<p align="center">
  <a href="https://www.blendbyte.com">blendbyte.com</a> · <a href="mailto:hello@blendbyte.com">hello@blendbyte.com</a>
</p>
