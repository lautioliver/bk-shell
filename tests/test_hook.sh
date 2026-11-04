#!/usr/bin/env bash
# Tests del hook de estado (hooks/agent_state.sh): es lo que corre antes de cada herramienta de cada
# agente, así que un error acá frena todo. Cada caso le pasa el JSON de un evento de Claude Code y
# mira el archivo de estado que deja en un BK_STATE_DIR temporal.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
HOOK="$ROOT/hooks/agent_state.sh"
source "$HERE/lib.sh"

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
# Aislar del entorno de quien corre los tests (bk lanza agentes con estas variables)
unset BARRILETE_ROLE BARRILETE_MODEL BARRILETE_ISSUE BARRILETE_PROVIDER TMUX_PANE BK_TASK_DIR
export BK_STATE_DIR="$TMP/state"
export BK_SOUND_DRY=1 BK_CONFIG_DIR="$TMP/config"   # los sonidos se imprimen, no suenan
ev() { printf '%s' "$1" | bash "$HOOK" >/dev/null; }
snd() { printf '%s' "$1" | bash "$HOOK"; }   # lo que imprime: el sonido (con BK_SOUND_DRY)
field() { jq -r "$2 // \"-\"" "$BK_STATE_DIR/$1.json" 2>/dev/null; }

bash -n "$HOOK"; check $? "el hook no tiene errores de sintaxis"

ev '{"session_id":"s1","hook_event_name":"SessionStart","cwd":"/tmp/p"}'
check_eq "$(field s1 .state)" idle "SessionStart deja la sesión libre"

ev '{"session_id":"s1","hook_event_name":"UserPromptSubmit","cwd":"/tmp/p","prompt":"/implement-issue 42"}'
check_eq "$(field s1 .state)" working "un prompt pasa a trabajando"
check_eq "$(field s1 .role)" implementer "/implement-issue marca el rol implementer"
check_eq "$(field s1 .ref)" "#42" "/implement-issue 42 marca el issue"

ev '{"session_id":"s1","hook_event_name":"PermissionRequest","tool_name":"Bash","cwd":"/tmp/p"}'
check_eq "$(field s1 .state)" waiting "un permiso pasa a te espera"
check_eq "$(field s1 .wait)" permission "un permiso de Bash es 'permission'"

ev '{"session_id":"s1","hook_event_name":"PostToolUse","tool_name":"Bash","cwd":"/tmp/p","tool_input":{"command":"ls"}}'
check_eq "$(field s1 .state)" working "después de la herramienta vuelve a trabajando"
check_eq "$(field s1 .wait)" - "al seguir se borra qué esperaba"

ev '{"session_id":"s2","hook_event_name":"PermissionRequest","tool_name":"AskUserQuestion","cwd":"/tmp/p"}'
check_eq "$(field s2 .wait)" question "AskUserQuestion es 'question'"

ev '{"session_id":"s1","hook_event_name":"Stop","cwd":"/tmp/p"}'
check_eq "$(field s1 .state)" done "Stop sin nada en segundo plano es listo"

# planner.py: solo cuenta si el comando lo CORRE, no si lo nombra
ev '{"session_id":"s3","hook_event_name":"PostToolUse","tool_name":"Bash","cwd":"/tmp/p","tool_input":{"command":"python3 .claude/skills/plan-feature/planner.py --draft plan.json"}}'
check_eq "$(field s3 .phase)" plan_pending "planner.py --draft deja el plan esperando el sí"
ev '{"session_id":"s4","hook_event_name":"PostToolUse","tool_name":"Bash","cwd":"/tmp/p","tool_input":{"command":"grep -n planner.py --draft README.md"}}'
check_eq "$(field s4 .phase)" - "nombrar planner.py en un grep no es un plan"

