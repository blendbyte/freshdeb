#!/bin/bash
#
# Smoke test for headscale, run as root in a fresh container right after our
# package was installed (and again after an upgrade from upstream's 0.28
# package). headscale runs as its user, as the systemd unit would run it.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 0.29.6

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/headscale/tests/lib.sh
source "$(dirname "$0")/lib.sh"
guard="$(mktemp -d)"
trap 'rm -rf "$guard"' EXIT

echo "version, commit and build time, as upstream's release binaries report them"
version="$(headscale version)"
[[ "$version" == "headscale version v${expected}"$'\n'* ]] || { echo "$version" >&2; exit 1; }
[[ "$version" =~ $'\n'"commit: "[0-9a-f]{40}$'\n' ]] || { echo "$version" >&2; exit 1; }
[[ "$version" =~ $'\n'"build time: "[0-9]{4}-[0-9]{2}-[0-9]{2}T ]] || { echo "$version" >&2; exit 1; }

echo "static binary"
linkage="$(ldd /usr/bin/headscale 2>&1 || true)"
[[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$linkage" >&2; exit 1; }

echo "laid out like upstream's package"
id headscale
[[ "$(getent passwd headscale | cut -d: -f6,7)" == "/var/lib/headscale:/usr/sbin/nologin" ]]
test -d /var/lib/headscale
files="$(dpkg -L headscale)"
for file in /etc/headscale/config.yaml /usr/share/doc/headscale/examples/config-example.yaml \
        /usr/share/bash-completion/completions/headscale; do
    grep -qxF "$file" <<<"$files" || { echo "missing from the package: $file" >&2; exit 1; }
done
grep -qE '^/(usr/)?lib/systemd/system/headscale\.service$' <<<"$files" \
    || { echo "missing from the package: headscale.service" >&2; exit 1; }
conffiles="$(dpkg-query -W -f='${Conffiles}' headscale)"
[[ "$conffiles" == *" /etc/headscale/config.yaml "* ]] || { echo "$conffiles" >&2; exit 1; }

echo "the service is enabled, as upstream's package enables it"
test -L /etc/systemd/system/multi-user.target.wants/headscale.service

echo "configtest passes, the server starts and answers"
configure_offline
prepare_dirs
runuser -u headscale -- headscale configtest >/dev/null
start_headscale
health="$(curl -fsS http://127.0.0.1:8080/health)"
[[ "$health" == *pass* ]] || { echo "$health" >&2; exit 1; }

echo "users, pre-auth keys and nodes over the unix socket"
headscale users create smoke-user >/dev/null
id="$(user_id smoke-user)"
[[ -n "$id" ]] || { echo "smoke-user not created" >&2; exit 1; }
key="$(headscale preauthkeys create --user "$id" --reusable --expiration 1h -o json | jq -r .key)"
[[ -n "$key" && "$key" != null ]] || { echo "no pre-auth key" >&2; exit 1; }
[[ "$(headscale preauthkeys list -o json | jq 'length')" -ge 1 ]]
headscale nodes list -o json >/dev/null
stop_headscale

echo "the database version check is on: a database from two minors back is refused"
# Without a version (the "dev" of a build outside git) headscale would skip it.
minor="${expected#*.}"; minor="${minor%%.*}"
old="v${expected%%.*}.$(( minor - 2 )).0"
cp "$CONFIG" "$guard/config.yaml"
sed -i "s|^    path: /var/lib/headscale/db.sqlite$|    path: $guard/db.sqlite|" "$guard/config.yaml"
sqlite3 "$guard/db.sqlite" "CREATE TABLE database_versions (id integer PRIMARY KEY, version text, updated_at datetime);
    INSERT INTO database_versions VALUES (1, '$old', '2026-01-01 00:00:00');"
chown -R headscale:headscale "$guard"
if refused="$(runuser -u headscale -- timeout 30 headscale serve -c "$guard/config.yaml" 2>&1)"; then
    echo "headscale started on a database from $old" >&2; exit 1
fi
[[ "$refused" == *"upgrading more than one minor version at a time is not supported"* ]] \
    || { echo "$refused" >&2; exit 1; }

echo "smoke test passed"
