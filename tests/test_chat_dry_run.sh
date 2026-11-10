#!/usr/bin/env bash
# bk --dry-run chat / ws: dice qué abriría y no abre nada; y mem_ok: sin memoria libre, launch no lanza.
# bk ve un tmux FALSO en el PATH que anota lo que le piden y falla. HOME y BK_STATE_DIR van a un tmp.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(cd "$(mktemp -d)" && pwd -P); trap 'rm -rf "$TMP"' EXIT
unset TMUX TMUX_PANE BK_ACCOUNT BK_ACCOUNT_CLAUDE BK_ACCOUNT_CODEX BK_FROM BK_WS_SESSION
export HOME="$TMP/home" BK_STATE_DIR="$TMP/state" BK_CONFIG_DIR="$TMP/home/.config/bk"; mkdir -p "$HOME"
mkdir -p "$TMP/fake"; printf '#!/bin/sh\necho "$*" >> "%s/tmux.log"\nexit 1\n' "$TMP" >"$TMP/fake/tmux"; chmod +x "$TMP/fake/tmux"
REPO="$TMP/proj"; git init -q "$REPO"; git -C "$REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
# run <args>: salida de bk (sin colores) desde el repo; created: cuántas cosas intentó crear en tmux
run() { : >"$TMP/tmux.log"; (cd "$REPO" && PATH="$TMP/fake:$PATH" "$ROOT/bin/bk" --dry-run "$@" 2>&1) | sed 's/\x1b\[[0-9;]*m//g'; }
created() { grep -c -E '^(new-session|new-window|split-window|switch-client|attach)' "$TMP/tmux.log"; }

check_eq "$(run chat)" "(dry-run: abriría el workspace bk-proj-2 en $REPO)" "bk chat: dice qué workspace abriría"
check_eq "$(created)" "0" "bk chat: no abre nada en tmux"
check_eq "$(run chat --rama 'Prueba ñ')" "(dry-run: crearía la rama chat/prueba-n en $TMP/proj-wt/chat-prueba-n)
(dry-run: abriría el workspace bk-proj-prueba-n en $TMP/proj-wt/chat-prueba-n)" "bk chat --rama: dice qué rama y workspace"
check_eq "$(created)" "0" "bk chat --rama: no abre nada en tmux"
check_eq "$(find "$TMP" -maxdepth 1 -name 'proj-wt' | wc -l | tr -d ' ')" "0" "bk chat --rama: no crea el worktree"
check_eq "$(git -C "$REPO" branch --list 'chat/*' | wc -l | tr -d ' ')" "0" "bk chat --rama: no crea la rama"
check_eq "$(run chat --rama '  ' | tail -1)" "bk: bk chat --rama <nombre del cambio>" "--rama en blanco sigue fallando antes del dry-run"
check_eq "$(run chat --suelto)" "(dry-run: abriría un chat sin proyecto en $HOME/.bk/chats)" "bk chat --suelto: dice dónde"
check_eq "$(created)" "0" "bk chat --suelto: no abre nada en tmux"
check_eq "$([ -d "$HOME/.bk/chats" ] && echo sí || echo no)" "no" "bk chat --suelto: no crea la carpeta"
check_eq "$(run ws "$REPO")" "(dry-run: abriría el workspace bk-proj en $REPO)" "bk ws: dice qué workspace"
check_eq "$(created)" "0" "bk ws: no abre nada en tmux"
check_eq "$(run)" "(dry-run: abriría el workspace bk-proj en $REPO)" "bk (sin comando): dice qué workspace"

# --- mem_ok: un memory_pressure falso dice cuánta memoria libre queda ---
mkdir -p "$REPO/scripts/agents"; : >"$REPO/scripts/agents/guardrails.py"
printf '#!/bin/sh\necho "System-wide memory free percentage: $FAKE_FREE%%"\n' >"$TMP/fake/memory_pressure"
chmod +x "$TMP/fake/memory_pressure"
# tmux falso que "crea" la ventana del agente: new-window/new-session responden un pane
cat >"$TMP/fake/tmux" <<SH
#!/bin/sh
echo "\$*" >>"$TMP/tmux.log"
case "\$1" in new-session|new-window) echo "%99" ;; set-option) ;; *) exit 1 ;; esac
SH
lanza() { : >"$TMP/tmux.log"; (cd "$REPO" && env PATH="$TMP/fake:$PATH" "$@" "$ROOT/bin/bk" review 7 2>&1); }
out=$(lanza FAKE_FREE=5 BK_FORCE=0); rc=$?
check $(( rc == 0 )) "con 5% de memoria libre, bk review no lanza"
check_eq "$(grep -c 'queda 5% de memoria libre' <<<"$out")" "1" "y dice cuánta queda y cómo forzarlo"
check_eq "$(created)" "0" "y no crea ninguna ventana"
lanza FAKE_FREE=5 BK_FORCE=1 >/dev/null; check $? "BK_FORCE=1 lanza igual"
check_eq "$(created)" "1" "BK_FORCE=1: crea la ventana del agente"
lanza FAKE_FREE=5 BK_FORCE=0 BK_MEM_MIN=3 >/dev/null; check $? "BK_MEM_MIN=3: con 5% libre lanza"
lanza FAKE_FREE=40 BK_FORCE=0 >/dev/null; check $? "con 40% libre lanza"
printf '#!/bin/sh\nexit 1\n' >"$TMP/fake/memory_pressure"
lanza BK_FORCE=0 >/dev/null; check $? "si memory_pressure no responde: no frena"
finish
