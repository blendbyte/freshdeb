# keepalived

[keepalived](https://www.keepalived.org) (VRRP failover and health checking for Linux Virtual Server) packaged from Debian's packaging (`salsa.debian.org/debian/pkg-keepalived`).

Upstream maintains only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/keepalived <codename> main`

Binary package: `keepalived`, with Debian's paths, `/etc/keepalived/keepalived.conf`, `/etc/default/keepalived`, systemd unit and init script. Versions keep Debian's epoch (`1:2.4.3-1+freshdeb~…`).

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 2.2.7 |
| trixie | 2.3.3 |
| jammy | 2.2.4 |
| noble | 2.2.8 |
| resolute | 2.3.4 |

What they miss: 2.4.0 fixes buffer overflows in the HTTP checker and the configuration parser and hardens the parsing of kernel and other untrusted input. New since then: `auth_hmac` (HMAC-SHA256 authenticated VRRP adverts, recommended for unicast), `${_ENV …}` in the configuration, `vrrp_delay_after_boot`. See the [release notes](https://www.keepalived.org/release-notes/).

## Notes

- **Source:** upstream's release tarball from keepalived.org, like Debian (upstream publishes no GitHub releases). The site only lists MD5 sums; the tarball is pinned by sha256 in `package.yaml` (watch.yml pins each new one).
- **Packaging:** Debian's, unchanged: no patches, the same configure options (SNMP, DBus, JSON, BFD, regex, log file, nftables, systemd). Debian's `--enable-sha1` is no option of current keepalived and is ignored, as in Debian's own build. The only overlay (`overlays/news.patch`) adds a NEWS entry, shown by apt-listchanges, on what changed since the distributions' versions: the security fixes, notify scripts and the notify FIFO (symlink targets validated, hardened environment) and DBus methods (denied by default) in 2.4.0, and `auth_hmac`'s signed format changing in 2.4.2, so every node of a virtual router that uses it has to run the same version.
- **Tests:** keepalived has no unit test suite. The test containers get `NET_ADMIN` (`tests.capabilities`) to add the virtual IPs.

## Tests

- **Smoke:** version; `keepalived -t` accepts a valid configuration (with `auth_hmac`) and refuses a broken one; then one VRRP instance on a veth pair, through the init script: it becomes MASTER and adds its virtual IP (the notify script records the state), a failing tracking script moves it to FAULT and removes the IP, recovery brings it back, `service keepalived reload` applies a configuration with a second virtual IP, and stopping removes both. Load balancing (IPVS) isn't tested: the containers have no `ip_vs` kernel module.
- **Upgrade:** the distribution's keepalived (2.2.4 on jammy) runs the same kind of instance (only keywords every version knows) with `DAEMON_ARGS` set in `/etc/default/keepalived`. Ours has to keep the configuration and the settings and become MASTER with the virtual IP.
