#!/bin/bash
# Hook de Claude Code -> estado de cada sesión para bk shell (riel, tira, footer, bk ls, bk s).
# Lee el JSON del hook por stdin y actualiza $BK_STATE_DIR/<session>.json (~/.cache/bk/agents).
# Si hay SketchyBar, además dispara `agent_update`. Nunca bloquea ni imprime nada (no altera a Claude).
#
# Además del estado, registra el contexto Barrilete de la sesión:
#   phase  plan | plan_pending | plan_done | implement | review | orchestrate
#          (plan_pending = `planner.py --draft` mostró el plan y espera tu "sí")
#   ref    issue/PR (#N): de /implement-issue N, /review-pr N, $BARRILETE_ISSUE o la rama
#   role   planner | implementer | reviewer | orchestrator, o $BARRILETE_ROLE
#   model  $BARRILETE_MODEL, o el último modelo que respondió (transcript)
#   pane   $TMUX_PANE si la sesión corre dentro de tmux

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
DIR="${BK_STATE_DIR:-$HOME/.cache/bk/agents}"
mkdir -p "$DIR" 2>/dev/null || exit 0

INPUT=$(cat)
# Todos los campos con placeholder "-": con IFS=tab, bash colapsa campos vacíos.
IFS=$'\t' read -r SID EVENT CWD TOOL NTYPE PCMD PARG PLANNER TRANSCRIPT < <(
  printf '%s' "$INPUT" | jq -r '
    def nz: if . == null or . == "" then "-" else tostring end;
    ((.prompt // "") | [capture("^\\s*/(?:[\\w-]+:)?(?<c>[a-z-]+)(?:\\s+#?(?<n>[0-9]+))?")] | .[0] // {}) as $p
    | (if .tool_name == "Bash" and .hook_event_name == "PostToolUse"
         # Solo si el comando CORRE planner.py (al principio o después de &&, ;, |), no si lo nombra
         # en un texto, un grep o un heredoc: eso dejaba sesiones como "plan esperando tu sí" sin plan.
         and ((.tool_input.command // "") | test("(^|&&|;|\\|)\\s*(python3?\\s+)?[^\\s\u0027\"]*planner\\.py(\\s|$)"))
       then (if (.tool_input.command | test("planner\\.py[^|;&\\n]*\\s--draft(\\s|$)")) then "draft" else "create" end)
       else "-" end) as $planner
    | [ (.session_id|nz), (.hook_event_name|nz), (.cwd|nz), (.tool_name|nz),
        (.notification_type|nz), ($p.c|nz), ($p.n|nz), $planner, (.transcript_path|nz) ] | @tsv' 2>/dev/null
)
[ -z "$SID" ] || [ "$SID" = "-" ] && exit 0
F="$DIR/$SID.json"

trigger() { sketchybar --trigger agent_update >/dev/null 2>&1 & }

# bk registra al agente antes de que Claude Code corra hooks (bk-<ventana>.json, "esperando
# confianza"). Cuando la sesión real arranca en ese mismo pane, ese registro previo sobra.
if [ -n "$TMUX_PANE" ] && { [ "$EVENT" = "SessionStart" ] || [ "$EVENT" = "UserPromptSubmit" ]; }; then
  grep -l "\"pane\": *\"$TMUX_PANE\"" "$DIR"/bk-*.json 2>/dev/null | xargs rm -f 2>/dev/null
fi

if [ "$EVENT" = "SessionEnd" ]; then
  rm -f "$F"; trigger; exit 0
fi

STATE=""; DELTA=0
case "$EVENT" in
  SessionStart)                     STATE="idle" ;;
  UserPromptSubmit)                 STATE="working" ;;
  PreToolUse|PostToolUse|PostToolUseFailure) STATE="working" ;;
  PermissionRequest)                STATE="waiting" ;;
  Notification)
    case "$NTYPE" in
      permission_prompt) STATE="waiting" ;;
      idle_prompt)       STATE="done" ;;
      *) exit 0 ;;
    esac ;;
  Stop)                             STATE="done" ;;
  StopFailure)                      STATE="error" ;;
  SubagentStart)                    STATE="working"; DELTA=1 ;;
  SubagentStop)                     DELTA=-1 ;;
  *) exit 0 ;;
esac

# ---- Contexto Barrilete ----
PHASE=""; ROLE=""; REF=""
if [ "$EVENT" = "UserPromptSubmit" ]; then
  case "$PCMD" in
    plan-feature)    PHASE="plan";        ROLE="planner";      REF="none" ;;
    implement-issue) PHASE="implement";   ROLE="implementer";  [ "$PARG" != "-" ] && REF="#$PARG" ;;
    review-pr)       PHASE="review";      ROLE="reviewer";     [ "$PARG" != "-" ] && REF="#$PARG" ;;
    orchestrate)     PHASE="orchestrate"; ROLE="orchestrator" ;;
  esac
fi
case "$PLANNER" in
  draft)  PHASE="plan_pending" ;;
  create) PHASE="plan_done" ;;
esac

# Lo que lanza el runner manda sobre lo inferido
[ -n "$BARRILETE_ROLE" ]  && ROLE="$BARRILETE_ROLE"
[ -n "$BARRILETE_ISSUE" ] && REF="#${BARRILETE_ISSUE#\#}"

# Issue desde la rama (p. ej. feat/42-algo) solo al empezar, si nada lo dio
if [ "$EVENT" = "SessionStart" ] && [ -z "$REF" ] && [ "$CWD" != "-" ]; then
  BR=$(git -C "$CWD" branch --show-current 2>/dev/null)
  N=$(printf '%s' "$BR" | sed -nE 's#^[^/]+/([0-9]+)([-_].*)?$#\1#p')
  [ -n "$N" ] && REF="#$N"
