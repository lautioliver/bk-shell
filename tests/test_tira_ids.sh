#!/usr/bin/env bash
# La tira con ids: cada workspace con su número fijo (bk _ids, alt+N), quién lanzó a quién (@bk_parent, bk _launcher),
# los subagentes por <ventana>.<orden> <tipo> y los workspaces del mismo proyecto unidos (link_projects).
# Sin tmux real: bk ve un tmux FALSO en el PATH y la tira, un sh() falso. HOME y BK_STATE_DIR van a un tmp.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(cd "$(mktemp -d)" && pwd -P); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/fake" "$TMP/home"
# tmux falso: display-message responde FAKE_DISPLAY; list-sessions, el contenido de FAKE_SESSIONS (un archivo);
# set-option se anota en $TMP/tmux.log; lo demás no hace nada.
cat >"$TMP/fake/tmux" <<'SH'
#!/bin/sh
case "$1" in
  display-message) printf '%s\n' "$FAKE_DISPLAY" ;;
  list-sessions)   cat "$FAKE_SESSIONS" 2>/dev/null ;;
  set-option)      echo "$*" >>"$FAKE_LOG" ;;
  *) exit 0 ;;
esac
SH
chmod +x "$TMP/fake/tmux"
bk() { env -u TMUX FAKE_SESSIONS="$TMP/sessions" FAKE_LOG="$TMP/tmux.log" PATH="$TMP/fake:$PATH" HOME="$TMP/home" \
         BK_STATE_DIR="$TMP/state" BK_CONFIG_DIR="$TMP/home/.config/bk" "$ROOT/bin/bk" "$@" 2>&1; }

# --- bk _launcher: lo que launch anota como @bk_parent ---
launcher() { FAKE_DISPLAY="$1" TMUX_PANE="${2-%1}" bk _launcher; }
check_eq "$(launcher 'bk-demo-3|3||main')" "ws:bk-demo-3" "desde el chat de un workspace: su sesión (no el número, que se reusa)"
check_eq "$(launcher 'agents|||i7')" "ag:i7" "desde un agente bk: su ventana"
check_eq "$(launcher 'agents||bk-demo-3|i7')" "ws:bk-demo-3" "desde un chat de visita en la sesión de agentes: el workspace del chat"
check_eq "$(launcher 'otra|||x')" "" "desde una sesión de tmux que no es de bk: nada"
check_eq "$(launcher 'bk-demo-3|3||main' '')" "" "fuera de tmux: nada"

# launch anota @bk_parent en la ventana nueva (con un tmux falso que también "crea" la ventana)
cat >"$TMP/fake/tmux" <<'SH'
#!/bin/sh
case "$1" in
  display-message) printf '%s\n' "$FAKE_DISPLAY" ;;
  list-sessions)   cat "$FAKE_SESSIONS" 2>/dev/null ;;
  new-session|new-window) echo "%99" ;;
  has-session|list-windows|list-panes|show-options|show-environment) exit 1 ;;
  *) echo "$*" >>"$FAKE_LOG" ;;
esac
SH
mkdir -p "$TMP/repo/scripts/agents"; : > "$TMP/repo/scripts/agents/guardrails.py"
git -C "$TMP/repo" init -q 2>/dev/null
: > "$TMP/tmux.log"
(cd "$TMP/repo" && FAKE_DISPLAY='bk-demo-3|3||main' TMUX_PANE=%1 BK_FORCE=1 bk review 7 >/dev/null)
check_eq "$(grep -c '@bk_parent ws:bk-demo-3' "$TMP/tmux.log")" "1" "bk review desde el workspace 3: la ventana nueva queda con @bk_parent ws:bk-demo-3"
: > "$TMP/tmux.log"
(cd "$TMP/repo" && FAKE_DISPLAY='otra|||x' TMUX_PANE=%1 BK_FORCE=1 bk review 8 >/dev/null)
check_eq "$(grep -c '@bk_parent' "$TMP/tmux.log")" "0" "lanzado desde fuera de bk: sin @bk_parent"

