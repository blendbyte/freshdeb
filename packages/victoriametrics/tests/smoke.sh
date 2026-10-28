#!/bin/bash
#
# Smoke test for VictoriaMetrics, run as root in a fresh container right
# after our package was installed (and again after an upgrade from the
# distro's version). The server runs through its init script as
# _victoria-metrics; the tools run next to it. Nothing leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 1.155.0

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/victoriametrics/tests/lib.sh
source "$(dirname "$0")/lib.sh"
work="$(mktemp -d)"
trap 'kill "${vmagent_pid:-}" "${restored_pid:-}" 2>/dev/null || true; rm -rf "$work"' EXIT
binaries=(victoria-metrics vmagent vmalert vmalert-tool vmauth vmbackup vmctl vmrestore)

echo "binaries report the expected version"
# Upstream's stamp: <app>-<build date>-tags-v<version>-0-g<commit>. The
# urfave/cli tools print "<app> version <stamp>". Captured first throughout:
# "cmd | grep -q" trips pipefail when grep exits early.
for binary in "${binaries[@]}"; do
    case "$binary" in
        vmctl|vmalert-tool) version="$("$binary" --version 2>/dev/null | head -n 1)"; version="${version#"$binary version "}" ;;
        *) version="$("$binary" -version 2>/dev/null)" ;;
    esac
    [[ "$version" =~ ^"$binary"-[0-9]{8}-[0-9]{6}-tags-v"$expected"-0-g[0-9a-f]{10}$ ]] \
        || { echo "$binary: $version" >&2; exit 1; }
done

echo "static binaries"
for binary in "${binaries[@]}"; do
    linkage="$(ldd "/usr/bin/$binary" 2>&1 || true)"
    [[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$binary: $linkage" >&2; exit 1; }
done

echo "system user and data directory"
id _victoria-metrics
test "$(stat -c %U /var/lib/victoria-metrics)" = _victoria-metrics

echo "service starts as _victoria-metrics"
start_vm
test "$(vm_users)" = _victoria-metrics

echo "samples written are returned by queries"
at=$(( $(date +%s) - 120 ))
write_samples "$VM" <<EOF
freshdeb_smoke{role="test"} 42 ${at}000
EOF
expect_value 'freshdeb_smoke{role="test"}' 42 "$at"
expect_value 'sum(freshdeb_smoke)' 42 "$at"

echo "web UI is served"
page="$(curl -fsS "$VM/vmui/")"
# The application bundle (assets/index-<hash>.js), not the small dashboards
# list loaded before it.
script="$(grep -o 'src="[^"]*assets/[^"]*\.js"' <<<"$page" | head -n 1 | sed 's/^src="\.\{0,1\}\/\{0,1\}//; s/"$//')"
[[ -n "$script" ]] || { echo "no application script in the UI page: $page" >&2; exit 1; }
size="$(curl -fsS "$VM/vmui/$script" | wc -c)"
(( size > 100000 )) || { echo "UI script $script is only $size bytes" >&2; exit 1; }

echo "vmagent scrapes and remote-writes into it"
cat > "$work/scrape.yml" <<'EOF'
scrape_configs:
  - job_name: freshdeb-vmagent
    scrape_interval: 1s
    static_configs:
      - targets: ["127.0.0.1:8428"]
EOF
vmagent -promscrape.config="$work/scrape.yml" -remoteWrite.url="$VM/api/v1/write" \
    -remoteWrite.tmpDataPath="$work/vmagent" -httpListenAddr=127.0.0.1:8429 >"$work/vmagent.log" 2>&1 &
vmagent_pid=$!
expect_value 'up{job="freshdeb-vmagent"}' 1 \
    || { cat "$work/vmagent.log" >&2; exit 1; }
kill "$vmagent_pid"

echo "vmalert-tool runs rule unit tests"
cat > "$work/rules.yml" <<'EOF'
groups:
  - name: freshdeb
    rules:
      - alert: FreshdebHigh
        expr: freshdeb_value > 10
        for: 1m
EOF
cat > "$work/rules-test.yml" <<'EOF'
rule_files: [rules.yml]
evaluation_interval: 1m
tests:
  - interval: 1m
    input_series:
      - series: 'freshdeb_value{job="test"}'
        values: '20x10'
    alert_rule_test:
      - eval_time: 5m
        groupname: freshdeb
        alertname: FreshdebHigh
        exp_alerts:
          - exp_labels: {job: test}
EOF
(cd "$work" && vmalert-tool unittest --files=rules-test.yml) > "$work/unittest.log" 2>&1 \
    || { cat "$work/unittest.log" >&2; exit 1; }

echo "vmbackup and vmrestore round-trip a snapshot"
reply="$(curl -fsS "$VM/snapshot/create")"
snapshot="$(sed -n 's/.*"snapshot":"\([^"]*\)".*/\1/p' <<<"$reply")"
[[ -n "$snapshot" ]] || { echo "no snapshot: $reply" >&2; exit 1; }
vmbackup -storageDataPath=/var/lib/victoria-metrics -snapshotName="$snapshot" \
    -dst="fs://$work/backup" >"$work/vmbackup.log" 2>&1 || { cat "$work/vmbackup.log" >&2; exit 1; }
curl -fsS "$VM/snapshot/delete?snapshot=$snapshot" >/dev/null
vmrestore -src="fs://$work/backup" -storageDataPath="$work/restored" >"$work/vmrestore.log" 2>&1 \
    || { cat "$work/vmrestore.log" >&2; exit 1; }
victoria-metrics -storageDataPath="$work/restored" -httpListenAddr=127.0.0.1:18428 >"$work/restored.log" 2>&1 &
restored_pid=$!
wait_healthy http://127.0.0.1:18428 || { cat "$work/restored.log" >&2; exit 1; }
expect_value 'freshdeb_smoke{role="test"}' 42 "$at" http://127.0.0.1:18428
kill "$restored_pid"
wait "$restored_pid" || true

echo "service reload keeps it running"
pid="$(cat /run/victoria-metrics/victoria-metrics.pid)"
service victoria-metrics reload
sleep 1
wait_healthy "$VM"
test "$(cat /run/victoria-metrics/victoria-metrics.pid)" = "$pid"
test -d "/proc/$pid"
expect_value 'freshdeb_smoke{role="test"}' 42 "$at"

stop_vm
echo "smoke test passed"
