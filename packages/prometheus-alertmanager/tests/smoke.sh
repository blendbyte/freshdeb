#!/bin/bash
#
# Smoke test for Alertmanager, run as root in a fresh container right after
# our package was installed (and again after an upgrade from the distro's
# version). It runs through its init script as the prometheus user; nothing
# leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 0.34.1

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/prometheus-alertmanager/tests/lib.sh
source "$(dirname "$0")/lib.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "binaries report the expected version"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
for binary in prometheus-alertmanager amtool; do
    version="$("$binary" --version 2>&1)"
    [[ "$version" =~ ^[a-z]+", version ${expected} (branch: HEAD, revision: "[0-9a-f]{40}")" ]] \
        || { echo "$version" >&2; exit 1; }
done

echo "static binaries"
for binary in prometheus-alertmanager amtool; do
    linkage="$(ldd "/usr/bin/$binary" 2>&1 || true)"
    [[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$binary: $linkage" >&2; exit 1; }
done

echo "system user, data directory, configuration"
id prometheus
test "$(stat -c %U /var/lib/prometheus/alertmanager)" = prometheus
amtool check-config /etc/prometheus/alertmanager.yml

echo "service starts as the prometheus user"
start_alertmanager
test "$(alertmanager_users)" = prometheus
curl -fsS "$AM/-/healthy" >/dev/null
status="$(curl -fsS "$AM/api/v2/status")"
[[ "$status" == *"\"version\":\"${expected}\""* ]] || { echo "$status" >&2; exit 1; }

echo "web UI is built in"
index="$(curl -fsS "$AM/")"
[[ "$index" == *"<title>Alertmanager</title>"* ]] || { echo "$index" >&2; exit 1; }
script="$(grep -o 'assets/index-[A-Za-z0-9_-]*\.js' <<<"$index" | head -n 1)"
[[ -n "$script" ]] || { echo "no script in the UI page: $index" >&2; exit 1; }
size="$(curl -fsS "$AM/$script" | wc -c)"
(( size > 50000 )) || { echo "UI script $script is only $size bytes" >&2; exit 1; }

echo "alerts through API v2, silences through amtool"
post_alert FreshdebSmoke
wait_alert FreshdebSmoke
silence="$(amtool silence add --comment='freshdeb smoke test' --duration=1h alertname=FreshdebSmoke)"
silences="$(amtool silence query -q)"
grep -qxF "$silence" <<<"$silences" || { echo "silence $silence missing: $silences" >&2; exit 1; }
amtool silence expire "$silence"

echo "amtool reads upstream's /etc/amtool/config.yml, Debian's file first"
mkdir -p /etc/amtool
echo "output: json" > /etc/amtool/config.yml
query="$(amtool silence query --expired)"
[[ "$query" == "["* ]] || { echo "not JSON: $query" >&2; exit 1; }
echo "output: simple" > /etc/prometheus/amtool.yml
query="$(amtool silence query --expired)"
[[ "$query" == "ID "* ]] || { echo "not Debian's file's output: $query" >&2; exit 1; }
rm -rf /etc/amtool /etc/prometheus/amtool.yml

echo "reload through the init script"
reloads() {
    curl -fsS "$AM/metrics" | sed -n 's/^alertmanager_config_last_reload_success_timestamp_seconds //p'
}
before="$(reloads)"
sleep 1
service prometheus-alertmanager reload
for _ in $(seq 1 50); do
    [[ "$(reloads)" != "$before" ]] && break
    sleep 0.2
done
[[ "$(reloads)" != "$before" ]] || { echo "configuration was not reloaded" >&2; show_log; exit 1; }
stop_alertmanager

echo "man pages and bash completion are packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L prometheus-alertmanager)"
for file in /usr/share/man/man1/prometheus-alertmanager.1.gz /usr/share/man/man1/amtool.1.gz \
    /usr/share/bash-completion/completions/amtool; do
    grep -qxF "$file" <<<"$files" || { echo "missing from the package: $file" >&2; exit 1; }
done
echo "smoke test passed"
