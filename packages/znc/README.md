# znc

[ZNC](https://znc.in) with Debian's packaging (source package `znc` 1.10.3-1), unchanged: `znc`, `znc-dev`, `znc-perl`, `znc-python`, `znc-tcl`, and the third-party modules `znc-push` and `znc-backlog` that Debian bundles.

Upstream supports only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/znc <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 1.8.2 |
| trixie | 1.9.1 |
| jammy | 1.8.2 |
| noble | 1.9.0 |
| resolute | 1.10.1 |

## Notes

- **Source:** upstream's release tarball from znc.in, identical to Debian's orig tarball.
- **Packaging reference:** Debian maintains this packaging without a public VCS, so `packaging.ref` is the sha256 of the imported `.debian.tar.xz`, not a commit.
- **No changes to Debian's packaging:** it patches nothing upstream and builds unchanged on every release.
- **`znc-push` and `znc-backlog`:** Debian keeps their sources in `debian/contrib` and versions them from ZNC's version (`1.0.0+git20190521.78d0385+<znc version>`, `0.20180824+<znc version>`). The release accepts binaries whose version equals the package's or ends in `+<package version>`. They sort above the distributions' builds, so `apt upgrade` moves them along with ZNC.
- **`znc --version`** shows `1.10.3+deb1+freshdeb~…`. Debian's rules append `+deb<revision>` to the version string, and we leave that as it is.
- **Tests:**
  - **Smoke:** ZNC runs as `_znc` from a fresh configuration, then the test logs in over IRC, checks the version via `*status`, and loads `modpython`, `modperl`, `push` and `backlog`.
  - **Upgrade:** a configuration written for the distribution's ZNC (1.8.2 on bookworm and jammy) has to start unchanged with ours and accept the same login.
