#!/bin/bash
#
# Smoke test for tindra, run as root in a fresh container right after our
# package was installed non-interactively (nothing preseeded). It then sets
# Tindra up with debconf against the distribution's PostgreSQL, the way an
# unattended install does, and uses it: login, a project, an event from a
# Sentry SDK. Nothing leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 1.2.0

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/tindra/tests/lib.sh
source "$(dirname "$0")/lib.sh"
trap 'stop_tindra; rm -rf "$WORK"' EXIT
email=admin@freshdeb.invalid
password=freshdeb-smoke-password
wants=/etc/systemd/system/multi-user.target.wants/tindra.service

echo "static binary"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
linkage="$(ldd /usr/bin/tindra 2>&1 || true)"
[[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$linkage" >&2; exit 1; }

echo "installed without answers: not configured, not enabled"
id tindra
[[ "$(stat -c '%U:%G %a' "$CONF")" == "root:tindra 640" ]] || { stat "$CONF" >&2; exit 1; }
[[ "$(stat -c '%U:%G %a' /var/lib/tindra)" == "tindra:tindra 750" ]] || { stat /var/lib/tindra >&2; exit 1; }
[[ -z "$(conf_value PUBLIC_URL)" && -z "$(conf_value DATABASE_URL)" ]] || { cat "$CONF" >&2; exit 1; }
[[ "$(conf_value DATA_DIR)" == /var/lib/tindra ]]
[[ ! -e "$wants" ]] || { echo "enabled although not configured" >&2; exit 1; }
# /lib or /usr/lib, depending on the release's debhelper.
unit="$(cat "$(dpkg -L tindra | grep '/systemd/system/tindra.service$')")"
[[ "$unit" == *"TINDRA_ENV_FILE=/etc/tindra/tindra.env"* && "$unit" == *"ExecReload=/bin/kill -HUP"* \
   && "$unit" != *EnvironmentFile* && "$unit" == *"TimeoutStopSec=75"* ]] \
    || { echo "$unit" >&2; exit 1; }
files="$(dpkg -L tindra)"
for file in /usr/share/doc/tindra/examples/env.example /usr/share/bash-completion/completions/tindra; do
    grep -qxF "$file" <<<"$files" || { echo "missing from the package: $file" >&2; exit 1; }
done

echo "debconf sets it up with the local PostgreSQL"
start_postgresql
echo "PostgreSQL $(psql_value 'SHOW server_version')"
reconfigure public_url string "$URL" port string 8080 local_proxy boolean true \
    local_database boolean true admin_email string "$email" admin_name string "Freshdeb Smoke" \
    admin_password password "$password"
[[ "$(conf_value PUBLIC_URL)" == "$URL" ]]
[[ "$(conf_value BIND_ADDR)" == 127.0.0.1:8080 ]]
[[ "$(conf_value TRUSTED_PROXIES)" == "127.0.0.1,::1" ]]
[[ "$(conf_value COOKIE_SECURE)" == false ]]
database="$(conf_value DATABASE_URL)"
[[ "$database" =~ ^postgres://tindra:[0-9a-f]{48}@localhost:[0-9]+/tindra\?sslmode=disable$ ]] \
    || { echo "DATABASE_URL: $database" >&2; exit 1; }
[[ "$(stat -c '%U:%G %a' "$CONF")" == "root:tindra 640" ]]
[[ "$(psql_value "SELECT count(*) FROM pg_roles WHERE rolname = 'tindra'")" == 1 ]]
[[ "$(psql_value "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'tindra'")" == tindra ]]
[[ -e /var/lib/tindra/.debconf-account-created ]] || { echo "no account created" >&2; exit 1; }
stored="$(echo 'GET tindra/admin_password' | debconf-communicate tindra || true)"
[[ "$stored" == "0" || "$stored" == "0 " ]] || { echo "password left in debconf: ${stored:0:2}..." >&2; exit 1; }
[[ -e "$wants" ]] || { echo "not enabled after the setup" >&2; exit 1; }

echo "the server runs, with the web UI"
# An admin's own settings: API access without MFA enrollment for this test.
printf '# freshdeb smoke test\nREQUIRE_MFA=false\n' >>"$CONF"
start_tindra
page="$(curl -fsS "$URL/")"
asset="$(grep -o 'src="/assets/[^"]*\.js"' <<<"$page" | head -n 1 | cut -d'"' -f2 || true)"
[[ -n "$asset" ]] || { echo "no script on the front page" >&2; exit 1; }
size="$(curl -fsS "$URL$asset" | wc -c)"
(( size > 1000 )) || { echo "$asset: $size bytes" >&2; exit 1; }

echo "the account logs in; version and commit"
jar="$WORK/cookies"
login="$(curl -fsS -c "$jar" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$email\",\"password\":\"$password\"}" "$URL/api/auth/login")"
[[ "$login" != *mfa_required\":true* ]] || { echo "$login" >&2; exit 1; }
settings="$(curl -fsS -b "$jar" "$URL/api/settings")"
[[ "$settings" == *"\"version\":\"v${expected}\""* && "$settings" =~ \"commit\":\"[0-9a-f]{40}\" ]] \
    || { echo "$settings" >&2; exit 1; }

echo "an event from a Sentry SDK shows up"
created="$(tindra_cli projects create --name "Freshdeb Smoke" --slug freshdeb-smoke)"
key="$(sed -n 's/^ *public_key: *//p' <<<"$created")"
[[ -n "$key" ]] || { echo "$created" >&2; exit 1; }
payload='{"timestamp":"2026-01-01T00:00:00Z","level":"error","message":"freshdeb smoke event","platform":"other"}'
envelope="$(printf '{"event_id":"%s"}\n{"type":"event","length":%d}\n%s\n' \
    "$(openssl rand -hex 16)" "${#payload}" "$payload")"
curl -fsS -o /dev/null -H "X-Sentry-Auth: Sentry sentry_version=7, sentry_key=$key" \
    --data-binary "$envelope" "$URL/api/1/envelope/"
for _ in $(seq 1 100); do
    issues="$(curl -fsS -b "$jar" "$URL/api/issues")"
    [[ "$issues" == *"freshdeb smoke event"* ]] && break
    sleep 0.2
done
[[ "$issues" == *"freshdeb smoke event"* ]] || { echo "the event isn't there: $issues" >&2; exit 1; }

echo "it read /etc/tindra/tindra.env itself, and reloads it on SIGHUP (the unit's reload)"
grep -q 'configuration file loaded.*/etc/tindra/tindra.env' "$WORK/serve.log" \
    || { tail -n 20 "$WORK/serve.log" >&2; exit 1; }
printf 'PROJECT_LIMIT=7\n' >>"$CONF"
kill -HUP "$(tindra_pid)"
for _ in $(seq 1 50); do
    grep -qE 'limits reloaded.*project_limit("?:|=)7' "$WORK/serve.log" && break
    sleep 0.2
done
grep -qE 'limits reloaded.*project_limit("?:|=)7' "$WORK/serve.log" \
    || { echo "SIGHUP didn't reload the configuration" >&2; tail -n 20 "$WORK/serve.log" >&2; exit 1; }
curl -fsS "$URL/healthz" >/dev/null
stop_tindra

echo "dpkg-reconfigure changes its settings and keeps the admin's"
reconfigure public_url string https://tindra.example.invalid
[[ "$(conf_value PUBLIC_URL)" == https://tindra.example.invalid ]]
[[ "$(conf_value COOKIE_SECURE)" == true ]]
[[ "$(conf_value DATABASE_URL)" == "$database" ]]
if ! grep -qxF 'REQUIRE_MFA=false' "$CONF" || ! grep -qxF '# freshdeb smoke test' "$CONF"; then
    echo "the admin's settings are gone" >&2; cat "$CONF" >&2; exit 1
fi
[[ "$(stat -c '%U:%G %a' "$CONF")" == "root:tindra 640" ]]

echo "smoke test passed"
