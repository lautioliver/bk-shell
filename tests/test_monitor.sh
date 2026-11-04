#!/usr/bin/env bash
# bk monitor: detectores de desvíos sobre un agente y un transcript falsos, en un tmux aparte. No suena (BK_MONITOR_DRY).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'TMUX_TMPDIR="$TMP/tmux" tmux kill-server 2>/dev/null; rm -rf "$TMP"' EXIT
command -v tmux >/dev/null || { echo "1..0 # SKIP sin tmux"; exit 0; }

export TMUX_TMPDIR="$TMP/tmux" BK_STATE_DIR="$TMP/state" BK_CACHE_DIR="$TMP/cache" BK_PROJECTS="$TMP/proj" BK_MONITOR_DRY=1
unset TMUX; mkdir -p "$TMUX_TMPDIR" "$BK_STATE_DIR" "$BK_PROJECTS/p"
tmux new-session -d -s agents -n i99 "cat"
P=$(tmux list-panes -t agents:i99 -F '#{pane_id}')
jq -n --arg p "$P" '{state: "working", pane: $p, ts: ((now | floor) - 1300), cwd: "/tmp", tool: "Bash"}' > "$BK_STATE_DIR/sid1.json"

OUT=$(python3 - "$ROOT" "$BK_PROJECTS/p/sid1.jsonl" <<'PY'
import json, sys, time
sys.path.insert(0, sys.argv[1] + "/lib")
import monitor as M
f = sys.argv[2]
ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
def w(*evs):
    with open(f, "a") as fh:
        for e in evs:
            fh.write(json.dumps(e) + "\n")
w({"type": "assistant", "timestamp": ts, "message": {"model": "claude-sonnet-5-5", "content": [{"type": "text", "text": "hola"}],
   "usage": {"input_tokens": 1000000, "output_tokens": 0}}})
m = M.Monitor(); m.refresh()
print("costo", round(m.agents[0]["_cost"], 2))
tu = lambda i, c: {"type": "assistant", "timestamp": ts, "message": {"content": [{"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": c}}]}}
tr = lambda i, t: {"type": "user", "timestamp": ts, "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "is_error": True, "content": t}]}}
w(tu(1, "git push --force-with-lease origin HEAD"), tu(2, "gh auth login"))
w({"type": "assistant", "timestamp": ts, "message": {"content": [{"type": "text", "text": "Lo dejé así, como decidiste."}]}})
for i in range(3, 7):
    w(tu(i, "bash tests/x.sh"), tr(i, "Exit code 1\nnot ok 3 - falla"))
for i in range(7, 10):   # "Exit code 1" con distinto error: no es un loop
    w(tu(i, "bash tests/y.sh"), tr(i, f"Exit code 1\nnot ok {i} - otro"))
w(tu(10, "bash red.sh"), tr(10, "red.sh: no hay casos"))
m.refresh(); m.refresh()
for e in m.events:
    print("ev", e["type"])
print("frame", len(M.frame(m, 140, 45, 0, "agents", 0, None)))
PY
)
check_eq "$(grep '^costo' <<<"$OUT")" "costo 2.0" "costo de hoy de la sesión (1M tokens de entrada de sonnet 5.5)"
for t in force_push credential attribution loop verify_fail stale_grave; do
  grep -qx "ev $t" <<<"$OUT"; check $? "detecta $t"
done
check_eq "$(grep -c '^ev loop' <<<"$OUT")" 1 "un solo loop (otros errores con Exit code 1 no cuentan)"
check_eq "$(grep -cx 'ev stale' <<<"$OUT")" 0 "trabado se avisa una vez, como grave"
check_eq "$(grep -c '^ev ' <<<"$OUT")" 6 "ni un desvío de más"
grep -q '^frame [0-9]' <<<"$OUT"; check $? "dibuja un cuadro"
# La tecla s alterna el sonido en los dos sentidos (antes no volvía a prenderlo)
SC="$TMP/sndconf"; mkdir -p "$SC"; echo off > "$SC/sound"
tmux new-session -d -s mon -x 150 -y 40 "BK_CONFIG_DIR=$SC python3 $ROOT/lib/monitor.py"
sleep 3; tmux send-keys -t mon s; sleep 1.2
check_eq "$(cat "$SC/sound")" on "s prende el sonido apagado"
tmux send-keys -t mon s; sleep 1.2
check_eq "$(cat "$SC/sound")" off "s lo vuelve a apagar"
tmux send-keys -t mon q

# Detectores compartidos (lib/deviations.py): cerrar una pregunta no es un desvío; los hallazgos de una review se clasifican
DV=$(python3 - "$ROOT" <<'PY'
import sys
sys.path.insert(0, sys.argv[1] + "/lib")
import deviations as D
ctx = {"ts": "", "project": "p", "session": "s", "model": "m", "role": "implementer"}
print("ask", len(D.user_events({"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                                "content": "The user doesn't want to proceed"}, ctx, {"t1": "AskUserQuestion"}, {}, "x")))
print("bash", len(D.user_events({"type": "tool_result", "tool_use_id": "t2", "is_error": True,
                                 "content": "The user doesn't want to proceed"}, ctx, {"t2": "Bash::ls"}, {}, "x")))
body = "### Blocking\n\nNone.\n\n### To fix\n\n1. **PR body:** the red block is from another PR.\n   - detalle anidado\n2. **x.py:** a `null` gives a traceback.\n\n### Suggestions\n\n- algo"
items = D.review_findings(body)
print("items", len(items))
print("clases", "|".join(D.finding_class(i) for i in items))
print("chat", len(D.agent_risks({"type": "tool_use", "input": {"command": "env -u GH_CONFIG_DIR gh pr merge 1"}}, ctx, False)))
PY
)
check_eq "$(grep '^ask' <<<"$DV")" "ask 0" "cerrar una pregunta (AskUserQuestion) no es un desvío"
check_eq "$(grep '^bash' <<<"$DV")" "bash 1" "rechazar un comando sí"
check_eq "$(grep '^items' <<<"$DV")" "items 2" "una review: solo los ítems de primer nivel de To fix (Blocking: None, Suggestions afuera)"
check_eq "$(grep '^clases' <<<"$DV")" "clases metadatos del PR|entrada inesperada sin manejar" "los hallazgos se clasifican"
check_eq "$(grep '^chat' <<<"$DV")" "chat 0" "en un chat tuyo, env -u GH_CONFIG_DIR no es un desvío"

finish
