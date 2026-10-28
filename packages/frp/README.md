# frp

[frp](https://github.com/fatedier/frp), a fast reverse proxy that exposes local servers behind a NAT or firewall, built from source with a pinned Go toolchain. Two packages: `frps`, the server, and `frpc`, the client, each with its web dashboard, a configuration in `/etc/frp`, systemd units and a man page.

Upstream supports only its latest release, so there is one track, `main`, which follows every release.

Repository: `deb [signed-by=…] https://deb.freshdeb.com/frp <codename> main`

## Where the distributions are

None of them ship frp (a Debian ITP from 2023 never got past a draft). The packaging is our own, in Debian's style (see [Own packaging](../../docs/packages.md#own-packaging-when-debian-has-none)).

## How it's built

- **Binaries:** as upstream's Makefile builds them: `CGO_ENABLED=0`, `-trimpath`, `-ldflags "-s -w"`, build tags `frps` and `frpc`, with the pinned Go toolchain from `config.yaml` and vendored dependencies verified against `go.sum` and sum.golang.org (see [Go programs](../../docs/packages.md#go-programs)). Upstream's unit tests run during the build, as its `make test` runs them; its end-to-end suite doesn't.
- **Dashboards:** the frps dashboard and the frpc admin UI are Vue apps in `web/` that the binaries embed. They aren't in the git tag; upstream's release workflow builds them with Node 22 and npm, and without them its Makefile builds `-tags noweb`, without dashboards. The prepare hook (`hooks/build-web.py`) does what upstream's workflow does: Node from nodejs.org (pinned version, sha256 per architecture), `npm ci` against upstream's `web/package-lock.json` (every package must come from the npm registry with a sha512 integrity hash, which npm checks), and each dashboard's `npm run build`. Only the built `dist` directories stay in the source; the build runs offline.
- **Man pages** come from `--help` (help2man), **shell completions** (bash, zsh, fish) from the binaries' `completion` command.

## Packaging

- **Configuration:** `/etc/frp/frps.toml` and `/etc/frp/frpc.toml` are upstream's minimal examples (conffiles), readable only by root and the `frp` group: they hold the auth token. The full references are in `/usr/share/doc/frps/` and `/usr/share/doc/frpc/`.
- **Services:** `frps.service`, `frpc.service`, and `frps@<name>.service` / `frpc@<name>.service` for `/etc/frp/<name>.toml`, modeled on the units upstream shipped until 2022. They run as the `frp` system user, with systemd's sandboxing (read-only system, no new privileges, no capabilities); frps may bind ports below 1024 (`CAP_NET_BIND_SERVICE`) for HTTP(S) virtual hosts; frpc can read home directories (plugins such as `static_file`) and shares `/tmp` (local services' sockets). `systemctl reload frpc` uses frpc's admin API, so it needs `webServer.port`. Log files, if configured, go to `/var/log/frp`.
- **Not enabled or started on install**, unlike most Debian services: upstream's example frps configuration has no token, so any client could use it, and frpc's points at localhost. Configure, check with `frps verify -c /etc/frp/frps.toml` (or `frpc verify`), then `systemctl enable --now frps`. Enabled services restart after upgrades. `README.Debian` in each package says the same.

## Tests

- **Smoke:** versions and static linking, man pages and completions, the `frp` user and the configuration files' owner and mode, `verify` on the shipped configurations, the units installed and not enabled; then frps and frpc run as `frp` with a token on localhost: a TCP proxy reaches a local web server through frps, the frps dashboard serves its page and script and `/api/serverinfo` (and refuses requests without credentials), the frpc admin UI serves its page and script and `/api/status`, and `frpc reload` works.
- **Upgrade:** none; no distribution has frp to upgrade from.