fi

# Lock simple para eventos concurrentes (herramientas en paralelo)
LOCK="$F.lock"
for _ in 1 2 3 4 5 6 7 8 9 10; do mkdir "$LOCK" 2>/dev/null && break; sleep 0.05; done
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

[ -s "$F" ] || echo '{"subagents":0,"tool":"-"}' > "$F"

MODEL="${BARRILETE_MODEL:-}"
if [ -z "$MODEL" ] && [ "$TRANSCRIPT" != "-" ] && [ -f "$TRANSCRIPT" ]; then
  # Barato: solo al terminar un turno o si todavía no lo sabemos
  if [ "$EVENT" = "Stop" ] || [ "$(jq -r '.model // ""' "$F" 2>/dev/null)" = "" ]; then
    MODEL=$(tail -n 200 "$TRANSCRIPT" 2>/dev/null \
      | jq -r 'select(.type=="assistant") | .message.model // empty | select(. != "<synthetic>")' 2>/dev/null \
      | tail -1)
  fi
fi

# "done" falso: el turno terminó pero quedan comandos en segundo plano (red.sh, CI…).
# Lanzados = backgroundTaskId en el transcript; terminados = los que ya avisaron (<task-id>).
# Un pendiente cuenta solo si algún proceso tiene abierto su archivo de salida (si la sesión se
# reinició, el comando murió sin avisar y no debe quedar "en segundo plano" para siempre).
BG=0
if [ "$STATE" = "done" ] && [ "$TRANSCRIPT" != "-" ] && [ -f "$TRANSCRIPT" ]; then
  BG=$(python3 - "$TRANSCRIPT" "$SID" <<'PY' 2>/dev/null
import glob, json, os, re, subprocess, sys, time
from datetime import datetime
t, sid = sys.argv[1:]
d = open(t, errors="replace").read()
started = set(re.findall(r'"backgroundTaskId":"([A-Za-z0-9_-]+)"', d))
finished = set(re.findall(r"<task-id>([A-Za-z0-9_-]+)</task-id>", d))
n = 0
for tid in started - finished:
    for out in glob.glob(f"/private/tmp/claude-*/*/{sid}/tasks/{tid}.output"):
        if subprocess.run(["/usr/sbin/lsof", "-t", out], capture_output=True).stdout.strip():
            n += 1
            break
# Monitor: {"taskId", "timeoutMs"} sin archivo de salida; vivo si no avisó y no venció su timeout
for line in d.splitlines():
    if '"timeoutMs"' not in line or '"taskId"' not in line:
        continue
    try:
        o = json.loads(line)
        r = o.get("toolUseResult") or {}
        if r.get("taskId") in finished or "timeoutMs" not in r:
            continue
        t0 = datetime.fromisoformat(o["timestamp"].replace("Z", "+00:00")).timestamp()
        limit = 3600 if r.get("persistent") else r["timeoutMs"] / 1000
        if time.time() < t0 + limit:
            n += 1
    except Exception:
        pass
# Subagentes en segundo plano (Agent con run_in_background, skills "forked" como /code-review):
# toolUseResult {"agentId", "background": true | "status": "async_launched"}; avisan con <task-id>agentId.
# Vivo si no avisó y su transcript (<sesión>/subagents/agent-<id>.jsonl) se escribió en los últimos 10 min.
sub_dir = os.path.join(os.path.dirname(t), sid, "subagents")
for line in d.splitlines():
    if '"agentId"' not in line or '"toolUseResult"' not in line:
        continue
    try:
        r = json.loads(line).get("toolUseResult") or {}
        aid = r.get("agentId")
        if not aid or aid in finished or not (r.get("background") or r.get("status") == "async_launched"):
            continue
        f = os.path.join(sub_dir, f"agent-{aid}.jsonl")
        if os.path.exists(f) and time.time() - os.path.getmtime(f) < 600:
            n += 1
    except Exception:
        pass
print(n)
PY
)
  [[ "$BG" =~ ^[0-9]+$ ]] || BG=0
  [ "$BG" -gt 0 ] && STATE="background"
fi

[ "$EVENT" = "SessionStart" ] || [ "$EVENT" = "Stop" ] && RESET_SUB=1 || RESET_SUB=0
[ "$TOOL" = "-" ] && TOOL_ARG="-" || TOOL_ARG="$TOOL"
[ "$CWD" = "-" ] && CWD=""

jq --arg state "$STATE" --arg cwd "$CWD" --arg tool "$TOOL_ARG" \
   --arg phase "$PHASE" --arg role "$ROLE" --arg ref "$REF" --arg model "$MODEL" \
   --arg pane "${TMUX_PANE:-}" --argjson bg "${BG:-0}" \
   --argjson d "$DELTA" --argjson reset "$RESET_SUB" '
  (if $state != "" then .state = $state else . end)
  | .background = $bg
  | (if $cwd != "" then .cwd = $cwd else . end)
  | .ts = (now | floor)
  | (if $tool != "-" then .tool = $tool else . end)
  | (if $phase != "" then .phase = $phase else . end)
  | (if $role  != "" then .role  = $role  else . end)
  | (if $ref == "none" then del(.ref) elif $ref != "" then .ref = $ref else . end)
  | (if $model != "" then .model = $model else . end)
  | (if $pane  != "" then .pane  = $pane  else . end)
  | .subagents = (if $reset == 1 then 0 else ((.subagents // 0) + $d) end)
  | (if .subagents < 0 then .subagents = 0 else . end)
' "$F" > "$F.tmp" 2>/dev/null && mv "$F.tmp" "$F"

trigger
exit 0
