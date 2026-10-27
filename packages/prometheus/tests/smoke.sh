#!/bin/bash
#
# Smoke test for Prometheus, run as root in a fresh container right after
# our packages were installed (and again after an upgrade from the distro's
# version). Prometheus runs through its init script as the prometheus user
# and scrapes itself; nothing leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 3.15.1

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/prometheus/tests/lib.sh
source "$(dirname "$0")/lib.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "binaries report the expected version"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
version="$(prometheus --version 2>&1)"
[[ "$version" =~ ^"prometheus, version ${expected} (branch: HEAD, revision: "[0-9a-f]{40}")" ]] \
    || { echo "$version" >&2; exit 1; }
version="$(promtool --version 2>&1)"
[[ "$version" == "promtool, version ${expected} "* ]] || { echo "$version" >&2; exit 1; }

echo "static binaries"
for binary in /usr/bin/prometheus /usr/bin/promtool; do
    linkage="$(ldd "$binary" 2>&1 || true)"
    [[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$binary: $linkage" >&2; exit 1; }
done

echo "features Debian has to leave out are there"
help="$(prometheus --help 2>&1)"
[[ "$help" == *web.enable-otlp-receiver* ]] || { echo "no OTLP receiver" >&2; exit 1; }
# Every service discovery mechanism Debian disables, and Azure AD for
# remote write: the configuration only parses if they are compiled in.
cat > "$work/features.yml" <<'EOF'
scrape_configs:
  - job_name: consul
    consul_sd_configs: [{server: "127.0.0.1:8500"}]
  - job_name: nomad
    nomad_sd_configs: [{server: "http://127.0.0.1:4646"}]
  - job_name: xds
    kuma_sd_configs: [{server: "http://127.0.0.1:5676"}]
  - job_name: azure
    azure_sd_configs: [{subscription_id: "00000000-0000-0000-0000-000000000000", authentication_method: ManagedIdentity}]
  - job_name: openstack
    openstack_sd_configs: [{role: instance, region: RegionOne}]
  - job_name: stackit
    stackit_sd_configs: [{project: "00000000-0000-0000-0000-000000000000"}]
remote_write:
  - url: "http://127.0.0.1:9201/write"
    azuread: {cloud: AzurePublic, managed_identity: {client_id: "00000000-0000-0000-0000-000000000000"}}
EOF
promtool check config "$work/features.yml"

echo "system user, configuration and data directory"
id prometheus
promtool check config /etc/prometheus/prometheus.yml
test "$(stat -c %U /var/lib/prometheus/metrics2)" = prometheus

echo "service starts as the prometheus user and scrapes itself"
start_prometheus
test "$(prometheus_users)" = prometheus
expect_value 'up{job="prometheus"}' 1
buildinfo="$(curl -fsS "$PROM/api/v1/status/buildinfo")"
[[ "$buildinfo" == *"\"version\":\"${expected}\""* ]] || { echo "$buildinfo" >&2; exit 1; }
instant="$(promtool query instant "$PROM" 'up{job="prometheus"}')"
[[ "$instant" == *"=> 1 @"* ]] || { echo "$instant" >&2; exit 1; }

echo "the web UI is served"
# Debian's package serves a "no UI" placeholder here.
page="$(curl -fsSL "$PROM/query")"
[[ "$page" =~ src=\"\./assets/(index-[A-Za-z0-9_-]+\.js)\" ]] || { echo "$page" >&2; exit 1; }
curl -fsS -o "$work/ui.js" "$PROM/assets/${BASH_REMATCH[1]}"
test "$(stat -c %s "$work/ui.js")" -gt 100000

echo "reload through the init script"
loaded="$(value prometheus_config_last_reload_success_timestamp_seconds)"
test -n "$loaded"
sleep 1
service prometheus reload
for _ in $(seq 1 60); do
    reloaded="$(value prometheus_config_last_reload_success_timestamp_seconds)"
    [[ -n "$reloaded" && "$reloaded" != "$loaded" ]] && break
    sleep 0.5
done
[[ "$reloaded" != "$loaded" ]] || { echo "configuration was not reloaded" >&2; show_log; exit 1; }
test "$(prometheus_users)" = prometheus

echo "data lands in /var/lib/prometheus/metrics2"
test -d /var/lib/prometheus/metrics2/wal
stop_prometheus

echo "man pages are packaged"
# From the file lists: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L prometheus promtool)"
for path in /usr/share/man/man1/prometheus.1.gz /usr/share/man/man1/promtool.1.gz; do
    grep -qxF "$path" <<<"$files" || { echo "missing from the packages: $path" >&2; exit 1; }
done
echo "smoke test passed"
