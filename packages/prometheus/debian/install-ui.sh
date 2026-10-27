#!/usr/bin/env bash

set -e

echo "Prometheus React web UI installer"

if [ "$EUID" != 0 ]; then
  echo "This script must be run as root (or via sudo)." >&2
  exit 1
fi

PACKAGE_VER=$(dpkg-query -f '${Version}' -W prometheus)
echo "Installed Prometheus package version: $PACKAGE_VER"

DEST_DIR=/usr/share/prometheus/web

if [ ! -d $DEST_DIR ]; then
  echo "Destination directory $DEST_DIR does not exist." >&2
  exit 1
fi

# Since the React web UI tarball contains dynamically-named files which will change with each
# release, clear out any existing installation first.
rm -rf ${DEST_DIR}/static/react

# Strip Debian version suffix to get back to native upstream version.
UPSTREAM_VER=${PACKAGE_VER%%[+-]*}
echo "Upstream version: $UPSTREAM_VER"

TMP_DIR=$(mktemp -d)
# shellcheck disable=SC2064
trap "rm -rf $TMP_DIR" EXIT

echo "Fetching upstream release web UI tarball..."
wget -P $TMP_DIR -q https://github.com/prometheus/prometheus/releases/download/v${UPSTREAM_VER}/prometheus-web-ui-${UPSTREAM_VER}.tar.gz

echo "Installing React web UI in Prometheus directory..."
tar -x -f ${TMP_DIR}/prometheus-web-ui-${UPSTREAM_VER}.tar.gz -C $DEST_DIR static/react

echo "Prometheus React web UI successfully installed."