# postsrsd

[PostSRSd](https://github.com/roehling/postsrsd) (Sender Rewriting Scheme for Postfix, so forwarded mail passes SPF) packaged from Debian's packaging (`salsa.debian.org/debian/postsrsd`).

Upstream maintains only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/postsrsd <codename> main`

Binary package: `postsrsd`, with Debian's paths: `/etc/postsrsd.conf`, the secret in `/etc/postsrsd.secret`, the socketmap socket `/var/spool/postfix/srs`, chroot `/var/lib/postsrsd`, systemd unit, init script and the debconf question for the local domain.

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 1.10 |
| trixie | 1.10 (2.4.1 in trixie-backports) |
| jammy | 1.10 (not built here, see below) |
| noble | 1.10 |
| resolute | 2.0.11 |

**Not for jammy:** postsrsd 2.x needs CMake 3.24 or newer, and jammy has 3.22. Its standard support ends in April 2027, so there is no build for it (`codenames:` in `package.yaml`).

## Upgrading from 1.x

postsrsd 2.x reads `/etc/postsrsd.conf` instead of `/etc/default/postsrsd`, and answers Postfix through `socketmap:` tables instead of `tcp:` tables.

- **Settings are migrated:** when upgrading from 1.x, the package carries `SRS_DOMAIN`, `SRS_EXCLUDE_DOMAINS`, `SRS_SECRET`, `SRS_SEPARATOR`, `SRS_HASHLENGTH`, `SRS_HASHMIN`, `RUN_AS`, `CHROOT` and `-A` in `SRS_EXTRA_OPTIONS` into the newly created `/etc/postsrsd.conf`, as upstream's migration notes describe (the SRS domain becomes `srs-domain` and, with the excluded domains, the local `domains`; a leading dot still means subdomains only). The secret file stays, so addresses rewritten by 1.x still reverse. An existing `/etc/postsrsd.conf` is never touched.
- **Postfix needs a change**, which the package can't make for you. Replace the `tcp:` lookups (ports 10001 and 10002) in `/etc/postfix/main.cf` with

  ```
  sender_canonical_maps = socketmap:unix:srs:forward
  sender_canonical_classes = envelope_sender
  recipient_canonical_maps = socketmap:unix:srs:reverse
  recipient_canonical_classes = envelope_recipient, header_recipient
  ```

  and reload Postfix. Until then, Postfix's lookups fail and it defers mail (and retries it later). Alternatively, postsrsd works as a milter (`milter` in `postsrsd.conf`, `smtpd_milters` in `main.cf`).

The package's NEWS entry (shown by apt-listchanges) says the same.

## Changes to Debian's packaging

- **`overlays/upgrade-from-1.x.patch`** (all releases): the settings migration above in `postinst`, and the NEWS entry. Debian's NEWS only describes the migration.
- **`overlays/init-daemonize.patch`** (all releases): Debian's init script, which systems without systemd use, started postsrsd 2.x without `-D`, so it stayed in the foreground and `service postsrsd start` never came back. It now daemonizes it with a pid file (`/run/postsrsd.pid`) and stops it by that. With systemd this doesn't apply.
- **`overlays/bookworm-systemd-pc.patch`** (bookworm): `systemd-dev` only exists from systemd 253; bookworm's `systemd` package ships `systemd.pc` itself.
- Otherwise Debian's packaging as is: no patches, upstream's git tag built with CMake and upstream's defaults (no SQLite or Redis envelope database, no seccomp), upstream's test suite during the build. (Debian's rules still call `dh_apparmor`, but neither upstream 2.x nor Debian has a profile any more, so the package has none.)

## Tests

- **Build:** upstream's unit and blackbox tests (`ctest`).
- **Smoke:** version, the secret root-owned and `0600`, systemd unit and man page packaged, the service starts through the init script (its main process stays root to re-read secrets and domains on reload; the worker serving each connection runs as `postsrsd` in the chroot), forward rewriting to an `SRS0` address and its reverse over the socketmap socket, local domains and subdomains left alone, a tampered address refused.
- **Upgrade:** the distribution's 1.10 (bookworm, noble, trixie) gets settings in `/etc/default/postsrsd` (SRS domain, excluded domains with a leading dot, `+` separator) and rewrites an address over `tcp_table`. Ours has to migrate the settings into `/etc/postsrsd.conf`, keep the secret, reverse that address over socketmap and rewrite new ones the same way. From resolute's 2.0.11 (started directly: its init script has the bug fixed above), the configuration has to stay as it is and the address it made has to reverse.
