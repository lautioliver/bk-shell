#!/usr/bin/env bash
# Tareas de los agentes (lib/tasks.py, ADR-005 del kit): estados, bk done, propuestas, cola de bk say,
# seq sin huecos y, con tmux, que bk run/review no lancen dos agentes y que el cierre llegue solo.
# Todo en carpetas temporales: nunca toca tus tareas (BK_TASKS_DIR) ni tu tmux (TMUX_TMPDIR).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
unset BK_TASK_DIR BARRILETE_ROLE BARRILETE_MODEL BARRILETE_ISSUE BARRILETE_PROVIDER TMUX_PANE
export BK_TASKS_DIR="$TMP/tasks"
T="python3 $ROOT/lib/tasks.py"
st() { jq -r .status "$1/task.json"; }

# ---- crear y no duplicar (D2) ----
d=$($T create kit 244-implementer --role implementer --ref '#244' --repo "$TMP/kit" --worktree "$TMP/wt" --window i244)
check $? "create crea la tarea"
check_eq "$d" "$BK_TASKS_DIR/kit/244-implementer" "en <raíz>/<proyecto>/<id>"
check_eq "$(st "$d")" in_progress "una tarea nueva arranca en in_progress"
check_eq "$(jq -r .dir "$d/task.json")" "$d" "task.json guarda su carpeta (D9)"
check_eq "$(head -1 "$d/events.jsonl" | jq -r '"\(.seq) \(.type)"')" "1 task.created" "el primer evento es task.created con seq 1"
d2=$($T create kit 244-implementer --role implementer); rc=$?
check_eq "$rc" 3 "crear una tarea activa que ya existe sale con 3"
check_eq "$d2" "$d" "y devuelve la que ya existe"
check_eq "$($T state kit 244-implementer)" in_progress "state devuelve el estado"
$T state kit nada >/dev/null; check_eq $? 4 "state de una tarea que no existe sale con 4"
$T create kit ../x --role r >/dev/null 2>&1; check_eq $? 64 "un id con / se rechaza"

# ---- estados no terminales (los usan los hooks del kit) ----
$T status "$d" waiting; check_eq "$(st "$d")" waiting "status waiting"
check_eq "$(tail -1 "$d/events.jsonl" | jq -r .type)" turn.waiting "y deja turn.waiting"
$T status "$d" in_progress; check_eq "$(st "$d")" in_progress "status in_progress"
$T status "$d" completed >/dev/null 2>&1; check_eq $? 64 "status no acepta un estado terminal"

# ---- bk done (D4) ----
(cd "$TMP" && $T done pr 12 >/dev/null 2>&1); check_eq $? 64 "bk done sin BK_TASK_DIR sale con 64"
BK_TASK_DIR="$d" $T done pr abc >/dev/null 2>&1; check_eq $? 64 "bk done pr sin número sale con 64"
BK_TASK_DIR="$d" $T done dropped >/dev/null 2>&1; check_eq $? 64 "bk done dropped sin motivo sale con 64"
BK_TASK_DIR="$d" $T done blockers extra >/dev/null 2>&1; check_eq $? 64 "bk done blockers no lleva argumentos"
BK_TASK_DIR="$d" $T done otra >/dev/null 2>&1; check_eq $? 64 "un resultado desconocido sale con 64"
check_eq "$(st "$d")" in_progress "los usos inválidos no tocan la tarea"
out=$(BK_TASK_DIR="$d" $T done pr '#12'); check $? "bk done pr 12"
check_eq "$(st "$d")" completed "pr cierra en completed"
check_eq "$(jq -c .result "$d/task.json")" '{"kind":"pr","pr":12}' "con el resultado tipado"
check_eq "$(tail -1 "$d/events.jsonl" | jq -r '"\(.type) \(.task.status)"')" "task.completed completed" "el último evento es el terminal y copia el task.json"
grep -q 'bk approve pr12-reviewer' <<<"$out"; check $? "avisa la propuesta del reviewer"

# ---- propuesta del reviewer (D6) ----
r="$BK_TASKS_DIR/kit/pr12-reviewer"
check_eq "$(st "$r")" pending "el PR de un implementer crea pr<n>-reviewer en pending"
check_eq "$(jq -r '"\(.from) \(.pr) \(.repo)"' "$r/task.json")" "244-implementer 12 $TMP/kit" "con su origen, el PR y el repo"
$T status "$r" in_progress; check_eq "$(st "$r")" pending "un hook no arranca una propuesta"

