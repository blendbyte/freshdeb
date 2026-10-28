#!/bin/bash
#
# Smoke test for frp, run as root in a fresh container right after our
# packages were installed. frps and frpc run as the frp user on localhost,
# with a token: a TCP proxy to a local web server, the frps dashboard and
# the frpc admin UI. Nothing leaves the container.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 0.71.1

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/frp/tests/lib.sh
source "$(dirname "$0")/lib.sh"
work="$(mktemp -d)"
pids=()
cleanup() {
    local pid
    for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
    rm -rf "$work"
}
trap cleanup EXIT

echo "binaries report the expected version, static"
for binary in frps frpc; do
    version="$("$binary" -v 2>&1)"
    [[ "$version" == "$expected" ]] || { echo "$binary -v: $version" >&2; exit 1; }
    linkage="$(ldd "/usr/bin/$binary" 2>&1 || true)"
    [[ "$linkage" == *"not a dynamic executable"* ]] || { echo "$linkage" >&2; exit 1; }
done

echo "man pages and shell completions are packaged"
# From the file lists: Ubuntu's minimized images skip /usr/share/man.
for binary in frps frpc; do
    files="$(dpkg -L "$binary")"
    for file in "/usr/share/man/man1/$binary.1.gz" "/usr/share/bash-completion/completions/$binary" \
                "/usr/share/zsh/vendor-completions/_$binary" "/usr/share/fish/vendor_completions.d/$binary.fish"; do
        grep -qxF "$file" <<<"$files" || { echo "missing from $binary: $file" >&2; exit 1; }
    done
done

echo "user, configuration readable by root and frp only"
id frp
for config in /etc/frp/frps.toml /etc/frp/frpc.toml; do
    [[ "$(stat -c '%U:%G %a' "$config")" == "root:frp 640" ]] || { stat "$config" >&2; exit 1; }
done
frps verify -c /etc/frp/frps.toml
frpc verify -c /etc/frp/frpc.toml

echo "services installed, not enabled"
for unit in frps.service frps@.service frpc.service frpc@.service; do
    unit_dir "$unit" >/dev/null || { echo "missing: $unit" >&2; exit 1; }
done
for unit in frps.service frpc.service; do
    [[ ! -e "/etc/systemd/system/multi-user.target.wants/$unit" ]] || { echo "enabled: $unit" >&2; exit 1; }
done

echo "frps and frpc forward a TCP proxy to a local web server"
mkdir -p "$work/www"
echo "freshdeb frp smoke test" > "$work/www/index.html"
busybox httpd -f -p 127.0.0.1:18080 -h "$work/www" &
pids+=($!)
cat > "$work/frps.toml" <<'TOML'
bindAddr = "127.0.0.1"
bindPort = 17000
auth.token = "freshdeb-token"
webServer.addr = "127.0.0.1"
webServer.port = 17500
webServer.user = "admin"
webServer.password = "freshdeb-secret"
TOML
cat > "$work/frpc.toml" <<'TOML'
serverAddr = "127.0.0.1"
serverPort = 17000
auth.token = "freshdeb-token"
webServer.addr = "127.0.0.1"
webServer.port = 17400

[[proxies]]
name = "freshdeb-web"
type = "tcp"
localIP = "127.0.0.1"
localPort = 18080
remotePort = 16000
TOML
chmod 0755 "$work"
chmod 0644 "$work"/*.toml
frps verify -c "$work/frps.toml"
frpc verify -c "$work/frpc.toml"
runuser -u frp -- frps -c "$work/frps.toml" >"$work/frps.log" 2>&1 &
pids+=($!)
# frpc gives up if the server isn't there yet (loginFailExit).
wait_http http://127.0.0.1:17500/api/serverinfo -u admin:freshdeb-secret || { cat "$work/frps.log" >&2; exit 1; }
runuser -u frp -- frpc -c "$work/frpc.toml" >"$work/frpc.log" 2>&1 &
pids+=($!)
wait_http http://127.0.0.1:16000/ || { cat "$work/frps.log" "$work/frpc.log" >&2; exit 1; }
page="$(curl -fsS http://127.0.0.1:16000/)"
[[ "$page" == "freshdeb frp smoke test" ]] || { echo "$page" >&2; exit 1; }
[[ "$(frp_users | sort -u)" == frp ]] || { frp_users >&2; exit 1; }

# dashboard_assets <base url> [curl args...]: the page and its script.
dashboard_assets() {
    local base="$1" index script size
    shift
    index="$(curl -fsSL "$@" "$base/")"
    [[ "$index" == *'<div id="app">'* ]] || { echo "$index" >&2; return 1; }
    script="$(grep -o 'src="[^"]*\.js"' <<<"$index" | head -n 1 | sed 's/^src="\(\.\/\)\{0,1\}//; s/"$//')"
    [[ -n "$script" ]] || { echo "no script in: $index" >&2; return 1; }
    size="$(curl -fsS "$@" -o /dev/null -w '%{size_download}' "$base/static/$script")"
    (( size > 100000 )) || { echo "$script: $size bytes" >&2; return 1; }
}

echo "frps dashboard and API"
status="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:17500/api/serverinfo)"
[[ "$status" == 401 ]] || { echo "without credentials: HTTP $status" >&2; exit 1; }
dashboard_assets http://127.0.0.1:17500 -u admin:freshdeb-secret
info="$(curl -fsS -u admin:freshdeb-secret http://127.0.0.1:17500/api/serverinfo)"
[[ "$info" == *"\"version\":\"$expected\""* ]] || { echo "$info" >&2; exit 1; }

echo "frpc admin UI, API and reload"
dashboard_assets http://127.0.0.1:17400
proxies="$(curl -fsS http://127.0.0.1:17400/api/status)"
[[ "$proxies" == *freshdeb-web* ]] || { echo "$proxies" >&2; exit 1; }
runuser -u frp -- frpc reload -c "$work/frpc.toml"
echo "smoke test passed"
