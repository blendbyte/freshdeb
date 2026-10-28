#!/bin/bash
#
# Smoke test for postsrsd, run as root in a fresh container right after our
# package was installed (and again after an upgrade from the distribution's
# version). It runs through its init script; Postfix's lookups are made over
# the socketmap socket directly.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 2.4.1

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/postsrsd/tests/lib.sh
source "$(dirname "$0")/lib.sh"
saved="$(mktemp)"
cp -a "$CONF" "$saved"
trap 'stop_postsrsd; cp -a "$saved" "$CONF"; rm -f "$saved"' EXIT

echo "binary reports the expected version"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
version="$(postsrsd -v 2>&1)"
[[ "$version" == *"$expected"* ]] || { echo "$version" >&2; exit 1; }

echo "secret and configuration"
[[ "$(stat -c '%U %a' "$SECRET")" == "root 600" ]] || { stat "$SECRET" >&2; exit 1; }
test -s "$SECRET"
test -f "$CONF"

echo "systemd unit and man page are packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/man.
files="$(dpkg -L postsrsd)"
grep -qxF /usr/share/man/man8/postsrsd.8.gz <<<"$files" || { echo "missing from the package: the man page" >&2; exit 1; }
grep -q '/postsrsd.service$' <<<"$files" || { echo "missing from the package: the systemd unit" >&2; exit 1; }

echo "service starts with local domains; workers drop root"
# The local domains: example.org (the first one is the SRS domain) and every
# subdomain of example.net.
sed -i 's/^domains = .*/domains = { "example.org", ".example.net" }/' "$CONF"
start_postsrsd
wait_for "postsrsd to answer" socketmap forward probe@remote.test
# The main process stays root, to re-read the secrets and domains on reload;
# each connection is served by a worker in the chroot, as postsrsd.
workers="$(worker_users | sort -u)"
[[ "$workers" == "postsrsd /var/lib/postsrsd" ]] || { echo "workers: $workers" >&2; exit 1; }

echo "forward rewriting and its reverse"
forward="$(socketmap forward user@remote.test)"
expect "forward" 'OK SRS0[=+-]????[=+-]??[=+-]remote.test[=+-]user@example.org' "$forward"
srs="${forward#OK }"
expect "reverse" "OK user@remote.test" "$(socketmap reverse "$srs")"

echo "local domains are not rewritten"
expect "own domain" "NOTFOUND*" "$(socketmap forward user@example.org)"
expect "subdomain" "NOTFOUND*" "$(socketmap forward user@mail.example.net)"

echo "tampered addresses are refused"
tampered="SRS0=AAAA${srs#SRS0?????}"
[[ "$(socketmap reverse "$tampered")" != OK* ]] || { echo "accepted: $tampered" >&2; exit 1; }

echo "smoke test passed"