# ---- lanzada por un orquestador: él lanza al reviewer, bk no lo propone (enmienda de D6) ----
o=$($T create kit 60-implementer --role implementer --orchestrated)
check_eq "$(jq -r .orchestrated "$o/task.json")" true "--orchestrated queda en task.json"
out=$(BK_TASK_DIR="$o" $T done pr 61)
[ ! -e "$BK_TASKS_DIR/kit/pr61-reviewer" ]; check $? "con --orchestrated, bk done pr no propone reviewer"
grep -q 'lo lanza el orquestador' <<<"$out"; check $? "y lo dice"

# ---- terminal no cambia nunca (D3) ----
BK_TASK_DIR="$d" $T done dropped "otra cosa" >/dev/null 2>&1; check_eq $? 64 "bk done sobre una tarea cerrada falla"
$T ended "$d" >/dev/null; $T cancel "$d" >/dev/null; $T status "$d" waiting
$T event "$d" commit sha=abc
check_eq "$(st "$d")" completed "ni ended, ni cancel, ni status, ni event cambian un estado terminal"
check_eq "$(tail -1 "$d/events.jsonl" | jq -r .type)" task.completed "y no se agregan eventos después del terminal"

# ---- incomplete y cancelled ----
e=$($T create kit 9-implementer --role implementer --window i9)
$T ended "$e" >/dev/null; check_eq "$(st "$e")" incomplete "terminar sin bk done es incomplete"
c=$($T create kit pr5-reviewer --role reviewer --window pr5)
$T cancel "$c" >/dev/null; check_eq "$(st "$c")" cancelled "bk stop es cancelled"
BK_TASK_DIR="$c" $T done blockers >/dev/null 2>&1; check_eq $? 64 "no se cierra dos veces"

# ---- relanzar una terminada la guarda aparte (D9: no se borra) ----
n=$($T create kit 9-implementer --role implementer --window i9); check $? "relanzar una tarea terminada crea una nueva"
check_eq "$(st "$n")" in_progress "la nueva arranca en in_progress"
check_eq "$(ls -d "$BK_TASKS_DIR"/kit/9-implementer~* | wc -l | tr -d ' ')" 1 "la vieja queda como <id>~<fecha>"

# ---- seq sin huecos con escrituras concurrentes (D5) ----
for i in $(seq 1 30); do $T event "$n" commit "sha=$i" & done; wait
check_eq "$(jq -s '[.[].seq] | sort == [range(1; length + 1)]' "$n/events.jsonl")" true "30 eventos en paralelo: seq de 1 a N sin huecos ni repetidos"
check_eq "$(jq -r .seq "$n/task.json")" "$(wc -l <"$n/events.jsonl" | tr -d ' ')" "task.json lleva el último seq"
check_eq "$(jq -c 'keys' <<<"$(tail -1 "$n/events.jsonl")")" '["seq","sha","ts","type"]' "un evento lleva solo los metadatos que se le pasan"

# ---- find, cola de bk say (D8) ----
check_eq "$($T find --window i9)" "$n" "find --window da la tarea activa de la ventana"
$T find --window pr5 >/dev/null; check_eq $? 4 "una ventana sin tarea activa sale con 4"
$T queue "$n" "primero"; $T queue "$n" "después"
check_eq "$($T drain "$n" | jq -r .msg | tr '\n' '|')" "primero|después|" "drain devuelve la cola en orden"
check_eq "$($T drain "$n")" "" "y la vacía"

# ---- bk tasks y prune (D9) ----
out=$($T list)
grep -q "$BK_TASKS_DIR/kit/pr12-reviewer" <<<"$out"; check $? "bk tasks muestra la carpeta de cada tarea"
check_eq "$($T list --json | jq -r 'first.status')" pending "y pone primero las propuestas (después de las que esperan)"
$T prune >/dev/null; check_eq "$(ls "$BK_TASKS_DIR/kit" | wc -l | tr -d ' ')" 6 "prune sin --yes no borra nada"
$T prune --yes >/dev/null
check_eq "$(ls "$BK_TASKS_DIR/kit" | sort | tr '\n' ' ')" "9-implementer pr12-reviewer " "prune --yes borra las terminadas y deja las activas"

