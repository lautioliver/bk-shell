#!/usr/bin/env bash
# lib/handoff.py, el aviso rojo de context_hook.py, bk chat --desde y bk handoff: el resumen para seguir en un chat
# nuevo; y la ↪ de la tira (sidebar) y de bk s (sessions) en el chat que lo dejó. Todo en un directorio temporal
# (HOME, BK_HANDOFF_DIR, estado): no toca ~/.local, ~/.cache ni ~/.claude, y no abre workspaces (bk --dry-run
# con un tmux falso que anota y falla).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
# shellcheck source=tests/lib.sh
. "$HERE/lib.sh"
TMP=$(cd "$(mktemp -d)" && pwd -P); trap 'rm -rf "$TMP"' EXIT
export HOME="$TMP/home" XDG_STATE_HOME="$TMP/state" BK_STATE_DIR="$TMP/agents" BK_CONFIG_DIR="$TMP/config"
export BK_HANDOFF_DIR="$TMP/h" BK_CONTEXT_HOOK_STATE="$TMP/ctx" PYTHONDONTWRITEBYTECODE=1
mkdir -p "$HOME" "$BK_STATE_DIR"
unset TMUX TMUX_PANE BK_ACCOUNT BK_ACCOUNT_CLAUDE BK_FROM
mkdir -p "$TMP/fake"; printf '#!/bin/sh\necho "$*" >> "%s/tmux.log"\nexit 1\n' "$TMP" >"$TMP/fake/tmux"; chmod +x "$TMP/fake/tmux"
: >"$TMP/tmux.log"
bk() { PATH="$TMP/fake:$PATH" "$ROOT/bin/bk" "$@"; }
H="$ROOT/lib/handoff.py"
has() { case "$1" in *"$2"*) check 0 "$3" ;; *) check 1 "$3 (no está «$2»)" ;; esac; }

REPO="$TMP/repo"; git init -q -b rama-x "$REPO"

# El aviso rojo de context_hook.py pide el archivo con la plantilla
T="$TMP/t.jsonl"
printf '{"type":"assistant","message":{"id":"m1","model":"claude-opus-5-5","usage":{"input_tokens":0,"output_tokens":5,"cache_creation_input_tokens":0,"cache_read_input_tokens":410000}}}\n' >"$T"
out=$(printf '{"session_id":"s-rojo","transcript_path":"%s","hook_event_name":"UserPromptSubmit","cwd":"%s"}' "$T" "$REPO" \
      | python3 "$ROOT/lib/context_hook.py")
ctx=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["hookSpecificOutput"]["additionalContext"])' "$out" 2>/dev/null)
has "$ctx" "$TMP/h/s-rojo.md" "el rojo nombra el archivo del handoff de esa sesión"
for sec in "## Hecho" "## Falta (en orden)" "1. Primer paso:" "## Te espera a vos" "## No tocar" "## Archivos y comandos"; do
  has "$ctx" "$sec" "el rojo trae la plantilla: $sec"
done
has "$ctx" "De dónde: sesión s-rojo · fuera de un workspace · rama rama-x en $REPO · cuenta default" "la línea «De dónde» la completa bk (rama, carpeta, cuenta)"
has "$ctx" "bk chat --desde s-rojo" "el rojo dice cómo seguir"
has "$ctx" "BK_TASK_DIR" "un agente de bk sigue terminando con bk done"
check "$([ -f "$TMP/h/s-rojo.json" ]; echo $?)" "el rojo deja el .json de dónde viene"
check_eq "$(BK_ACCOUNT_CLAUDE=otra python3 "$H" pedido --sid s-cta --cwd "$REPO" | grep -o 'cuenta [a-z]*' | head -1)" "cuenta otra" "la cuenta sale de BK_ACCOUNT_CLAUDE"
check_eq "$(env -u BK_HANDOFF_DIR python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import handoff; print(handoff.folder())' "$ROOT/lib")" \
  "$TMP/state/bk/handoffs" "sin BK_HANDOFF_DIR, la carpeta sale de XDG_STATE_HOME"

