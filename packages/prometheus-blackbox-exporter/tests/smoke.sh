#!/bin/bash
#
# Smoke test for blackbox_exporter, run as root in a fresh container right
# after our package was installed (and again after an upgrade from the
# distro's version). It runs through its init script as the prometheus user
# and only probes targets inside the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 0.29.1

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/prometheus-blackbox-exporter/tests/lib.sh
source "$(dirname "$0")/lib.sh"
work="$(mktemp -d)"
cp "$CONFIG" "$work/blackbox.yml.orig"
trap 'cp "$work/blackbox.yml.orig" "$CONFIG"; rm -rf "$work"' EXIT

echo "binary reports the expected version"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
version="$(prometheus-blackbox-exporter --version 2>&1)"
[[ "$version" =~ ^"blackbox_exporter, version ${expected} (branch: HEAD, revision: "[0-9a-f]{40}")" ]] \
    || { echo "$version" >&2; exit 1; }

echo "static binary"
linkage="$(ldd /usr/bin/prometheus-blackbox-exporter 2>&1 || true)"
[[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$linkage" >&2; exit 1; }

echo "system user and a valid shipped configuration"
id prometheus
prometheus-blackbox-exporter --config.check --config.file="$CONFIG"

echo "service starts as the prometheus user"
start_exporter 9115
test "$(exporter_users)" = prometheus
metrics="$(curl -fsS http://127.0.0.1:9115/metrics)"
[[ "$metrics" == *'blackbox_exporter_build_info{branch="HEAD",'*"version=\"${expected}\"} 1"* ]] \
    || { echo "build_info has no version ${expected}" >&2; exit 1; }
[[ "$metrics" == *$'\nblackbox_exporter_config_last_reload_successful 1\n'* ]] \
    || { echo "configuration not loaded" >&2; exit 1; }

echo "HTTP and TCP probes"
expect_probe 9115 http_2xx http://127.0.0.1:9115/metrics 'probe_success 1' 'probe_http_status_code 200'
expect_probe 9115 tcp_connect 127.0.0.1:9115 'probe_success 1'
# Nothing listens on port 1: a probe that has to fail.
expect_probe 9115 tcp_connect 127.0.0.1:1 'probe_success 0'

echo "ICMP probes with the capability from debconf"
stop_exporter
echo 'prometheus-blackbox-exporter prometheus-blackbox-exporter/want_cap_net_raw boolean true' \
    | debconf-set-selections
dpkg-reconfigure -f noninteractive prometheus-blackbox-exporter
caps="$(getcap /usr/bin/prometheus-blackbox-exporter)"
[[ "$caps" == *cap_net_raw*ep* ]] || { echo "no cap_net_raw: '$caps'" >&2; exit 1; }
start_exporter 9115
expect_probe 9115 icmp 127.0.0.1 'probe_success 1'
stop_exporter
echo 'prometheus-blackbox-exporter prometheus-blackbox-exporter/want_cap_net_raw boolean false' \
    | debconf-set-selections
dpkg-reconfigure -f noninteractive prometheus-blackbox-exporter
caps="$(getcap /usr/bin/prometheus-blackbox-exporter)"
# setcap leaves an empty set ("<path> =") behind.
[[ "$caps" != *cap_net_raw* ]] || { echo "cap_net_raw not removed: '$caps'" >&2; exit 1; }

echo "service reload picks up a new module"
start_exporter 9115
status="$(curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:9115/probe?module=freshdeb_reloaded&target=127.0.0.1:9115')"
[[ "$status" == 400 ]] || { echo "unknown module: HTTP $status" >&2; exit 1; }
add_module freshdeb_reloaded
service prometheus-blackbox-exporter reload
for _ in $(seq 1 50); do
    probe 9115 freshdeb_reloaded 127.0.0.1:9115 >/dev/null 2>&1 && break
    sleep 0.2
done
expect_probe 9115 freshdeb_reloaded 127.0.0.1:9115 'probe_success 1'
test "$(exporter_users)" = prometheus
stop_exporter

echo "man page is packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L prometheus-blackbox-exporter)"
grep -qxF /usr/share/man/man1/prometheus-blackbox-exporter.1.gz <<<"$files" \
    || { echo "missing from the package: the man page" >&2; exit 1; }
echo "smoke test passed"