# ---- con tmux: bk review dos veces, cola de say, fin del proceso, stop y approve ----
if command -v tmux >/dev/null; then
  X="$TMP/x"; mkdir -p "$X/bin" "$X/tmux" "$X/state"
  P="$X/proj"; mkdir -p "$P/scripts/agents" "$P/.barrilete"; git init -q "$P"; touch "$P/scripts/agents/guardrails.py"
  printf 'reviewer: claude/sonnet\nimplementer: claude/sonnet\n' >"$P/.barrilete/roles.yml"
  # claude falso: escribe en un archivo de la tarea lo que le tipean, hasta Ctrl-D
  printf '#!/bin/bash\nexec cat > "$BK_TASK_DIR/typed.txt"\n' >"$X/bin/claude"; chmod +x "$X/bin/claude"
  (
    export PATH="$X/bin:$PATH" TMUX_TMPDIR="$X/tmux" BK_STATE_DIR="$X/state" BK_CACHE_DIR="$X/cache" BK_CLIENT=nocliente
    export BK_TASKS_DIR="$X/tasks" BK_DRAIN_DELAY=0 BK_SOUND_DRY=1 BK_CONFIG_DIR="$X/config"; unset TMUX
    bk() { "$ROOT/bin/bk" "$@" </dev/null; }
    # Una sesión que no es de agentes mantiene vivo el servidor de prueba, como el tuyo: si bk stop cierra la
    # última ventana, el servidor se apagaría y el bk run siguiente podía colgarse arrancando otro a la vez.
    tmux new-session -d -s keep "sleep 600"
    cd "$P"
    bk review 7 >/dev/null 2>&1; echo "review1=$?"
    out=$(bk review 7 2>&1); echo "review2=$?"; grep -q 'ya hay una tarea activa: pr7-reviewer' <<<"$out" && echo "aviso=ok"
    echo "ventanas=$(tmux list-windows -t agents -F '#{window_name}' | grep -c '^pr7$')"
    td="$X/tasks/proj/pr7-reviewer"; sleep 0.3
    pane=$(tmux list-panes -t agents:pr7 -F '#{pane_id}')
    echo "envdir=$(tmux show-environment -t agents BK_TASK_DIR 2>/dev/null | cut -c1-12)"
    # El agente trabaja: bk say se encola; al terminar el turno el hook lo escribe
    jq -n --arg p "$pane" '{state: "working", pane: $p, ts: (now | floor + 1), cwd: "/tmp"}' > "$X/state/s1.json"
    bk say pr7 "hola agente" >/dev/null 2>&1; echo "say=$?"; sleep 0.3
    echo "antes=$(cat "$td/typed.txt" 2>/dev/null)|cola=$(wc -l < "$td/queue.jsonl" | tr -d ' ')"
    bk ls 2>/dev/null | grep pr7 | grep -q '1 en cola' && echo "lscola=ok"
    printf '{"session_id":"s1","hook_event_name":"Stop","cwd":"/tmp"}' \
      | BK_TASK_DIR="$td" TMUX_PANE="$pane" bash "$ROOT/hooks/agent_state.sh"
    sleep 1.5; echo "despues=$(cat "$td/typed.txt" 2>/dev/null)"
    jq '.state = "working"' "$X/state/s1.json" > "$X/s" && mv "$X/s" "$X/state/s1.json"
    bk say --now pr7 "ya mismo" >/dev/null 2>&1; sleep 0.4; echo "now=$(tail -1 "$td/typed.txt")"
    # El proceso del agente termina sin bk done: incomplete
    tmux send-keys -t "$pane" C-d; sleep 1; echo "fin=$(jq -r .status "$td/task.json")"
    bk ls 2>/dev/null | grep pr7 | grep -q 'terminó sin bk done' && echo "lsfin=ok"
    bk stop pr7 >/dev/null 2>&1
    # bk fix con el otro implementer sobre un PR que ya tiene uno activo: no lanza otro (antes de tocar gh)
    python3 "$ROOT/lib/tasks.py" create proj pr9-implementer-light --role implementer-light --window f9 >/dev/null
    out=$(bk fix 9 "algo" 2>&1); echo "fixrol=$?"; grep -q 'ya hay una tarea activa: pr9-implementer-light' <<<"$out" && echo "fixaviso=ok"
    # bk stop sobre un agente vivo: cancelled
    bk review 8 >/dev/null 2>&1; sleep 0.3; bk stop pr8 >/dev/null 2>&1
    echo "stop=$(jq -r .status "$X/tasks/proj/pr8-reviewer/task.json")"
    # bk run --orchestrated de punta a punta (prepare.sh falso, remote local): sin propuesta; sin el flag, con
    git init -q --bare "$X/origin.git"; git -C "$P" remote add origin "$X/origin.git"
    git -C "$P" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
    mkdir -p "$P/.claude/skills/implement-issue"
    # como el real: un worktree de git en ../<repo>-wt/ (bk saca el proyecto de ahí)
    printf '#!/bin/bash\nw="%s-wt/i$1"; git worktree add -q -b "t$1" "$w" >/dev/null 2>&1; echo "Worktree: $w"\n' "$P" > "$P/.claude/skills/implement-issue/prepare.sh"
    chmod +x "$P/.claude/skills/implement-issue/prepare.sh"
    BK_BOT=0 bk run 40 --role implementer --orchestrated >/dev/null 2>&1; echo "run40=$?"
    BK_TASK_DIR="$X/tasks/proj/40-implementer" python3 "$ROOT/lib/tasks.py" done pr 41 >/dev/null
    [ -e "$X/tasks/proj/pr41-reviewer" ] && echo "prop41=si" || echo "prop41=no"
    BK_BOT=0 bk run 42 --role implementer >/dev/null 2>&1; echo "run42=$?"
    BK_TASK_DIR="$X/tasks/proj/42-implementer" python3 "$ROOT/lib/tasks.py" done pr 43 >/dev/null
    [ -e "$X/tasks/proj/pr43-reviewer" ] && echo "prop43=si" || echo "prop43=no"
    # Una propuesta: bk ls la muestra y bk approve la lanza
    i=$(python3 "$ROOT/lib/tasks.py" create proj 30-implementer --role implementer --repo "$P")
    BK_TASK_DIR="$i" python3 "$ROOT/lib/tasks.py" done pr 31 >/dev/null
    bk ls 2>/dev/null | grep -q 'bk approve pr31-reviewer' && echo "ls=ok"
    (cd /tmp && bk approve pr31-reviewer >/dev/null 2>&1); echo "approve=$?"
    echo "aprobada=$(jq -r .status "$X/tasks/proj/pr31-reviewer/task.json") $(tmux list-windows -t agents -F '#{window_name}' | grep -c '^pr31$')"
    bk approve pr31-reviewer >/dev/null 2>&1; echo "approve2=$?"
    sleep 0.3; tmux kill-server 2>/dev/null
  ) > "$X/out" 2>&1
  g() { grep "^$1=" "$X/out" | head -1; }
  check_eq "$(g review1)" "review1=0" "bk review lanza el agente con su tarea"
  check_eq "$(g review2)" "review2=0" "un segundo bk review sobre el mismo PR no falla"
  check_eq "$(g aviso)" "aviso=ok" "y avisa que ya hay una tarea activa"
  check_eq "$(g ventanas)" "ventanas=1" "pero no lanza otro agente (D2)"
  check_eq "$(g envdir)" "envdir=" "BK_TASK_DIR es del pane, no de toda la sesión"
  check_eq "$(g say)" "say=0" "bk say a un agente que trabaja no falla"
  check_eq "$(g antes)" "antes=|cola=1" "se encola sin escribirle (D8)"
  check_eq "$(g lscola)" "lscola=ok" "bk ls muestra el mensaje en cola"
  check_eq "$(g despues)" "despues=hola agente" "al terminar el turno el hook se lo escribe"
  check_eq "$(g now)" "now=ya mismo" "bk say --now le escribe aunque trabaje"
  check_eq "$(g fin)" "fin=incomplete" "si el proceso termina sin bk done, la tarea queda incomplete"
  check_eq "$(g lsfin)" "lsfin=ok" "bk ls muestra que terminó sin bk done"
  check_eq "$(g stop)" "stop=cancelled" "bk stop deja la tarea cancelled"
  check_eq "$(g fixrol)" "fixrol=0" "bk fix sobre un PR con otro implementer activo no falla"
  check_eq "$(g fixaviso)" "fixaviso=ok" "pero avisa la tarea del otro rol y no lanza nada"
  check_eq "$(g ls)" "ls=ok" "bk ls muestra la propuesta del reviewer"
  check_eq "$(g run40)" "run40=0" "bk run --orchestrated lanza el agente"
  check_eq "$(g prop41)" "prop41=no" "y su PR no propone reviewer (lo lanza el orquestador)"
  check_eq "$(g run42)" "run42=0" "bk run sin --orchestrated lanza el agente"
  check_eq "$(g prop43)" "prop43=si" "y su PR sí propone reviewer"
  check_eq "$(g approve)" "approve=0" "bk approve la lanza desde cualquier carpeta"
  check_eq "$(g aprobada)" "aprobada=in_progress 1" "la propuesta pasa a in_progress con su ventana"
  check_eq "$(g approve2)" "approve2=1" "una tarea ya aprobada no se aprueba dos veces"
fi

finish
