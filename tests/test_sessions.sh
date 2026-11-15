#!/usr/bin/env bash
# bk s (lib/sessions.py): el número del workspace en cada chat, qué cierra x y los estados fantasma; y el chat de un
# workspace: volver a entrar lo repone (ws_chat_pane) y has-session busca el nombre exacto. Lo de tmux corre en un
# servidor propio (-S en un tmp, que se mata por ese socket); HOME y BK_STATE_DIR van a un tmp.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(cd "$(mktemp -d)" && pwd -P)
SOCK="$TMP/tmux.sock"
trap 'env -u TMUX tmux -S "$SOCK" kill-server 2>/dev/null; rm -rf "$TMP"' EXIT
unset TMUX_PANE BK_FROM BK_WS_SESSION BK_ACCOUNT BK_ACCOUNT_CLAUDE BK_ACCOUNT_CODEX CLAUDE_CONFIG_DIR
export HOME="$TMP/home" BK_STATE_DIR="$TMP/state" BK_CONFIG_DIR="$TMP/home/.config/bk"; mkdir -p "$HOME" "$BK_STATE_DIR"

py() { env -u TMUX python3 - "$ROOT/lib" "$@" <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import sessions as S
cmd, a = sys.argv[2], sys.argv[3:]
if cmd == "where":   # sesión ventana wsid
    print(S.where({"session": a[0], "window": a[1], "wsid": a[2] if len(a) > 2 else ""}))
elif cmd == "close":   # sesión-de-la-fila sesión-actual
    it = {"session": a[0] or None, "target": f"{a[0]}:1", "pane": "%1"}
    print(*S.close_action(it, a[1] or None))
elif cmd == "load":   # panes vivos (separados por coma): qué estados quedan abiertos
    S.tmux_panes = lambda: {p: {"session": "agents", "target": "agents:1", "window": "w", "dead": False, "wsid": ""}
                            for p in a[0].split(",") if p}
    S.PROJECTS = "/nonexistent"
    o, _ = S.load()
    print(",".join(sorted(r["sid"] for r in o)))
PY
}
check_eq "$(py where bk-demo main 3)" "ws 3" "chat de un workspace: su número fijo"
check_eq "$(py where agents i7)" "i7" "agente: su ventana"
check_eq "$(py where term-demo zsh)" "term-demo:zsh" "otra sesión sin número: sesión y ventana"
check_eq "$(py close agents bk-x)" "window agents:1" "x sobre un agente: cierra su ventana"
check_eq "$(py close bk-demo bk-otro)" "session bk-demo" "x sobre otro workspace: cierra el workspace (con confirmación)"
check_eq "$(py close bk-otro bk-otro | cut -d' ' -f1)" "error" "x sobre el workspace donde estás: no"
check_eq "$(py close term-demo bk-otro | cut -d' ' -f1)" "error" "x sobre otra sesión de tmux: no"

# Estados: uno en un pane vivo, otro en un pane que ya no existe (fantasma), otro sin pane
echo '{"state":"working","pane":"%1","ts":1}' >"$BK_STATE_DIR/vivo.json"
echo '{"state":"working","pane":"%9","ts":1}' >"$BK_STATE_DIR/fantasma.json"
echo '{"state":"done","ts":1}' >"$BK_STATE_DIR/sinpane.json"
check_eq "$(py load %1)" "sinpane,vivo" "un estado cuyo pane ya no existe no se muestra como abierto"
check_eq "$([ -e "$BK_STATE_DIR/fantasma.json" ] && echo queda || echo borrado)" "borrado" "y su archivo de estado se borra"
echo '{"state":"working","pane":"%9","ts":1}' >"$BK_STATE_DIR/fantasma.json"
check_eq "$(py load '')" "fantasma,sinpane,vivo" "sin tmux (ningún pane) no borra nada"

if command -v tmux >/dev/null; then
  FB="$TMP/bin"; mkdir -p "$FB"
  # claude y bk falsos: los panes del workspace (chat, "bk rail", "bk sidebar") no corren nada de verdad
  printf '#!/bin/sh\nexec sleep 300\n' >"$FB/claude"; cp "$FB/claude" "$FB/bk"; chmod +x "$FB/claude" "$FB/bk"
  export PATH="$FB:$PATH"
  tm() { env -u TMUX tmux -S "$SOCK" "$@"; }
  P="$TMP/demo"; mkdir -p "$P"
  # Un workspace a mano: chat, riel y tira (el comando de cada pane lleva "bk rail"/"bk sidebar" como comentario)
  tm -f /dev/null new-session -d -s bk-demo -n main -x 160 -y 40 -c "$P" "exec sleep 300"
  tm set-environment -t bk-demo BK_PROJECT "$P"
  tm split-window -d -h -t bk-demo:main "exec sleep 300 # bk rail"
  tm split-window -d -hb -t bk-demo:main.0 "exec sleep 300 # bk sidebar"
  bks() { (cd "$P" && TMUX="$SOCK,1,0" BK_SESSION=agents-t "$ROOT/bin/bk" "$@" >/dev/null 2>&1); }
  chat=$(tm list-panes -t bk-demo:main -F '#{pane_id} #{pane_start_command}' | grep -v -e 'bk rail' -e 'bk sidebar' | cut -d' ' -f1)
  tm kill-pane -t "$chat"
  check_eq "$(tm list-panes -t bk-demo:main | wc -l | tr -d ' ')" "2" "(el chat se cerró: quedan riel y tira)"
  BK_WS_SESSION=bk-demo bks ws "$P"
  check_eq "$(tm list-panes -t bk-demo:main | wc -l | tr -d ' ')" "3" "volver a entrar al workspace repone el chat"
  check_eq "$(tm list-panes -t bk-demo:main -F '#{pane_title}' | grep -c '✿ chat')" "1" "con el título del chat"
  check_eq "$(tm list-panes -t bk-demo:main -F '#{pane_start_command}' | head -1 | grep -c 'bk sidebar')" "1" \
    "la tira sigue a la izquierda del chat"
  . "$ROOT/lib/layout.conf"
  check_eq "$(tm list-panes -t bk-demo:main -F '#{pane_width} #{pane_start_command}' | grep 'bk sidebar' | cut -d' ' -f1)" \
    "$SIDE_W" "y vuelve a su ancho (bk _layout)"
  check_eq "$(tm list-panes -t bk-demo:main -F "#{pane_start_command}" | sed -n 2p | grep -c "claude; exec")" "1" \
    "y el chat nuevo corre claude, entre la tira y el riel"
  BK_WS_SESSION=bk-demo bks ws "$P"
  check_eq "$(tm list-panes -t bk-demo:main | wc -l | tr -d ' ')" "3" "con el chat en su lugar, volver a entrar no agrega nada"

  # has-session con "=": bk-otro-2 existe, pero eso no quiere decir que exista bk-otro
  tm new-session -d -s bk-otro-2 -c "$P" "exec sleep 300"
  BK_WS_SESSION=bk-otro bks ws "$P"
  tm has-session -t "=bk-otro" 2>/dev/null; check $? "bk-otro-2 no cuenta como bk-otro: el workspace se crea"
else
  echo "# SKIP sin tmux"
fi
finish