# find / take / list
python3 "$H" find s-rojo >/dev/null; check_eq "$?" "1" "sin el .md, no hay resumen listo"
printf '# Seguir desde ws 5: acentos ñ y `backticks` $(no) "comillas"\n- línea 2\n' >"$TMP/h/s-rojo.md"
python3 - "$TMP/h/s-rojo.json" <<'PY'
import json, sys; p = sys.argv[1]; m = json.load(open(p)); m.update(ws="5", session="bk-demo-3", name="mejoras"); json.dump(m, open(p, "w"))
PY
check_eq "$(python3 "$H" find s-rojo | cut -f1,2,3,4)" "s-rojo	$REPO	default	bk-demo-3" "find por sesión de Claude: sid, carpeta, cuenta y workspace"
check_eq "$(python3 "$H" find 5 | cut -f1)" "s-rojo" "find por número de workspace"
check_eq "$(python3 "$H" find 'ws 5' | cut -f1)" "s-rojo" "find con «ws 5»"
check_eq "$(python3 "$H" find bk-demo-3 | cut -f1)" "s-rojo" "find por nombre de la sesión de tmux"
python3 "$H" find 6 >/dev/null; check_eq "$?" "1" "otro número: no"
python3 "$H" pedido --sid s-sinws --cwd "$REPO" >/dev/null; printf 'x\n' >"$TMP/h/s-sinws.md"
python3 "$H" find "" >/dev/null; check_eq "$?" "1" "vacío: no agarra uno sin número de workspace"
check_eq "$(python3 "$H" find - | cut -f1)" "" "«-»: tampoco"
rm "$TMP/h/s-sinws".*
printf 'afuera\n' >"$TMP/s-fuera.md"; python3 "$H" take ../s-fuera >/dev/null
check "$([ -f "$TMP/s-fuera.md" ]; echo $?)" "take con una ruta (../) no sale de la carpeta de los resúmenes"
python3 "$H" pedido --sid s-vacio --cwd "$REPO" >/dev/null; : >"$TMP/h/s-vacio.md"
python3 "$H" find s-vacio >/dev/null; check_eq "$?" "1" "un .md vacío (el agente todavía no escribió): no está listo"
check_eq "$(python3 "$H" list)" "s-rojo	5	bk-demo-3	mejoras" "list: los listos (no el vacío)"
rm "$TMP/h/s-vacio".*
check_eq "$(cd "$ROOT/lib" && python3 -c 'import new; print([i[2] for i in new.build_items(None, []) if i[0] == "desde"][0])')" "↪ Seguir desde ws 5 «mejoras» · repo" "alt+n ofrece «Seguir desde ws 5»"
check_eq "$(cd "$ROOT/lib" && python3 -c 'import new; print([i[0] for i in new.build_items(None, ["/x"])])')" "['loose', 'desde', 'ws']" "alt+n: los resúmenes van entre los chats y los workspaces"
# Un worktree: .git es un archivo, y la rama aparte se ofrece igual
mkdir -p "$TMP/wt"; printf 'gitdir: %s/.git/worktrees/wt\n' "$REPO" >"$TMP/wt/.git"
check_eq "$(cd "$ROOT/lib" && python3 -c 'import new, sys; print([i[0] for i in new.build_items(sys.argv[1], [])][:2])' "$TMP/wt")" "['chat', 'branch']" "alt+n en un worktree (.git archivo): ofrece la rama aparte"

