#!/bin/bash
#
# Smoke test for unbound, run as root in a fresh container right after our
# packages were installed (and again after an upgrade from the distro's
# version). The server answers for a local zone; nothing is resolved on the
# internet.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 1.26.2

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/unbound/tests/lib.sh
source "$(dirname "$0")/lib.sh"
trap 'rm -f "$SNIPPET"' EXIT

echo "binaries report the expected version"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
version="$(unbound -V)"
[[ "$version" == "Version ${expected}"$'\n'* ]] || { echo "$version" >&2; exit 1; }
host_version="$(unbound-host -V 2>&1 || true)"
[[ "$host_version" == *"Version ${expected}"* ]] || { echo "$host_version" >&2; exit 1; }
anchor_help="$(unbound-anchor -h 2>&1 || true)"
[[ "$anchor_help" == *"Version ${expected}"* ]] || { echo "$anchor_help" >&2; exit 1; }

echo "system user"
id unbound

echo "the service starts with Debian's configuration and answers for a local zone"
write_snippet v1
start_unbound
# The trust anchor the init script set up from dns-root-data on first start.
test -s /var/lib/unbound/root.key
unbound-checkconf
[[ "$(unbound_users | sort -u)" == unbound ]] || { unbound_users >&2; exit 1; }
[[ "$(answer A)" == 192.0.2.53 ]] || { answer A >&2; exit 1; }
expect_txt v1
status="$(unbound-control status)"
[[ "$status" == *"version: ${expected}"* ]] || { echo "$status" >&2; exit 1; }

echo "service reload and fast_reload apply a changed configuration"
write_snippet v2
service unbound reload
expect_txt v2
write_snippet v3
unbound-control fast_reload >/dev/null
expect_txt v3

echo "libunbound and python3-unbound resolve through the server"
txt="$(python3 - <<'EOF'
import unbound
ctx = unbound.ub_ctx()
# libunbound skips localhost servers by default; ours is the forwarder.
ctx.set_option("do-not-query-localhost:", "no")
ctx.set_fwd("127.0.0.1")
# test. is one of libunbound's own default local zones (RFC 6761); removing
# it only works once the configuration is set up, after set_fwd.
assert ctx.zone_remove("test.") == 0
status, result = ctx.resolve("a.freshdeb.test.", unbound.RR_TYPE_TXT, unbound.RR_CLASS_IN)
assert status == 0 and result.havedata, (status, result.rcode_str)
print(result.data.as_raw_data()[0][1:].decode())
EOF
)"
[[ "$txt" == v3 ]] || { echo "python3-unbound got: $txt" >&2; exit 1; }

stop_unbound

echo "man pages are packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L unbound)"
grep -qxF /usr/share/man/man5/unbound.conf.5.gz <<<"$files" \
    || { echo "missing from the package: unbound.conf.5" >&2; exit 1; }
echo "smoke test passed"
