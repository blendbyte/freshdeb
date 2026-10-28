# valkey

[Valkey](https://valkey.io) packaged from Debian's packaging (`salsa.debian.org/debian/valkey`), for every release line upstream supports.

| Track | Component | Upstream support ends | Patch set from |
|---|---|---|---|
| 9.1 | `v9.1` (alias `v9`) | 2031-05-19 (security) | Debian 9.1.2-1 |
| 9.0 | `v9.0` | 2028-10-21 | Ubuntu 9.0.4-0ubuntu0.2 (Debian never packaged 9.0) |
| 8.1 | `v8.1` (alias `v8`) | 2030-03-31 (security) | Debian 8.1.4+dfsg1-2 |
| 8.0 | `v8.0` | 2027-09-15 | Debian 8.0.2+dfsg1-1 |

Repository: `deb [signed-by=…] https://deb.freshdeb.com/valkey <codename> v9`

Binary packages: `valkey-server`, `valkey-sentinel`, `valkey-tools`, with the same names, paths, user and config as Debian's.

## Where the distributions are

| Codename | Ships | Effect |
|---|---|---|
| bookworm | – | all tracks |
| trixie | 8.1.1 | 8.0 skipped by the version guard |
| jammy | – | all tracks |
| noble | 7.2.13 | all tracks |
| resolute | 9.0.4 | 8.0 and 8.1 skipped |

## Changes to Debian's packaging

- **`patches/<track>/`** replaces `debian/patches` per track, because Debian's patches are made for one upstream line. Of them we keep the configuration defaults and `CPPFLAGS` for upstream's makefiles. CVE backports were dropped, since upstream's later patch releases include them, and so were the kFreeBSD and Hurd fixes and the support for building against the system's jemalloc.
- **`overlays/bundled-jemalloc.patch`** (all releases): valkey is built with upstream's bundled jemalloc (`deps/jemalloc`) instead of Debian's `libjemalloc`. Upstream builds and tests with it, and only with it is active defragmentation (`activedefrag yes`) available; Debian's packages refuse it.
- **`overlays/legacy-sysusers.patch`** (jammy): debhelper 13.6 has no `dh_installsysusers`, so the `valkey` user and its directories are created in `valkey-tools.postinst`, as Debian did before 9.1.
- **`overlays/sysusers-no-lock-modifier.patch`** (bookworm, noble): the `u!` sysusers modifier needs systemd 256. It becomes `u`.
- **`build.options: [nocheck]`**: Debian runs the upstream test suite with `|| true` (it never fails the build) and it takes 40+ minutes. Ours is gated by `tests/` and Debian's autopkgtests on the installed packages instead.

## New release line

watch.yml opens a PR adding the track with `patches/<new>` copied from the newest track and the alias moved. Before merging, check Debian's packaging for the new line (salsa, or Ubuntu if Debian skipped it), refresh the patch set if needed, and set `eol` from [upstream's table](https://valkey.io/topics/releases/).