# bk chat --desde: el comando del chat nuevo le pasa el resumen entero a claude y lo borra
row=$(bk --dry-run chat --desde 5)
check_eq "$(cut -f1,2,3 <<<"$row")" "bk-repo-2	$REPO	default" "bk chat --desde 5: un workspace más del proyecto, en la carpeta y con la cuenta del resumen"
want=$(cat "$TMP/h/s-rojo.md")
mkdir "$TMP/bin"; printf '#!/bin/sh\nprintf "%%s\\n" "$@" > "%s/args"\n' "$TMP" >"$TMP/bin/claude"; chmod +x "$TMP/bin/claude"
PATH="$TMP/bin:$PATH" sh -c "$(cut -f4 <<<"$row")"
check_eq "$(head -1 "$TMP/args")" "--" "claude recibe el resumen después de --"
check_eq "$(tail -n +2 "$TMP/args")" "$want" "claude recibe el resumen completo, con acentos, backticks y \$()"
check "$([ ! -e "$TMP/h/s-rojo.md" ] && [ ! -e "$TMP/h/s-rojo.json" ]; echo $?)" "al tomarlo se borra (.md y .json)"
PATH="$TMP/bin:$PATH" sh -c "$(cut -f4 <<<"$row")"
check_eq "$(cat "$TMP/args")" "" "si ya lo tomó otro, claude arranca vacío"
# Sin --dry-run, con un tmux falso que dice que sí a todo menos a has-session (no hay sesiones): workspace abre
# sin cambiarse (WS_NOSWITCH) y el que se conecta al final es chat_from, con el comando que toma el resumen
mkdir -p "$TMP/fake2"
cat >"$TMP/fake2/tmux" <<EOF
#!/bin/sh
echo "\$*" >> "$TMP/tmux2.log"
case "\$1" in has-session) exit 1 ;; list-sessions|show-options|show-environment|display-message) exit 0 ;; esac
echo %1
EOF
chmod +x "$TMP/fake2/tmux"
printf 'resumen\n' >"$TMP/h/s-real.md"; python3 "$H" pedido --sid s-real --cwd "$REPO" >/dev/null
out=$(PATH="$TMP/fake2:$PATH" "$ROOT/bin/bk" chat --desde s-real 2>&1 </dev/null)
has "$out" "→ bk-repo-2 sigue desde s-real" "bk chat --desde: workspace no se cambia solo y chat_from sigue"
check_eq "$(grep -c '^attach' "$TMP/tmux2.log")" "1" "bk chat --desde: se conecta una sola vez, al final"
has "$(grep '^new-session' "$TMP/tmux2.log")" "take 's-real'" "el chat nuevo arranca con el comando que toma el resumen"
rm -f "$TMP/h/s-real".*
err=$(bk --dry-run chat --desde 5 2>&1); rc=$?
check_eq "$rc" "1" "bk chat --desde sin resumen listo: falla"
has "$err" "no hay un resumen listo" "y dice por qué"
err=$(bk --dry-run chat --desde 2>&1); rc=$?
check_eq "$rc" "1" "bk chat --desde sin nada: falla"
python3 "$H" pedido --sid s-cta2 --cwd "$REPO" >/dev/null; printf 'x\n' >"$TMP/h/s-cta2.md"
python3 - "$TMP/h/s-cta2.json" <<'PY'
import json, sys; p = sys.argv[1]; m = json.load(open(p)); m["account"] = "otra"; json.dump(m, open(p, "w"))
PY
check_eq "$(bk --dry-run chat --desde s-cta2 | cut -f3)" "otra" "--desde usa la cuenta del chat viejo"
check_eq "$(bk --dry-run --account default chat --desde s-cta2 | cut -f3)" "default" "bk --account X chat --desde: manda la que pediste"
python3 - "$TMP/h/s-cta2.json" "$TMP/no-existe" <<'PY'
import json, sys; p = sys.argv[1]; m = json.load(open(p)); m["cwd"] = sys.argv[2]; json.dump(m, open(p, "w"))
PY
err=$(bk --dry-run chat --desde s-cta2 2>&1); rc=$?
check_eq "$rc" "1" "--desde con la carpeta borrada: falla"
has "$err" "ya no existe" "y dice por qué"
rm -f "$TMP/h/s-cta2".*

