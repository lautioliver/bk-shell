#!/usr/bin/env bash
# lib/context_hook.py: el aviso de contexto al agente (UserPromptSubmit y PostToolUse). Transcripts y estado de
# prueba en un directorio temporal (también HOME): no toca ~/.claude ni ~/.cache.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
export BK_CONTEXT_HOOK_STATE="$TMP/state"
export HOME="$TMP/home"; mkdir -p "$HOME"; unset TMUX_PANE   # por si algo cae al default: el tmp, no ~/.cache
export PYTHONDONTWRITEBYTECODE=1

T="$TMP/t.jsonl"; : >"$T"
turn() {  # cache_read: el contexto del último turno
  printf '{"type":"assistant","message":{"id":"m%s","model":"claude-opus-5-5","usage":{"input_tokens":0,"output_tokens":5,"cache_creation_input_tokens":0,"cache_read_input_tokens":%s}}}\n' "$RANDOM" "$1" >>"$T"
}
# hook [evento] [sid] [extra json]: salida del hook (additionalContext, o "-" si no imprimió nada) y código
hook() {
  local out rc
  out=$(printf '{"session_id":"%s","transcript_path":"%s","hook_event_name":"%s"%s}' \
        "${2:-s1}" "$T" "${1:-UserPromptSubmit}" "${3:-}" | python3 "$ROOT/lib/context_hook.py"); rc=$?
  [ -z "$out" ] && { echo "- rc=$rc"; return; }
  python3 -c 'import json,sys; o=json.loads(sys.argv[1])["hookSpecificOutput"]; print(o["hookEventName"], o["additionalContext"].split(":")[0].split(" y ")[0])' "$out"
}

turn 199999
check_eq "$(hook)" "- rc=0" "abajo de 200k: nada"
turn 200000
check_eq "$(hook)" "UserPromptSubmit Llevás 200k de contexto" "cruza 200k: aviso ámbar en additionalContext"
check_eq "$(printf '{"session_id":"s1","transcript_path":"%s","hook_event_name":"UserPromptSubmit"}' "$T" | python3 "$ROOT/lib/context_hook.py" | wc -c | tr -d ' ')" "0" "no repite en el turno siguiente"
turn 350000
check_eq "$(hook PostToolUse)" "- rc=0" "sigue ámbar (350k): no repite, tampoco por PostToolUse"
turn 400000
check_eq "$(hook PostToolUse)" "PostToolUse Llevás 400k de contexto" "cruza 400k: aviso rojo por PostToolUse"
RED=$(printf '{"session_id":"s9","transcript_path":"%s","hook_event_name":"UserPromptSubmit"}' "$T" | python3 "$ROOT/lib/context_hook.py")
case "$RED" in *BK_TASK_DIR*'bk done'*) r=0 ;; *) r=1 ;; esac; check $r "el rojo nombra BK_TASK_DIR y bk done"
case "$RED" in *resumen*) r=0 ;; *) r=1 ;; esac; check $r "el rojo pide un resumen para seguir en otra sesión"
turn 499999
check_eq "$(hook)" "- rc=0" "rojo hasta 499k: no repite"
turn 500000
check_eq "$(hook)" "UserPromptSubmit Llevás 500k de contexto" "+100k (500k): vuelve a avisar"
turn 600001
check_eq "$(hook)" "UserPromptSubmit Llevás 600k de contexto" "+100k (600k): vuelve a avisar"
check_eq "$(hook UserPromptSubmit s2)" "UserPromptSubmit Llevás 600k de contexto" "otra sesión tiene su propio estado"
check_eq "$(hook PostToolUse s3 ',"agent_id":"a1"')" "- rc=0" "dentro de un subagente: nada"
check_eq "$(hook SessionStart s4)" "- rc=0" "otro evento: nada"
turn 150000
check_eq "$(hook)" "- rc=0" "baja (compact) a 150k: nada"
turn 210000
check_eq "$(hook)" "UserPromptSubmit Llevás 210k de contexto" "después de bajar, vuelve a avisar al cruzar 200k"

# Errores: siempre salida vacía y código 0
check_eq "$(echo 'no es json' | python3 "$ROOT/lib/context_hook.py"; echo "rc=$?")" "rc=0" "stdin que no es JSON: nada, código 0"
check_eq "$(printf '{"session_id":"s5","transcript_path":"%s/no-existe","hook_event_name":"UserPromptSubmit"}' "$TMP" | python3 "$ROOT/lib/context_hook.py"; echo "rc=$?")" "rc=0" "transcript que no existe: nada, código 0"
: >"$TMP/ro"; check_eq "$(BK_CONTEXT_HOOK_STATE="$TMP/ro/x" hook UserPromptSubmit s6)" "- rc=0" "no puede escribir el estado: nada, código 0"

check_eq "$(ls "$BK_CONTEXT_HOOK_STATE" | sort | tr '\n' ' ')" "s1 s2 s9 " "el estado queda en BK_CONTEXT_HOOK_STATE, un archivo por sesión"
check_eq "$(ls -A "$HOME")" "" "no escribe nada en HOME"
finish
