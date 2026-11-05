#!/usr/bin/env bash
# bk account (bk-shell#16): cuentas por sesión que comparten las conversaciones. Todo en carpetas de prueba,
# con un claude falso y un tmux propio: TMUX apunta al socket de prueba (nunca al tuyo) y se lo mata por ese socket.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(mktemp -d)
SOCK="$TMP/tmux.sock"
trap 'env -u TMUX tmux -S "$SOCK" kill-server 2>/dev/null; rm -rf "$TMP"' EXIT

export BK_ACCOUNTS_DIR="$TMP/accounts" BK_CLAUDE_MAIN="$TMP/main/.claude" BK_CODEX_MAIN="$TMP/main/.codex" \
       BK_CLAUDE_JSON="$TMP/main/.claude.json" BK_TASKS_DIR="$TMP/tasks" BK_STATE_DIR="$TMP/state"
unset BK_ACCOUNT BK_ACCOUNT_CLAUDE BK_ACCOUNT_CODEX BK_FROM CLAUDE_CONFIG_DIR CODEX_HOME
mkdir -p "$BK_CLAUDE_MAIN/skills" "$BK_CODEX_MAIN" "$BK_STATE_DIR"
echo '{}' >"$BK_CLAUDE_MAIN/settings.json"; echo '# global' >"$BK_CLAUDE_MAIN/CLAUDE.md"
echo 'model = "x"' >"$BK_CODEX_MAIN/config.toml"
jq -n '{oauthAccount: {emailAddress: "a@b"}, userID: "u1", projects: {"/p": {hasTrustDialogAccepted: true}},
        mcpServers: {m: {command: "x"}}, theme: "dark"}' >"$BK_CLAUDE_JSON"
C="$ROOT/lib/accounts.py"

# --- lib/accounts.py ---
d=$(python3 "$C" new claude trabajo); check $? "new claude crea la cuenta"
check_eq "$d" "$BK_ACCOUNTS_DIR/trabajo" "la carpeta va en BK_ACCOUNTS_DIR/<nombre>"
check_eq "$(python3 "$C" provider trabajo)" claude "guarda el proveedor"
check_eq "$(readlink "$d/projects")" "$BK_CLAUDE_MAIN/projects" "projects/ apunta a las conversaciones de la principal"
[ -d "$BK_CLAUDE_MAIN/projects" ]; check $? "crea projects/ en la principal si faltaba"
check_eq "$(readlink "$d/settings.json")" "$BK_CLAUDE_MAIN/settings.json" "comparte settings.json"
check_eq "$(readlink "$d/skills")" "$BK_CLAUDE_MAIN/skills" "comparte skills"
[ ! -e "$d/plugins" ]; check $? "no enlaza lo que la principal no tiene"
check_eq "$(jq -c 'keys' "$d/.claude.json")" '["mcpServers","projects","theme"]' \
  ".claude.json nuevo: proyectos confiados, MCP y tema, sin la cuenta (oauthAccount, userID)"
check_eq "$(stat -c %a "$d/.claude.json" 2>/dev/null || stat -f %Lp "$d/.claude.json")" 600 ".claude.json solo para vos"
check_eq "$(python3 "$C" env trabajo)" "CLAUDE_CONFIG_DIR=$d" "env: CLAUDE_CONFIG_DIR de la cuenta"
check_eq "$(python3 "$C" env default)" "" "env de default: nada (la de siempre)"

mkdir -p "$BK_CLAUDE_MAIN/plugins"; rm "$d/CLAUDE.md"; echo propio >"$d/CLAUDE.md"
python3 "$C" env trabajo >/dev/null
check_eq "$(readlink "$d/plugins")" "$BK_CLAUDE_MAIN/plugins" "env repara: lo nuevo de la principal se enlaza"
check_eq "$(cat "$d/CLAUDE.md")" propio "env nunca pisa un archivo real de la cuenta"

python3 "$C" new claude trabajo >/dev/null 2>&1; check $(( $? == 0 )) "no crea dos veces la misma cuenta"
python3 "$C" new claude default >/dev/null 2>&1; check $(( $? == 0 )) "'default' no es un nombre válido"
python3 "$C" new claude '../x' >/dev/null 2>&1; check $(( $? == 0 )) "no acepta rutas como nombre"
[ ! -e "$TMP/x" ]; check $? "un nombre con ../ no crea nada afuera"
python3 "$C" new gemini g >/dev/null 2>&1; check $(( $? == 0 )) "proveedor desconocido falla"
python3 "$C" env nada >/dev/null 2>&1; check $(( $? == 0 )) "env de una cuenta que no existe falla"

x=$(python3 "$C" new codex personal); check $? "new codex crea la cuenta"
check_eq "$(readlink "$x/sessions")" "$BK_CODEX_MAIN/sessions" "codex: sessions/ compartido"
check_eq "$(readlink "$x/config.toml")" "$BK_CODEX_MAIN/config.toml" "codex: config.toml compartido"
check_eq "$(python3 "$C" env personal)" "CODEX_HOME=$x" "codex: env es CODEX_HOME"

# --- bk: --account y la cuenta de la sesión llegan a lo que se lanza ---
FB="$TMP/bin"; mkdir -p "$FB"
# claude falso: anota con qué cuenta y argumentos lo lanzaron
cat >"$FB/claude" <<EOF
#!/bin/sh
echo "\${CLAUDE_CONFIG_DIR:-default} \$*" >>"$TMP/claude.log"
if [ "\$1" = auth ]; then echo '{"loggedIn": false}'; exit 0; fi
exec sleep 300
EOF
chmod +x "$FB/claude"
export PATH="$FB:$PATH"
P="$TMP/proj"; mkdir -p "$P/scripts/agents"; touch "$P/scripts/agents/guardrails.py"; git init -q "$P"

