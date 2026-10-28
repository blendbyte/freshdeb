#!/bin/bash
#
# Smoke test for Forgejo, run as root in a fresh container right after our
# package was installed. Forgejo runs as git with a configuration like the
# install wizard writes (SQLite, installation locked): an administrator, a
# repository created over the API, a git push and clone over HTTP, and the
# web interface with its assets. Nothing leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 17.0.1

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/forgejo/tests/lib.sh
source "$(dirname "$0")/lib.sh"
work="$(mktemp -d)"
chmod 0755 "$work"
trap 'kill "${FORGEJO_PID:-}" 2>/dev/null || true; rm -rf "$work"' EXIT

echo "binary reports the expected version and build tags"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
version="$(forgejo --version)"
[[ "$version" == "forgejo version ${expected}+gitea-"*" (release name ${expected})"* ]] \
    || { echo "$version" >&2; exit 1; }
[[ "$version" == *": bindata, timetzdata, sqlite, sqlite_unlock_notify" ]] || { echo "$version" >&2; exit 1; }

echo "static binary"
linkage="$(ldd /usr/bin/forgejo 2>&1 || true)"
[[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$linkage" >&2; exit 1; }

echo "user git, directories and the unit (not enabled)"
id git
group="$(id -gn git)"
[[ "$(stat -c '%U:%G %a' "$WORK")" == "git:$group 750" ]] || { stat "$WORK" >&2; exit 1; }
[[ "$(stat -c '%U:%G %a' /etc/forgejo)" == "root:$group 770" ]] || { stat /etc/forgejo >&2; exit 1; }
unit=/usr/lib/systemd/system/forgejo.service
[[ -e "$unit" ]] || unit=/lib/systemd/system/forgejo.service
grep -qx 'ExecStart=/usr/bin/forgejo web --config /etc/forgejo/app.ini' "$unit"
grep -qx 'User=git' "$unit"
[[ ! -e /etc/systemd/system/multi-user.target.wants/forgejo.service ]] \
    || { echo "forgejo.service is enabled" >&2; exit 1; }
[[ ! -e "$CONFIG" ]] || { echo "the package ships $CONFIG" >&2; exit 1; }

echo "database, administrator and the web server"
write_config
# The default paths are built in: no --config or --work-path.
as_git forgejo migrate
as_git forgejo admin user create --admin --username freshdeb --password 'freshdeb-Secret-1' \
    --email freshdeb@example.invalid --must-change-password=false
users="$(as_git forgejo admin user list)"
[[ "$users" == *freshdeb* ]] || { echo "$users" >&2; exit 1; }
start_forgejo
api="$(curl -fsS "$URL/api/v1/version")"
[[ "$api" == *"\"version\":\"${expected}+gitea-"* ]] || { echo "$api" >&2; exit 1; }

echo "repository over the API, git push and clone over HTTP"
curl -fsS -u 'freshdeb:freshdeb-Secret-1' -H 'Content-Type: application/json' \
    -d '{"name": "smoke"}' "$URL/api/v1/user/repos" >/dev/null
git -C "$work" init -q -b main source
echo "freshdeb smoke test" > "$work/source/README.md"
git -C "$work/source" add README.md
git -C "$work/source" -c user.name=freshdeb -c user.email=freshdeb@example.invalid commit -q -m "smoke test"
remote="http://freshdeb:freshdeb-Secret-1@127.0.0.1:3000/freshdeb/smoke.git"
git -C "$work/source" push -q "$remote" main
git clone -q "$remote" "$work/clone"
[[ "$(git -C "$work/clone" rev-parse HEAD)" == "$(git -C "$work/source" rev-parse HEAD)" ]]
cmp "$work/source/README.md" "$work/clone/README.md"
# The web view: Forgejo marks the repository non-empty shortly after the
# first push, not during it.
for _ in $(seq 1 50); do
    file="$(curl -fsS "$URL/freshdeb/smoke/raw/branch/main/README.md" 2>/dev/null)" && break
    sleep 0.2
done
[[ "$file" == "freshdeb smoke test" ]] || { echo "$file" >&2; exit 1; }

echo "web interface and its assets (built in)"
page="$(curl -fsS "$URL/")"
[[ "$page" == *"freshdeb test"* ]] || { echo "front page without APP_NAME" >&2; exit 1; }
grep -oE '/assets/(js|css)/index\.[a-z]+(\?v=[^"]+)?' <<<"$page" | sort -u > "$work/assets"
while read -r asset; do
    size="$(curl -fsS -o /dev/null -w '%{size_download}' "$URL$asset")"
    (( size > 10000 )) || { echo "$asset: $size bytes" >&2; exit 1; }
    echo "  $asset: $size bytes"
done < "$work/assets"
grep -qE '/assets/js/index\.js' <<<"$page" || { echo "no index.js on the front page" >&2; exit 1; }
stop_forgejo
echo "smoke test passed"
