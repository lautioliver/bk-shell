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

# bk mode <rol>: la 3.ª columna de roles.yml (el del proyecto manda), bk si no tiene; un rol que no existe falla.
# role_spec sigue devolviendo solo provider/modelo.
P="$TMP/proj"; mkdir -p "$P/.barrilete"; git init -q "$P"
printf 'planner: claude/opus sub\nreviewer: claude/sonnet   # sin modo\nimplementer: codex/gpt-5 bk # comentario\n' >"$P/.barrilete/roles.yml"
check_eq "$(cd "$P" && "$ROOT/bin/bk" mode planner)" sub "bk mode: el modo del roles.yml del proyecto"
check_eq "$(cd "$P" && "$ROOT/bin/bk" mode reviewer)" bk "bk mode: sin modo es bk"
check_eq "$(cd "$P" && "$ROOT/bin/bk" mode implementer)" bk "bk mode: modo seguido de un comentario"
check_eq "$(cd "$TMP" && "$ROOT/bin/bk" mode orchestrator)" main "bk mode: cae en el roles.yml de bk"
(cd "$P" && "$ROOT/bin/bk" mode no-existe >/dev/null 2>&1); check $(( $? == 0 )) "bk mode: un rol desconocido falla"
(cd "$P" && "$ROOT/bin/bk" mode '.*' >/dev/null 2>&1); check $(( $? == 0 )) "bk mode: no acepta regex como rol"
check_eq "$(cd "$P" && "$ROOT/bin/bk" roles | awk '$1 == "planner" {print $2, $3}')" "claude/opus sub" "bk roles: provider/modelo y modo por separado"

# bk chat --rama: sin nombre, con un nombre en blanco o fuera de un repo git falla con un mensaje claro y no
# crea nada (ni worktree, ni rama, ni sesión). tmux en un servidor propio (TMUX_TMPDIR), nunca el tuyo.
if command -v tmux >/dev/null; then
  T="$TMP/tmux"; mkdir -p "$T" "$TMP/nogit"
  git init -q -b main "$TMP/repo" && git -C "$TMP/repo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
  bkt() { env -u TMUX TMUX_TMPDIR="$T" BK_SESSION="bk-test-agents-$$" BK_CLIENT=nocliente BK_STATE_DIR="$TMP/state" "$ROOT/bin/bk" "$@"; }
  sess="zz-bk-test-$$"
  TMUX_TMPDIR="$T" tmux -f /dev/null new-session -d -s "$sess" "sleep 300"
  nsess() { TMUX_TMPDIR="$T" tmux list-sessions 2>/dev/null | wc -l | tr -d ' '; }
  before=$(nsess)
  out=$(cd "$TMP/repo" && bkt chat --rama 2>&1 </dev/null); rc=$?
  [ $rc -ne 0 ] && grep -q 'bk chat --rama <nombre' <<<"$out"; check $? "chat --rama sin nombre falla con el uso"
  out=$(cd "$TMP/repo" && bkt chat --rama "   " "$sess" 2>&1 </dev/null); rc=$?
  [ $rc -ne 0 ] && grep -q 'bk chat --rama <nombre' <<<"$out"; check $? "chat --rama con nombre en blanco falla"
  out=$(cd "$TMP/repo" && bkt chat --rama "¡¿~~?!" "$sess" 2>&1 </dev/null); rc=$?
  [ $rc -ne 0 ] && grep -q 'no deja un nombre de rama' <<<"$out"; check $? "chat --rama con un nombre sin letras ni números falla"
  out=$(cd "$TMP/nogit" && bkt chat --rama algo "$sess" 2>&1 </dev/null); rc=$?
  [ $rc -ne 0 ] && grep -q 'necesita un repo git' <<<"$out"; check $? "chat --rama fuera de un repo git falla"
  check_eq "$(git -C "$TMP/repo" branch --list 'chat/*')" "" "chat --rama fallido no crea ramas"
  [ ! -e "$TMP/repo-wt" ] && [ ! -e "$TMP/nogit-wt" ]; check $? "chat --rama fallido no crea worktrees"
  check_eq "$(nsess)" "$before" "chat --rama fallido no crea sesiones de tmux"
  TMUX_TMPDIR="$T" tmux kill-session -t "=$sess" 2>/dev/null
fi

finish
