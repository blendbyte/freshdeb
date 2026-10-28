#!/bin/bash

set -e

SRCDIR=/usr/share/gocode/src/github.com/prometheus/alertmanager/ui/app
DSTDIR=/usr/share/prometheus/alertmanager/ui

ASROOT=sudo

echo "Installing dependencies..." >&2
$ASROOT apt install --update -y --no-install-recommends elm-compiler libjs-bootstrap4 \
    fonts-font-awesome curl uglifyjs golang-github-prometheus-alertmanager-dev ca-certificates

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo "Compiling source code..." >&2
ln -s $SRCDIR/src $SRCDIR/elm.json $TMPDIR
# Exit 77 specifically when the elm build fails. debian/tests/generate-ui
# relies on this code to distinguish an expected elm/core build failure on
# 32-bit architectures (Debian bug #1023481) from any other kind of failure.
if ! (cd "$TMPDIR" && elm make src/Main.elm --optimize --output "$TMPDIR/app.js"); then
    echo "ERROR: elm make failed (expected on 32-bit architectures; see Debian bug #1023481)" >&2
    exit 77
fi

echo "Optimising source code..." >&2
uglifyjs $TMPDIR/app.js \
    --compress 'pure_funcs="F2,F3,F4,F5,F6,F7,F8,F9,A2,A3,A4,A5,A6,A7,A8,A9",pure_getters,keep_fargs=false,unsafe_comps,unsafe' \
    --mangle --output $TMPDIR/script.js

echo "Installing in Alertmanager directory..." >&2
$ASROOT mkdir -p $DSTDIR $DSTDIR/lib
$ASROOT cp $TMPDIR/script.js $SRCDIR/index.html $SRCDIR/public/favicon.ico $DSTDIR
test -L $DSTDIR/lib/font-awesome || \
    $ASROOT ln -s -T /usr/share/fonts-font-awesome $DSTDIR/lib/font-awesome
test -L $DSTDIR/lib/bootstrap || \
    $ASROOT ln -s -T /usr/share/nodejs/bootstrap/dist $DSTDIR/lib/bootstrap

echo "Finished! Please, restart prometheus-alertmanager to activate UI." >&2
