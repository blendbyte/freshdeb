# memcached

[memcached](https://memcached.org) packaged from Debian's packaging (`salsa.debian.org/lamby/pkg-memcached`, where Debian's uploads come from).

| Track | Component | Upstream support |
|---|---|---|
| 1.6 | `v1.6` (alias `v1`) | the only maintained release line |

Repository: `deb [signed-by=…] https://deb.freshdeb.com/memcached <codename> v1`

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
- **Build:** Debian's packaging is used unchanged on every distribution, with TLS, SASL and the proxy enabled. Upstream's test suite runs during the build, as in Debian. That takes about 8 minutes, because Debian runs it serially (the TLS tests are racy in parallel).
- **Upgrades:** `postinst` only creates `/etc/memcached.conf` when it's missing, so an existing configuration is kept. The upgrade test checks this.
- `memcached-tool` needs `liburi-perl`, which Debian only lists under `Suggests`.
