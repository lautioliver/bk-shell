#!/usr/bin/env bash
# bk dash --once: la tabla de agentes sale de BK_STATE_DIR (no de otra carpeta) y la ventana, de tmux.
# tmux falso en el PATH (nunca el servidor real) y HOME en un tmp (usage.py no lee tus transcripts).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
export HOME="$TMP/home" BK_STATE_DIR="$TMP/state" BK_CONFIG_DIR="$TMP/conf" BK_CACHE_DIR="$TMP/cache"
mkdir -p "$TMP/bin" "$HOME/.cache/sketchybar/agents" "$BK_STATE_DIR"
cat > "$TMP/bin/tmux" <<'EOF'
#!/bin/sh
case "$*" in *list-panes*) echo "%7 i42" ;; esac
exit 0
EOF
chmod +x "$TMP/bin/tmux"
NOW=$(date +%s)
printf '{"state":"waiting","pane":"%%7","ts":%s,"cwd":"/tmp/mi-proyecto-wt/i42","role":"implementer","model":"claude-sonnet-5-5"}\n' "$NOW" > "$BK_STATE_DIR/a.json"
printf '{"state":"working","ts":%s,"cwd":"/tmp/otra-carpeta","role":"reviewer"}\n' "$NOW" > "$HOME/.cache/sketchybar/agents/b.json"

OUT=$(env -u TMUX -u TMUX_PANE PATH="$TMP/bin:$PATH" "$ROOT/bin/bk" dash --once 2>&1 | sed $'s/\033\\[[0-9;?]*[A-Za-z]//g')
grep -q "i42.*mi-proyecto#i42.*implementer.*sonnet 5.5.*! te espera" <<<"$OUT"; check $? "bk dash: el agente de BK_STATE_DIR, con su ventana, proyecto, rol, modelo y estado"
grep -q "otra-carpeta" <<<"$OUT"; [ $? -ne 0 ]; check $? "bk dash: no lee ~/.cache/sketchybar/agents"
grep -q "uso de hoy" <<<"$OUT"; check $? "bk dash: la tabla del uso del día"

rm -f "$BK_STATE_DIR/a.json"
OUT=$(env -u TMUX -u TMUX_PANE PATH="$TMP/bin:$PATH" "$ROOT/bin/bk" dash --once 2>&1)
grep -q "sin agentes" <<<"$OUT"; check $? "bk dash: sin estado, 'sin agentes'"
finish
