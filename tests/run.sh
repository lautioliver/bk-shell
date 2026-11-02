#!/usr/bin/env bash
# Corre todos los tests de bk shell: los de bash (TAP) y los de Python. Sale con 1 si alguno falla.
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
rc=0
for t in "$HERE"/test_*.sh; do echo "# $(basename "$t")"; bash "$t" || rc=1; done
echo "# test_views.py"; python3 -m unittest -q "$HERE/test_views.py" 2>&1 | tail -5; [ "${PIPESTATUS[0]}" -eq 0 ] || rc=1
for f in "$ROOT"/lib/*.py; do python3 -m py_compile "$f" || rc=1; done
rm -rf "$ROOT"/lib/__pycache__ "$HERE"/__pycache__
[ $rc -eq 0 ] && echo "# todo ok" || echo "# FALLÓ algo"
exit $rc
