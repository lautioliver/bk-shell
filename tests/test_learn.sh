#!/usr/bin/env bash
# bk learn / lessons / learn effect / metrics: sin BARRILETE_VAULT explican qué poner y salen con error;
# con un vault de prueba, learn registra desvíos de transcripts falsos, propone una lección y arma INDEX.md.
# Todo en tmp: HOME, BK_STATE_DIR, BK_CONFIG_DIR y el vault; nunca ~/.claude ni el vault de verdad.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
# shellcheck source=tests/lib.sh
. "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
export HOME="$TMP/home" BK_STATE_DIR="$TMP/state" BK_CONFIG_DIR="$TMP/config" BK_LEARN_STATE="$TMP/learn-state.json"
mkdir -p "$HOME" "$BK_STATE_DIR" "$BK_CONFIG_DIR"
unset BARRILETE_VAULT BK_VAULT
BK="$ROOT/bin/bk"

# --- sin vault: error claro, sin tocar nada ---
for c in learn "learn effect" lessons metrics; do
  # shellcheck disable=SC2086
  out=$(env -u TMUX -u TMUX_PANE "$BK" $c 2>&1); rc=$?
  check "$([ "$rc" -ne 0 ] && echo 0 || echo 1)" "bk $c sin BARRILETE_VAULT sale con error"
  case "$out" in *"export BARRILETE_VAULT="*) r=0 ;; *) r=1 ;; esac
  check $r "bk $c sin vault dice qué variable poner"
done
for py in learn effect metrics; do
  out=$(python3 "$ROOT/lib/$py.py" 2>&1); rc=$?
  case "$out" in *"export BARRILETE_VAULT="*) r=$rc ;; *) r=0 ;; esac
  check "$([ "$r" -ne 0 ] && echo 0 || echo 1)" "lib/$py.py sin vault: error que nombra BARRILETE_VAULT"
done
check_eq "$(ls -A "$HOME")" "" "sin vault no crea nada en HOME"

# checkpoints y monitor sin vault: no hay lecciones ni vault, ni rutas relativas a la carpeta actual
mkdir -p "$TMP/cwd"; printf -- '---\nstatus: proposed\n---\n' >"$TMP/cwd/suelta.md"
n=$(cd "$TMP/cwd" && python3 -c "import sys; sys.path.insert(0, '$ROOT/lib'); import checkpoints as C, monitor as M; print(len(C.lessons()), repr(M.VAULT_DATA), M.week_average())")
check_eq "$n" "0 '' 0" "sin vault: checkpoints no lista lecciones y monitor no anota en ningún lado"

# --- BK_VAULT, el nombre viejo, también vale ---
mkdir -p "$TMP/viejo/agents/lessons"
out=$(env -u TMUX -u TMUX_PANE BK_VAULT="$TMP/viejo" "$BK" lessons 2>&1)
check_eq "$out" "no hay lecciones por revisar" "BK_VAULT sirve como alias de BARRILETE_VAULT"

# --- bk learn con un vault y transcripts de prueba ---
export BARRILETE_VAULT="$TMP/vault"; mkdir -p "$BARRILETE_VAULT"
P="$HOME/.claude/projects/demo"; mkdir -p "$P"
echo '{"role":"implementer"}' >"$BK_STATE_DIR/aaaa1111-0000-0000-0000-000000000000.json"
python3 - "$P/aaaa1111.jsonl" <<'PY'
import json, sys
from datetime import datetime, timezone
ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
sid = "aaaa1111-0000-0000-0000-000000000000"
base = {"sessionId": sid, "cwd": "/home/demo/proyecto", "timestamp": ts}
rows = []
for i in range(3):
    rows.append({**base, "type": "assistant", "message": {"model": "claude-demo-1", "content": [
        {"type": "tool_use", "id": f"toolu_{i}", "name": "Bash", "input": {"command": "git push origin main"}}]}})
    rows.append({**base, "type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": f"toolu_{i}", "is_error": True,
         "content": "PreToolUse:Bash hook error: [baranda]: push directo a main"}]}})
with open(sys.argv[1], "w") as f:
    for r in rows:
        f.write(json.dumps(r, separators=(",", ":")) + "\n")
PY
out=$(env -u TMUX -u TMUX_PANE "$BK" learn 2>&1); rc=$?
check $rc "bk learn corre con un vault de prueba"
case "$out" in "3 desvíos nuevos"*) r=0 ;; *) r=1 ;; esac
check $r "bk learn cuenta los 3 desvíos del transcript ($out)"
ev=$(cat "$BARRILETE_VAULT"/agents/data/events-*.jsonl 2>/dev/null | jq -rs '[.[] | .type + "/" + .role] | unique | join(",")')
check_eq "$ev" "guardrail_block/implementer" "los eventos van al vault con el rol que leyó de BK_STATE_DIR"
L=$(ls "$BARRILETE_VAULT"/agents/lessons/*.md 2>/dev/null | head -1)
check "$([ -n "$L" ] && grep -q '^status: proposed' "$L" && echo 0 || echo 1)" "3 veces la misma baranda: propone una lección"
out=$(env -u TMUX -u TMUX_PANE "$BK" lessons)
case "$out" in *"(3 veces)  proyecto"*"1 por revisar"*) r=0 ;; *) r=1 ;; esac
check $r "bk lessons la lista con sus veces y su scope"
check "$(grep -q 'Todavía no hay lecciones aceptadas' "$BARRILETE_VAULT/agents/INDEX.md" && echo 0 || echo 1)" "INDEX.md sin reglas mientras nada está accepted"
sed -i.bak 's/^status: proposed.*/status: accepted/' "$L" && rm -f "$L.bak"
env -u TMUX -u TMUX_PANE "$BK" learn --quiet >/dev/null 2>&1
check "$(grep -q '^- \[solo en proyecto\] No intentes' "$BARRILETE_VAULT/agents/INDEX.md" && echo 0 || echo 1)" "aceptada: su regla pasa a INDEX.md"
check_eq "$(cat "$BARRILETE_VAULT"/agents/data/events-*.jsonl | wc -l | tr -d ' ')" 3 "la segunda corrida no vuelve a contar lo ya leído"
check "$([ -f "$BK_LEARN_STATE" ] && echo 0 || echo 1)" "el estado va a BK_LEARN_STATE"
check_eq "$(ls -A "$HOME")" ".claude" "bk learn no escribe en HOME (solo leyó .claude/projects)"

# --- bk metrics con el vault: sin sesiones con número no hay PRs (no sale a GitHub) ---
out=$(env -u TMUX -u TMUX_PANE "$BK" metrics --json 2>&1); rc=$?
check_eq "$rc/$(jq -r '.prs | length' <<<"$out" 2>/dev/null)" "0/0" "bk metrics --json con el vault y sin PRs"

finish
