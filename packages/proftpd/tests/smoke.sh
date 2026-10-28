#!/bin/bash
#
# Smoke test for proftpd, run as root in a fresh container right after our
# packages were installed (and again after an upgrade from the distro's
# version): the configuration checks out with every module we build loaded,
# the server runs from its init script and serves FTP, FTPS and SFTP to a
# local user, and mod_vroot and mod_case work.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 1.3.9e
#      FRESHDEB_VERSION           companions' versions end in +<this>

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
# shellcheck source=packages/proftpd/tests/lib.sh
source "$(dirname "$0")/lib.sh"
work="$(mktemp -d)"
modules=/etc/proftpd/conf.d/freshdeb-modules.conf
trap 'rm -rf "$work" "$modules" "$SNIPPET"' EXIT

echo "proftpd reports the expected version"
# Captured first throughout: "cmd | grep -q" trips pipefail when grep exits early.
version="$(proftpd -v 2>&1)"
[[ "$version" == "ProFTPD Version ${expected}"* ]] || { echo "$version" >&2; exit 1; }

echo "system users"
id ftp
id proftpd

echo "Debian's configuration checks out"
proftpd -t

echo "every companion module loads"
# Companions are the packages versioned <module version>+<our version>. Some
# build on modules from proftpd-mod-crypto and proftpd-mod-ldap: those first.
{
    echo "LoadModule mod_tls.c"
    echo "LoadModule mod_sftp.c"
    echo "LoadModule mod_ldap.c"
} > "$modules"
companions=()
while read -r package package_version; do
    [[ "$package_version" == *"+${FRESHDEB_VERSION:?}" ]] || continue
    companions+=("$package")
    files="$(dpkg -L "$package")"
    while read -r so; do
        echo "LoadModule $(basename "$so" .so).c" >> "$modules"
    done < <(grep -E '^/usr/lib/proftpd/mod_[a-z0-9_]+\.so$' <<<"$files")
done < <(dpkg-query -W -f='${Package} ${Version}\n' 'proftpd-mod-*')
(( ${#companions[@]} )) || { echo "no companion packages installed" >&2; exit 1; }
echo "  ${companions[*]}"
cat "$modules"
proftpd -t
# Each one really is loaded: a block for a missing module would be parsed,
# and its unknown directive fail the check.
while read -r _ module; do
    printf '<IfModule !%s>\n  FreshdebNotLoaded %s\n</IfModule>\n' "$module" "$module" >> "$modules"
done < <(grep '^LoadModule' "$modules")
proftpd -t
rm -f "$modules"

echo "the service starts from its init script and serves FTP"
add_ftp_user
printf 'LoadModule %s\n' mod_vroot.c mod_case.c mod_tls.c mod_sftp.c > "$SNIPPET"
write_snippet
# FTPS with a throwaway certificate.
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=127.0.0.1 \
    -keyout "$work/tls.key" -out "$work/tls.crt" 2>/dev/null
# SFTP on its own port, with its own host key.
openssl genrsa -out "$work/ssh_host_rsa_key" 3072 2>/dev/null
chmod 0600 "$work/tls.key" "$work/ssh_host_rsa_key"
cat >> "$SNIPPET" <<EOF
<IfModule mod_tls.c>
  TLSEngine on
  TLSLog /var/log/proftpd/tls.log
  TLSRSACertificateFile $work/tls.crt
  TLSRSACertificateKeyFile $work/tls.key
</IfModule>
<VirtualHost 127.0.0.1>
  Port 2222
  SFTPEngine on
  SFTPLog /var/log/proftpd/sftp.log
  SFTPHostKey $work/ssh_host_rsa_key
  SFTPAuthMethods password
  DefaultRoot ~
</VirtualHost>
EOF
proftpd -t
start_proftpd
service proftpd status
[[ -n "$(proftpd_pids)" ]] || { echo "no proftpd process" >&2; exit 1; }

echo "FTP upload, listing and download"
echo "uploaded over ftp" > "$work/up.txt"
ftp ftp://127.0.0.1/up.txt -T "$work/up.txt"
cmp "$work/up.txt" "$FTP_HOME/up.txt"
[[ "$(stat -c %U "$FTP_HOME/up.txt")" == "$FTP_USER" ]]
listing="$(ftp ftp://127.0.0.1/ --list-only)"
grep -qx up.txt <<<"${listing//$'\r'/}" || { echo "$listing" >&2; exit 1; }
# A long listing goes through mod_ls's ListOptions.
long="$(ftp ftp://127.0.0.1/)"
[[ "$long" == *"$FTP_USER"*up.txt* ]] || { echo "$long" >&2; exit 1; }
[[ "$(ftp ftp://127.0.0.1/up.txt)" == "uploaded over ftp" ]]

echo "mod_vroot and mod_case"
check_modules

echo "FTPS (explicit TLS on the control and data connections)"
echo "uploaded over ftps" > "$work/up-tls.txt"
ftp ftp://127.0.0.1/up-tls.txt --ssl-reqd --insecure -T "$work/up-tls.txt"
cmp "$work/up-tls.txt" "$FTP_HOME/up-tls.txt"
[[ "$(ftp ftp://127.0.0.1/up-tls.txt --ssl-reqd --insecure)" == "uploaded over ftps" ]]
grep -q 'requested, starting TLS handshake' /var/log/proftpd/tls.log \
    || { cat /var/log/proftpd/tls.log >&2; exit 1; }

echo "SFTP"
if [[ " $(curl -V | sed -n 's/^Protocols: //p') " == *" sftp "* ]]; then
    echo "uploaded over sftp" > "$work/up-sftp.txt"
    ftp sftp://127.0.0.1:2222/up-sftp.txt --insecure -T "$work/up-sftp.txt"
    cmp "$work/up-sftp.txt" "$FTP_HOME/up-sftp.txt"
    [[ "$(ftp sftp://127.0.0.1:2222/up-sftp.txt --insecure)" == "uploaded over sftp" ]]
else
    # Without an SFTP client, at least the SSH handshake starts.
    banner="$(timeout 5 bash -c 'exec 3<>/dev/tcp/127.0.0.1/2222; head -c 64 <&3' || true)"
    [[ "$banner" == SSH-2.0-* ]] || { echo "no SSH banner: $banner" >&2; exit 1; }
fi

stop_proftpd
rm -f "$FTP_HOME"/up*.txt
echo "smoke test passed"