# BK_NO_WARM=1 (lo pone tests/lib.sh): launch no deja corriendo "agents.py warm" ni agents.py consulta GitHub.
# Un python3 falso anota los warm (sin correrlos) y un gh falso anota si lo llaman.
REALPY=$(command -v python3)
printf '#!/bin/sh\ncase "$1 $2" in *agents.py\ warm) echo warm >>"%s"; exit 0 ;; esac\nexec "%s" "$@"\n' "$TMP/warm.log" "$REALPY" >"$TMP/fake/python3"
printf '#!/bin/sh\necho "gh $*" >>"%s"\necho "Un título"\n' "$TMP/gh.log" >"$TMP/fake/gh"
chmod +x "$TMP/fake/python3" "$TMP/fake/gh"
warms() { for _ in $(seq 1 30); do [ -s "$TMP/warm.log" ] && break; sleep 0.1; done; { cat "$TMP/warm.log" 2>/dev/null || true; } | wc -l | tr -d ' '; }
(unset BK_NO_WARM; cd "$TMP/repo" && FAKE_DISPLAY='otra|||x' bk review 9 >/dev/null)   # bk: la función de arriba
check_eq "$(warms)" "1" "sin BK_NO_WARM, launch corre agents.py warm (el control: el python3 falso lo ve)"
rm -f "$TMP/warm.log"
(cd "$TMP/repo" && FAKE_DISPLAY='otra|||x' bk review 10 >/dev/null)
check_eq "$(warms)" "0" "con BK_NO_WARM=1 (tests/lib.sh), launch no corre agents.py warm"
rm -f "$TMP/fake/python3"
git -C "$TMP/repo" remote add origin https://github.com/demo/proyecto.git 2>/dev/null
title() { PATH="$TMP/fake:$PATH" python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import agents as A; print(A.title_for(sys.argv[2], 7, {}, True) or '-')" "$ROOT/lib" "$TMP/repo"; }
check_eq "$(title)/$(cat "$TMP/gh.log" 2>/dev/null | wc -l | tr -d ' ')" "-/0" "con BK_NO_WARM=1, title_for no llama a gh"
check_eq "$(unset BK_NO_WARM; title)/$(wc -l <"$TMP/gh.log" | tr -d ' ')" \
  "Un título/1" "sin BK_NO_WARM, title_for le pregunta a gh (el control)"

# --- bk _ids / _nth: número fijo por workspace ---
# list-sessions -F '#{session_created}<TAB>#{session_name}<TAB>#{@bk_id}'
printf '100\tbk-demo\t2\n200\tbk-otro\t\n300\tterm-x\t\n400\tbk-demo-2\t2\n500\tbk-tres\t5\n' >"$TMP/sessions"
: > "$TMP/tmux.log"
check_eq "$(BK_IDS_NOSTYLE=1 bk _ids | tr '\t\n' ':,')" "1:bk-otro,2:bk-demo,3:bk-demo-2,5:bk-tres," \
  "conserva los números que ya tienen; a los que no tienen o lo repiten, el libre más bajo (gana el más viejo)"
check_eq "$(grep -c '@bk_id' "$TMP/tmux.log")" "2" "solo anota número a los que les faltaba o lo repetían"
check_eq "$(grep -c 'set-option -t =bk-otro: @bk_id 1' "$TMP/tmux.log")" "1" "el que no tenía número toma el 1 (el libre más bajo)"
printf '100\tbk-demo\t2\n500\tbk-tres\t5\n' >"$TMP/sessions"
check_eq "$(bk _nth 5)" "bk-tres" "alt+5 va al workspace con el número 5, no al 5.º por orden de creación"
check_eq "$(bk _nth 1)" "" "alt+1 sin workspace 1: nada (no salta a otro)"

# --- las filas de la tira (sidebar.items): dos workspaces y cinco agentes bk ---
rows() { HOME="$TMP/home" BK_STATE_DIR="$TMP/state" python3 - "$ROOT/lib" <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import sidebar as S
S.AGENTS = "agents"
S.workspaces = lambda: [(3, "bk-demo-3"), (5, "bk-otro")]
S.states = lambda: {p: {"_sid": "s" + p[1:]} for p in ("%1", "%2", "%10", "%11", "%12", "%13", "%14")}
import json, os, tempfile, time
S.PROJECTS = tempfile.mkdtemp()
# El orden sale del timestamp de la 1.ª línea del transcript: Claude Code reescribe el meta.json al terminar (archivo
# nuevo), así que su fecha no sirve. Acá los archivos se crean desordenados y un meta se reescribe al final.
def meta(sid, aid, kind, desc, done=False, bg=False):
    d = os.path.join(S.PROJECTS, "proj", sid, "subagents"); os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".tmp-{aid}")
    with open(tmp, "w") as f:
        json.dump({"agentType": kind, "description": desc, "finished": done or None,
                   "requestShape": "background" if bg else "sync"}, f)
    os.replace(tmp, os.path.join(d, f"agent-{aid}.meta.json"))
    time.sleep(0.02)
def sub(sid, aid, kind, desc, ts, done=False, bg=False):
    d = os.path.join(S.PROJECTS, "proj", sid, "subagents"); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"agent-{aid}.jsonl"), "w") as f:
        f.write(json.dumps({"type": "user", "timestamp": ts}) + "\n")
    meta(sid, aid, kind, desc, done, bg)