# Viejos sin resumen: se borran a las dos semanas
python3 "$H" pedido --sid s-viejo --cwd "$REPO" >/dev/null
touch -t 202001010000 "$TMP/h/s-viejo.json"; python3 "$H" list >/dev/null
check "$([ ! -e "$TMP/h/s-viejo.json" ]; echo $?)" "un .json de hace dos semanas sin resumen se borra"
python3 "$H" pedido --sid s-nuevo --cwd "$REPO" >/dev/null; python3 "$H" list >/dev/null
check "$([ -e "$TMP/h/s-nuevo.json" ]; echo $?)" "uno nuevo sin resumen todavía no se borra"

# bk handoff --pedido: el mismo pedido, para que lo corra el agente
has "$(bk handoff --pedido --sid s-man)" "$TMP/h/s-man.md" "bk handoff --pedido --sid: el pedido con el archivo de esa sesión"
check_eq "$(bk handoff --pedido >/dev/null 2>&1; echo $?)" "1" "bk handoff --pedido sin sesión ni pane: falla"
check_eq "$(bk handoff --listos | cut -f1 | sort | tr '\n' ' ')" "" "bk handoff --listos: sin resúmenes escritos, nada"
printf 'x\n' >"$TMP/h/s-man.md"
check_eq "$(bk handoff --listos | cut -f1)" "s-man" "bk handoff --listos: el que se escribió"
rm -f "$TMP/h/s-man".*
# El pane del chat: pane_sid lee el registro más nuevo del hook de estado que nombra ese pane
printf '{"pane": "%%9", "state": "idle"}\n' >"$BK_STATE_DIR/s-viejo-del-pane.json"; touch -t 202001010000 "$BK_STATE_DIR/s-viejo-del-pane.json"
printf '{"pane": "%%9", "state": "idle"}\n' >"$BK_STATE_DIR/s-pane.json"
has "$(TMUX_PANE=%9 bk handoff --pedido)" "$TMP/h/s-pane.md" "bk handoff --pedido desde un pane: la sesión más nueva de ese pane (pane_sid)"

# Las marcas: la tira (sidebar.ready_ws, ctx_mark, row_tip) y bk s (sessions.mark_handoffs) con ↪
printf 'x\n' >"$TMP/h/s-marca.md"; python3 "$H" pedido --sid s-marca --cwd "$REPO" >/dev/null
check_eq "$(cd "$ROOT/lib" && python3 -c 'import sidebar as S, handoff as H; print(sorted(S.ready_ws([("bk-a", "s-marca"), ("bk-b", "otra"), ("bk-c", None)], H.ready_sids())))')" "['bk-a']" "la tira marca solo el workspace cuyo chat dejó el resumen"
check_eq "$(cd "$ROOT/lib" && python3 -c 'import sidebar as S; print(S.ctx_mark(450_000, True), S.ctx_mark(450_000))')" "('↪', 'ready') ('▪', 'high')" "en la tira, la ↪ va en lugar del ▪"
has "$(cd "$ROOT/lib" && python3 -c 'import sidebar as S; print(S.row_tip("ws", 3, ("demo", ""), None, "", True))')" "↪ resumen listo: bk chat --desde 3" "el tooltip dice cómo seguir"
check_eq "$(cd "$ROOT/lib" && python3 -c 'import sessions as S, handoff as H; print([r["handoff"] for r in S.mark_handoffs([{"sid": "s-marca"}, {"sid": "otra"}, {"sid": None}], H.ready_sids())])')" "[True, False, False]" "bk s marca la fila de esa sesión"
rm "$TMP/h/s-marca.md"
check_eq "$(cd "$ROOT/lib" && python3 -c 'import sessions as S, handoff as H; print(S.mark_handoffs([{"sid": "s-marca"}], H.ready_sids())[0]["handoff"])')" "False" "sin el .md (ya lo tomaron), sin marca"
check_eq "$(grep -c 'new-session' "$TMP/tmux.log")" "0" "ningún caso intentó abrir un workspace"

finish