plan() { (cd "$P" && "$ROOT/bin/bk" --dry-run "$@" plan "idea" 2>&1); }
grep -q "cuenta: trabajo" <<<"$(plan --account trabajo)"; check $? "bk … --account X: el lanzamiento usa esa cuenta"
grep -q "cuenta:" <<<"$(cd "$P" && env -u TMUX "$ROOT/bin/bk" --dry-run plan idea 2>&1 </dev/null)"
check $(( $? == 0 )) "sin cuenta elegida: la principal (no muestra cuenta)"
grep -q "cuenta: trabajo" <<<"$(cd "$P" && env -u TMUX BK_ACCOUNT_CLAUDE=trabajo "$ROOT/bin/bk" --dry-run plan idea 2>&1)"
check $? "un agente lanza con la cuenta que heredó en su pane"
grep -q "cuenta:" <<<"$(cd "$P" && env -u TMUX BK_ACCOUNT_CLAUDE=trabajo "$ROOT/bin/bk" --dry-run --account default plan idea 2>&1)"
check $(( $? == 0 )) "--account default le gana a la heredada"
(cd "$P" && "$ROOT/bin/bk" --account nada plan idea >/dev/null 2>&1); check $(( $? == 0 )) "--account de una cuenta que no existe falla"
grep -q "cuenta:" <<<"$(plan --account personal)"; check $(( $? == 0 )) "una cuenta de codex no cambia a un agente de claude"

if command -v tmux >/dev/null; then
  env -u TMUX PATH="$PATH" tmux -S "$SOCK" -f /dev/null new-session -d -s bk-t -n main -x 120 -y 30 "sh"
  CHAT=$(env -u TMUX tmux -S "$SOCK" list-panes -t bk-t:main -F '#{pane_id}')
  env -u TMUX tmux -S "$SOCK" select-pane -t "$CHAT" -T "✿ chat"
  # Como si corrieras bk desde el chat de la sesión bk-t del tmux de prueba
  bkt() { (cd "$P" && TMUX="$SOCK,1,0" TMUX_PANE="$CHAT" BK_SESSION=agents-t "$ROOT/bin/bk" "$@"); }
  tm() { env -u TMUX tmux -S "$SOCK" "$@"; }

  bkt account use trabajo >/dev/null 2>&1; check $? "bk account use"
  check_eq "$(tm show-options -qv -t bk-t @bk_account_claude)" trabajo "la sesión guarda la cuenta"
  check_eq "$(tm show-environment -t bk-t CLAUDE_CONFIG_DIR)" "CLAUDE_CONFIG_DIR=$d" "los panes nuevos de la sesión la heredan"
  grep -q "cuenta: trabajo" <<<"$(bkt --dry-run plan idea 2>&1)"; check $? "lo que lances desde la sesión usa su cuenta"
  bkt account 2>&1 | grep -q "en bk-t: claude trabajo · codex default"; check $? "bk account muestra la de la sesión"

  # Un agente de verdad (con el claude falso): su pane tiene la cuenta y la sesión de agentes no la guarda
  bkt plan idea >/dev/null 2>&1; check $? "bk plan lanza con la cuenta de la sesión"
  sleep 0.5
  grep -q "^$d --model" "$TMP/claude.log"; check $? "el agente corre con CLAUDE_CONFIG_DIR de la cuenta"
  tm show-environment -t agents-t CLAUDE_CONFIG_DIR 2>/dev/null | grep -q "^CLAUDE_CONFIG_DIR="
  check $(( $? == 0 )) "la sesión de agentes no se queda con la cuenta (el próximo agente no la hereda)"

  # --relaunch: retoma la conversación del chat con la cuenta nueva; no si está trabajando
  jq -n --arg p "$CHAT" '{state: "working", pane: $p, ts: 1}' >"$BK_STATE_DIR/sid-123.json"
  bkt account use trabajo --relaunch >/dev/null 2>&1; check $(( $? == 0 )) "--relaunch no corta un chat que está trabajando"
  jq -n --arg p "$CHAT" '{state: "done", pane: $p, ts: 2}' >"$BK_STATE_DIR/sid-123.json"
  : >"$TMP/claude.log"
  bkt account use trabajo --relaunch >"$TMP/rel.out" 2>&1; rc=$?; cat "$TMP/rel.out" >&2; check $rc "--relaunch con el chat libre"
  sleep 0.5
  check_eq "$(cat "$TMP/claude.log")" "$d --resume sid-123" "el chat retoma su conversación con la cuenta nueva"

  bkt account use default >/dev/null 2>&1
  check_eq "$(tm show-options -qv -t bk-t @bk_account_claude)" "" "use default: la sesión vuelve a la de siempre"
  tm show-environment -t bk-t CLAUDE_CONFIG_DIR 2>/dev/null | grep -q "^CLAUDE_CONFIG_DIR="
  check $(( $? == 0 )) "use default saca CLAUDE_CONFIG_DIR del entorno de la sesión"
  grep -q "cuenta:" <<<"$(bkt --dry-run plan idea 2>&1)"; check $(( $? == 0 )) "y lo que se lanza vuelve a la principal"
  bkt account use nada >/dev/null 2>&1; check $(( $? == 0 )) "usar una cuenta que no existe falla"
else
  echo "# SKIP sin tmux"
fi
finish
