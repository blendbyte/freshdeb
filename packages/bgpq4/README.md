# bgpq4

[bgpq4](https://github.com/bgp/bgpq4) built from source, using Debian's packaging (`salsa.debian.org/debian/bgpq4`): `/usr/bin/bgpq4` and its man page. The package keeps Debian's name, so it replaces the distribution's on upgrade.

Upstream maintains only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/bgpq4 <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 1.9 |
| trixie | 1.12 |
| jammy | 1.4 |
| noble | 1.12 |
| resolute | 1.12 |

What the distributions' versions miss: Nokia SR Linux output (`-n2`) and `-3` accepted as a no-op for bgpq3 compatibility (1.10), AS 23456 refused as an origin unless `-p` is given (1.11), a fix to the MikroTik output (1.12), a fix to the comma placement in IOS XR as-path sets (1.13), `TCP_NODELAY` on the IRR connection (1.14) and a fix for `-w` (1.16). Jammy's 1.4 also misses Junos `as-path-origins` (1.5), several parsing fixes, `SOURCE::` syntax (1.7) and `-p` for private ASNs (1.8).

## Changes to Debian's packaging

- **`overlays/offline-tests.patch`** (all releases): no network during the build and the tests. Upstream's `make check` runs `bgpq4 -v` and then queries NTT's IRR server (ignoring failures), and Debian's autopkgtest queries it too; the build runs only `bgpq4 -v`, and the autopkgtest is dropped. The smoke test covers the queries against a local IRR server instead.
- Like Debian, the build uses the git tag; debhelper runs `autoreconf` to create the `configure` script.

## Tests

- **Smoke:** version and man page, then bgpq4 against a small IRR server on localhost that speaks the IRRd protocol with fixed data (an as-set with two ASNs and their route and route6 objects): Cisco prefix lists for an AS and, aggregated, for the as-set with the server's source list, a Juniper IPv6 prefix list, a BIRD prefix set, JSON, a Nokia SR Linux prefix set and a Cisco AS-path access list, each compared with the exact output.
- **Upgrade:** there's no state to carry over; the upgrade run installs the distribution's version, upgrades to ours and runs the smoke test.
