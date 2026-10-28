# memcached

[memcached](https://memcached.org) packaged from Debian's packaging (`salsa.debian.org/lamby/pkg-memcached`, where Debian's uploads come from).

Upstream maintains only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/memcached <codename> main`

Binary package: `memcached`, with the same paths, `memcache` user, `/etc/memcached.conf` and services as Debian's.

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 1.6.18 |
| trixie | 1.6.38 |
| jammy | 1.6.14 |
| noble | 1.6.24 |
| resolute | 1.6.40 |

## Notes

- **Source:** upstream's release tarball (`upstream.tarball`), like Debian. The git tag has no `configure` script and takes its version from `git describe`.
- **Build:** Debian's packaging on every distribution, with TLS, SASL and the proxy enabled. One change (`overlays/bundled-lua.patch` and `patches/main/`, which is Debian's patch set without `0006-Don-t-use-embedded-Lua.patch`): the proxy is built with the Lua that upstream ships in the release tarball (`vendor/lua`), as upstream builds it, instead of the distribution's `lua5.4`. Upstream's test suite runs during the build, as in Debian. That takes about 8 minutes, because Debian runs it serially (the TLS tests are racy in parallel).
- **Tests:** the smoke test also runs the proxy: a backend memcached, and a proxy in front of it with a configuration in upstream's style; a value set through the proxy is on the backend.
- **Upgrades:** `postinst` only creates `/etc/memcached.conf` when it's missing, so an existing configuration is kept. The upgrade test checks this.
- `memcached-tool` needs `liburi-perl`, which Debian only lists under `Suggests`.
