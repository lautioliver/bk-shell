#!/usr/bin/env bash
# Lo que tmux le pasa a sh (run-shell y los #(…) del status): un nombre de sesión o una carpeta hostil no se
# parte ni se ejecuta. El hook window-resized de tmux/bk.conf corre para TODAS las sesiones del servidor.
# Con un servidor de tmux propio (-S en un tmp, sin TMUX) y un bk falso que solo anota sus argumentos.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
# shellcheck source=tests/lib.sh
. "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

# Ningún #(…), run-shell ni display-popup -E con un formato sin q: (lo escapa para sh)
SUELTOS=$(python3 - "$ROOT/bin/bk" "$ROOT/tmux/bk.conf" <<'PY'
import re, sys
for p in sys.argv[1:]:
    for n, line in enumerate(open(p), 1):
        shell = (re.findall(r"#\(([^)]*)\)", line) + re.findall(r"run-shell(?: -b)? (.*)", line)
                 + re.findall(r"display-popup .*-E (.*)", line))
        if any(re.search(r"#\{(session_name|pane_current_path|window_name|pane_title|client_name)\}", s) for s in shell):
            print(f"{p.split('/')[-1]}:{n}")
PY
)
check_eq "$SUELTOS" "" "run-shell, #(…) y display-popup -E pasan los nombres con #{q:…}"

if ! command -v tmux >/dev/null; then finish; exit; fi
mkdir -p "$TMP/bin" "$TMP/w"
printf '#!/bin/sh\nfor a; do printf "[%%s]" "$a"; done >> "%s/bk.log"; echo >> "%s/bk.log"\n' "$TMP" "$TMP" > "$TMP/bin/bk"
chmod +x "$TMP/bin/bk"
grep -E '^set-hook -g window-resized' "$ROOT/tmux/bk.conf" > "$TMP/hook.conf"
SOCK="$TMP/sock"
t() { env -u TMUX -u TMUX_PANE PATH="$TMP/bin:$PATH" BKQ="$TMP/PWNED" tmux -S "$SOCK" -f /dev/null "$@"; }

# resized <nombre>: crea la sesión, registra el hook de bk.conf y fuerza un cambio de tamaño; devuelve el log
resized() {
  : > "$TMP/bk.log"
  t new-session -d -s "$1" -c "$TMP/w" -x 80 -y 24 && t source-file "$TMP/hook.conf" \
    && t resize-window -t "=$1:" -x 100 -y 30
  for _ in $(seq 30); do [ -s "$TMP/bk.log" ] && break; sleep 0.1; done
  sleep 0.2; cat "$TMP/bk.log"
}
check_eq "$(resized 'mia; touch "$BKQ"')" '[_layout][mia; touch "$BKQ"]' "un nombre con ; llega entero a bk _layout"
check_eq "$(resized 'otra $(touch "$BKQ") x')" '[_layout][otra $(touch "$BKQ") x]' "con \$(…) y espacios, también"
check "$([ ! -e "$TMP/PWNED" ]; echo $?)" "y sh no ejecuta nada del nombre"
t kill-server 2>/dev/null
finish
