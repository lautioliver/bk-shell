#!/usr/bin/env bash
# bk e install.sh: sintaxis, ayuda, comillas de las instrucciones y el instalador en un home de prueba.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

for f in bin/bk install.sh hooks/agent_state.sh; do bash -n "$ROOT/$f"; check $? "$f no tiene errores de sintaxis"; done
"$ROOT/bin/bk" help >/dev/null; check $? "bk help funciona"
"$ROOT/bin/bk" no-existe >/dev/null 2>&1; [ $? -eq 2 ]; check $? "un comando desconocido sale con 2"

# Las instrucciones a los agentes llegan enteras: acentos y comillas simples (bash 3.2 rompía %q)
eval "$(sed -n '/^sq()/p' "$ROOT/bin/bk")"
got=$(eval "printf '%s' $(sq "Seguí: it's ñ")")
check_eq "$got" "Seguí: it's ñ" "sq conserva acentos y comillas"

# Instalador en un HOME vacío, sin terminal: no toca tmux.conf ni settings.json
H="$TMP/home"; mkdir -p "$H/.claude"; echo '{"theme":"dark"}' >"$H/.claude/settings.json"
HOME="$H" BK_REPO="$ROOT" bash "$ROOT/install.sh" </dev/null >/dev/null 2>&1; check $? "install.sh termina bien"
[ -L "$H/.local/bin/bk" ]; check $? "enlaza ~/.local/bin/bk"
check_eq "$(jq -c .hooks "$H/.claude/settings.json")" null "sin terminal no registra el hook"
[ ! -f "$H/.tmux.conf" ]; check $? "sin terminal no toca tmux.conf"

# Con BK_YES=s registra el hook en los eventos y lo saca al desinstalar
rm -rf "$H/.bk-shell"
HOME="$H" BK_REPO="$ROOT" BK_YES=s bash "$ROOT/install.sh" </dev/null >/dev/null 2>&1
check_eq "$(jq '.hooks | keys | length' "$H/.claude/settings.json")" 12 "con BK_YES=s registra el hook en 12 eventos"
grep -q 'bk-shell/tmux/bk.conf' "$H/.tmux.conf"; check $? "con BK_YES=s carga bk.conf en tmux.conf"
HOME="$H" bash "$H/.bk-shell/install.sh" --uninstall >/dev/null 2>&1
check_eq "$(jq -c .hooks "$H/.claude/settings.json")" "{}" "desinstalar saca el hook"
grep -q 'bk-shell' "$H/.tmux.conf"; [ $? -ne 0 ]; check $? "desinstalar saca bk.conf de tmux.conf"
[ "$(jq -r .theme "$H/.claude/settings.json")" = dark ]; check $? "no toca el resto de settings.json"

finish
