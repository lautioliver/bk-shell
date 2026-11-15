#!/usr/bin/env bash
# Los tests que comparan rutas con el BK_WS de bin/bk (que sale de readlink -f, la ruta física) pasan también
# si el repo se corre desde una ruta con un enlace simbólico (como /var → /private/var en macOS).
set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"; ROOT="$(dirname "$HERE")"
# shellcheck source=tests/lib.sh
. "$HERE/lib.sh"
TMP=$(cd "$(mktemp -d)" && pwd -P); trap 'rm -rf "$TMP"' EXIT
ln -s "$ROOT" "$TMP/enlace"
for t in test_plugins.sh test_enganches.sh; do
  out=$(cd "$TMP" && bash "enlace/tests/$t" 2>&1); rc=$?
  check_eq "$rc|$(printf '%s\n' "$out" | grep -c '^not ok')|$(printf '%s\n' "$out" | grep -q '^ok ' && echo hay)" "0|0|hay" \
    "$t pasa desde una ruta con un enlace simbólico"
done
finish
