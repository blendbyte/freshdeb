#!/bin/bash
#
# Smoke test for node_exporter, run as root in a fresh container right after
# our package was installed (and again after an upgrade from the distro's
# version). It runs through its init script as the prometheus user; nothing
# leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 1.12.1

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/prometheus-node-exporter/tests/lib.sh
source "$(dirname "$0")/lib.sh"
work="$(mktemp -d)"
trap 'kill "${auth_pid:-}" 2>/dev/null || true; rm -f "$TEXTFILE_DIR/freshdeb-smoke.prom"; rm -rf "$work"' EXIT

echo "binary reports the expected version"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
version="$(prometheus-node-exporter --version 2>&1)"
[[ "$version" =~ ^"node_exporter, version ${expected} (branch: HEAD, revision: "[0-9a-f]{40}")" ]] \
    || { echo "$version" >&2; exit 1; }

echo "static binary"
linkage="$(ldd /usr/bin/prometheus-node-exporter 2>&1 || true)"
[[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$linkage" >&2; exit 1; }

echo "system user and textfile directory"
id prometheus
test -d "$TEXTFILE_DIR"
printf '# HELP freshdeb_smoke Written by the smoke test.\n# TYPE freshdeb_smoke gauge\nfreshdeb_smoke{role="test"} 1\n' \
    > "$TEXTFILE_DIR/freshdeb-smoke.prom"

echo "service starts as the prometheus user and serves machine metrics"
start_exporter 9100
test "$(exporter_users)" = prometheus
expect_metrics 9100 \
    "node_exporter_build_info{branch=\"HEAD\"," \
    'node_cpu_seconds_total{' 'node_memory_MemTotal_bytes ' 'node_load1 ' \
    'node_network_receive_bytes_total{' 'node_uname_info{' \
    'freshdeb_smoke{role="test"} 1' 'node_textfile_scrape_error 0' \
    'node_scrape_collector_success{collector="cpu"} 1'
scrape="$(curl -fsS http://127.0.0.1:9100/metrics)"
[[ "$scrape" == *"version=\"${expected}\""* ]] || { echo "build_info has no version ${expected}" >&2; exit 1; }
stop_exporter

echo "TLS and auth settings (--web.config.file) work"
# Password "freshdeb-secret".
cat > "$work/web.yml" <<'EOF'
basic_auth_users:
  freshdeb: $2y$10$CykA/MSu8zdbzw6jt9cQQe2Ul0VZ6h3YhvY0TmfQ6pn3Jm.XkJ28C
EOF
chmod 0644 "$work/web.yml"
chmod 0755 "$work"
runuser -u prometheus -- prometheus-node-exporter --web.listen-address=127.0.0.1:19100 \
    --web.config.file="$work/web.yml" >"$work/auth.log" 2>&1 &
auth_pid=$!
for _ in $(seq 1 100); do
    status="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:19100/metrics || true)"
    [[ "$status" == 401 ]] && break
    sleep 0.2
done
[[ "$status" == 401 ]] || { echo "without credentials: HTTP $status" >&2; cat "$work/auth.log" >&2; exit 1; }
curl -fsS -u freshdeb:freshdeb-secret -o /dev/null http://127.0.0.1:19100/metrics
kill "$auth_pid"

echo "man page is packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L prometheus-node-exporter)"
grep -qxF /usr/share/man/man1/prometheus-node-exporter.1.gz <<<"$files" \
    || { echo "missing from the package: the man page" >&2; exit 1; }
echo "smoke test passed"