# Segundo plano: un comando lanzado y sin aviso de fin, con su salida abierta por un proceso
SID=s5; TR="$TMP/$SID.jsonl"; OUT="$TMP/claude-x/proj/$SID/tasks"; mkdir -p "$OUT"
echo '{"type":"user","toolUseResult":{"backgroundTaskId":"bg1"}}' >"$TR"
sleep 30 >"$OUT/bg1.output" & HOLD=$!
sleep 0.3
BK_TASKS_GLOB="$TMP/claude-*/*" ev "{\"session_id\":\"$SID\",\"hook_event_name\":\"Stop\",\"cwd\":\"/tmp/p\",\"transcript_path\":\"$TR\"}"
kill $HOLD 2>/dev/null; wait $HOLD 2>/dev/null
check_eq "$(field $SID .state)" background "Stop con un comando vivo en 2º plano no es listo"
echo '{"type":"queue-operation","content":"<task-notification><task-id>bg1</task-id>"}' >>"$TR"
BK_TASKS_GLOB="$TMP/claude-*/*" ev "{\"session_id\":\"$SID\",\"hook_event_name\":\"Stop\",\"cwd\":\"/tmp/p\",\"transcript_path\":\"$TR\"}"
check_eq "$(field $SID .state)" done "cuando el comando avisa que terminó, es listo"

# Robustez: basura por stdin no rompe nada (sale con 0 y no imprime)
out=$(printf 'no es json' | bash "$HOOK"; echo "rc=$?")
check_eq "$out" "rc=0" "una entrada inválida no frena a Claude"

ev '{"session_id":"s1","hook_event_name":"SessionEnd","cwd":"/tmp/p"}'
[ ! -e "$BK_STATE_DIR/s1.json" ]; check $? "SessionEnd borra el estado"


# Sonidos: uno por tipo de pedido, solo cuando cambia el estado
check_eq "$(snd '{"session_id":"s9","hook_event_name":"UserPromptSubmit","cwd":"/tmp/p"}')" "" "trabajar no suena"
check_eq "$(snd '{"session_id":"s9","hook_event_name":"Stop","cwd":"/tmp/p"}')" done "terminar suena 'done'"
check_eq "$(snd '{"session_id":"s9","hook_event_name":"Stop","cwd":"/tmp/p"}')" "" "un segundo Stop no repite el sonido"
ev '{"session_id":"s9","hook_event_name":"UserPromptSubmit","cwd":"/tmp/p"}'
check_eq "$(snd '{"session_id":"s9","hook_event_name":"PermissionRequest","tool_name":"Bash","cwd":"/tmp/p"}')" permission "pedir permiso suena 'permission'"
ev '{"session_id":"s9","hook_event_name":"PostToolUse","tool_name":"Bash","cwd":"/tmp/p"}'
check_eq "$(snd '{"session_id":"s9","hook_event_name":"PermissionRequest","tool_name":"AskUserQuestion","cwd":"/tmp/p"}')" question "un cuestionario suena 'question'"
check_eq "$(snd '{"session_id":"s9","hook_event_name":"StopFailure","cwd":"/tmp/p"}')" error "un error suena 'error'"
mkdir -p "$TMP/config"; echo off > "$TMP/config/sound"
ev '{"session_id":"s9","hook_event_name":"UserPromptSubmit","cwd":"/tmp/p"}'
check_eq "$(snd '{"session_id":"s9","hook_event_name":"Stop","cwd":"/tmp/p"}')" "" "con bk sound off no suena"

# Con la radio de bk song sonando, el aviso la baja al 35% y después la deja como estaba;
# dos avisos juntos no guardan el volumen ya bajado como el original
if command -v nc >/dev/null; then
  SC="$TMP/songcache"; mkdir -p "$SC"
  python3 "$HERE/fake_mpv.py" "$SC/song.sock" "$TMP/vols" 80 & FAKE=$!
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -S "$SC/song.sock" ] && break; sleep 0.1; done
  ( unset BK_SOUND_DRY; export BK_SONG_CACHE="$SC" BK_SOUND_PLAYER="sleep 0.4" BK_CONFIG_DIR="$TMP/config-on"
    bash "$ROOT/lib/agent_sound.sh" working done ""; bash "$ROOT/lib/agent_sound.sh" working waiting question )
  for _ in $(seq 1 30); do [ "$( { wc -l < "$TMP/vols"; } 2>/dev/null | tr -d ' ')" = 2 ] && [ ! -d "$SC/song.duck" ] && break; sleep 0.1; done
  check_eq "$(tr '\n' ' ' < "$TMP/vols" 2>/dev/null)" "28 80 " "un aviso baja la radio al 35% y la vuelve a 80"
  [ ! -d "$SC/song.duck" ]; check $? "el candado de la radio se libera"
  kill "$FAKE" 2>/dev/null; wait "$FAKE" 2>/dev/null
fi

finish