sub("s1", "a2f6743", "reviewer", "Revisar el PR, cuarta ronda", "2026-01-06T15:54:27.821Z", bg=True)
sub("s1", "aff0001", "general-purpose", "/code-review", "2026-01-06T04:45:34.886Z", done=True)
sub("s1", "a000002", "reviewer", "Revisar el PR, segunda ronda", "2026-01-06T04:40:59.195Z", done=True)
sub("s1", "a8ab28d", "general-purpose", "/code-review high", "2026-01-06T15:57:34.155Z")
meta("s1", "a2f6743", "reviewer", "Revisar el PR, cuarta ronda", done=True)   # terminó: meta nuevo, el más reciente
sub("s10", "a2c5a2b", "Explore", "Explorar", "2026-01-06T10:00:00.000Z")
S.AG.session_title = lambda sid: ""
S.U.context_of = lambda sid: None
def sh(*a, **k):
    if a[:2] == ("tmux", "list-panes") and a[3] == "bk-demo-3:main":
        return "%1\t2.1\t0\tclaude\tchat\t/demo\n"
    if a[:2] == ("tmux", "list-panes") and a[3] == "bk-otro:main":
        return "%2\t2.1\t0\tclaude\tchat\t/otro\n"
    if a[:2] == ("tmux", "list-panes") and "-s" in a:
        return ("i7\t%10\t2.1\t0\t\tt\t/demo\tws:bk-demo-3\n"
                "i9\t%11\t2.1\t0\t\tt\t/otro\tws:bk-otro\n"
                "pr7\t%12\t2.1\t0\t\tt\t/demo\tag:i7\n"
                "viejo\t%13\t2.1\t0\t\tt\t/demo\t\n"
                "suelto\t%14\t2.1\t0\t\tt\t/demo\tws:bk-cerrado\n")
    return ""
S.sh = sh
S.project_name = lambda p: p.strip("/") or "?"
rows = S.items()
print(" ".join(f"{r[0]}:{S.row_label(r)}" for r in rows))
print(S.branch_tip(rows[1]))
PY
}
OUT=$(rows)
check_eq "$(sed -n 1p <<<"$OUT")" "ws:3 sub:3.4 general kid:i7 ws:5 kid:i9 ag:i7 ←3 sub:i7.1 Explore kid:pr7 ag:i9 ←5 ag:pr7 ←i7 ag:viejo ag:suelto ←?" \
  "cada ventana con sus subagentes (ventana.orden, contando los terminados) y los agentes que lanzó; cada agente, de dónde viene (←? si esa ventana ya se cerró)"
check_eq "$(sed -n 2p <<<"$OUT")" "↳ subagente 3.4 general · /code-review high · agent-a8ab28d" "el tooltip del subagente: su nombre, la descripción y el id entero"

# Un tipo de subagente con escapes no llega a la pantalla; un transcript sin 1.ª línea completa ordena al final
SAFE=$(python3 - "$ROOT/lib" <<'PY'
import sys, os, json, tempfile; sys.path.insert(0, sys.argv[1]); import sidebar as S
d = tempfile.mkdtemp()
with open(os.path.join(d, "agent-b.jsonl"), "w") as f: f.write('{"timestamp": "2026-01-01T00:00:00Z"}\n')
with open(os.path.join(d, "agent-a.jsonl"), "w") as f: f.write('{"timestamp": "2026-01-0')   # a medio escribir
with open(os.path.join(d, "agent-a.meta.json"), "w") as f: json.dump({"agentType": "x\x1b[2Jy"}, f)
n = S.sub_name("i\x1b]0;t\x07w", os.path.join(d, "agent-a.jsonl"))
print(repr(n), "\x1b" in n)
PY
)
check_eq "$SAFE" "'i ]0;t w.2 x [2Jy' False" "sub_name: sin caracteres de control; el recién lanzado (sin 1.ª línea) va después"

# --- link_projects: une los workspaces del mismo proyecto ---
links() { python3 - "$ROOT/lib" "$1" <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import sidebar as S
blocks = []
for tok in sys.argv[2].split():          # "1:a 2:b+ 3:a": número:proyecto, "+" = con un subagente
    n, proj = tok.split(":"); sub = proj.endswith("+"); proj = proj.rstrip("+")
    ws = ("ws", f"bk-{n}", f"bk-{n}", "idle", (proj, ""), int(n), None, proj)
    blocks.append((ws, [("sub", "d", f"sub:{n}", "working", ("", "d"), None, None)] if sub else []))
print(" ".join(f"{r[7] or '.'}{r[5] if r[0] == 'ws' else 's'}" for r in S.link_projects(blocks)))
PY
}
check_eq "$(links '1:c 2:a 3:c 4:b 5:a 6:c')" "╭1 │3 ╰6 ╭2 ╰5 .4" "junta cada proyecto donde está su número más bajo y los une con la línea"
check_eq "$(links '1:a 2:b')" ".1 .2" "un proyecto con un solo workspace: sin línea"
check_eq "$(links '1:a+ 2:a+')" "╭1 │s ╰2 .s" "los subagentes de adentro del grupo siguen la línea; los del último, no"
check_eq "$(links '')" "" "sin workspaces: nada"
finish
