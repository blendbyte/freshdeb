# git

[git](https://git-scm.com) with Debian's packaging (`repo.or.cz/git/debian.git`, branch `debian-sid`), unchanged: `git`, `git-man`, `git-doc`, `git-email`, `git-svn`, `git-cvs`, `git-gui`, `gitk`, `gitweb` and `git-all`, the same packages as Debian's.

Upstream supports only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/git <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 2.39.5 |
| trixie | 2.47.3 |
| jammy | 2.34.1 |
| noble | 2.43.0 |
| resolute | 2.53.0 |

On Ubuntu, [`ppa:git-core/ppa`](https://launchpad.net/~git-core/+archive/ubuntu/ppa) also ships current releases. Someone with both gets ours: `1:2.56.0-1+freshdeb~…` sorts above the PPA's `1:2.56.0-0ppa1~…`.

## Notes

- **Source:** upstream's release tarball from kernel.org (`upstream.tarball`), as in Debian. It carries the generated `configure` and `version` files, and its checksums match kernel.org's signed `sha256sums.asc`.
- **No changes to Debian's packaging:** it modifies no upstream files and builds unchanged on every release. That includes git's test suite (about 1,000 test scripts), which gates the build as in Debian.
- **Epoch:** Debian's versions carry an epoch (`1:2.39.5-…`), so ours do too (`epoch: 1`).
- **Upgrade test:** a repository with a stash, a worktree and user configuration (including an alias) is created with the distribution's git, then checked, extended and maintained (`git maintenance run`) with ours.
