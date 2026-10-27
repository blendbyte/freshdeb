# neovim

[Neovim](https://neovim.io) with Debian's packaging (`salsa.debian.org/vim-team/neovim`): `neovim` and `neovim-runtime`, registered as `editor`, `vi`, `vim`, `ex`, `view` and `vimdiff` through alternatives, the same way Debian's package does it.

Upstream supports only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/neovim <codename> main`

## Where the distributions are

| Codename | Ships |
|---|---|
| bookworm | 0.7.2 |
| trixie | 0.10.4 |
| jammy | 0.6.1 |
| noble | 0.9.5 |
| resolute | 0.11.6 |

## Changes to Debian's packaging

- **`overlays/bundled-deps.patch`** (all releases): Debian builds against system libuv, LuaJIT, luv, lpeg, unibilium, utf8proc, tree-sitter and Debian's tree-sitter grammar packages. Older releases lack those or have them too old. We build with neovim's bundled dependencies instead, as upstream's release builds do:
  - `hooks/prefetch-deps.py` downloads everything listed in upstream's `cmake.deps/deps.txt` and verifies each sha256. The build then runs offline, and an unusable proxy makes any stray download fail.
  - The tree-sitter parsers land in `/usr/lib/nvim/parser`, where Debian puts them too.
  - debhelper compat drops from 14 to 13 (jammy), so `${shlibs:Depends}` and `${misc:Depends}` are listed explicitly. The shell completions are installed by `debian/rules`, because `dh-sequence-shell-completions` doesn't exist on every release.
- **Tests:** upstream's functional test suite needs a test framework fetched at build time, so it doesn't run. The smoke test covers LuaJIT, libuv/luv, all six bundled parsers (vimdoc against a real help file: its grammar flags short fragments as errors, in upstream's release build too), editing a file, runtime files and the alternatives. The upgrade test checks that a user's `init.lua` and the alternatives survive the upgrade from the distribution's neovim.

## Dependency updates

The bundled versions are part of each neovim release (`cmake.deps/deps.txt`), so we rebuild when neovim releases, like upstream's AppImage and tarballs. A new dependency in `deps.txt` is fetched automatically; only the skip list in the hook (Windows/macOS-only and optional ones) is maintained by hand.
