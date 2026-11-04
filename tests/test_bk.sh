#!/usr/bin/env bash
# bk e install.sh: sintaxis, ayuda, comillas de las instrucciones y el instalador en un home de prueba.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

for f in bin/bk install.sh hooks/agent_state.sh; do bash -n "$ROOT/$f"; check $? "$f no tiene errores de sintaxis"; done
"$ROOT/bin/bk" help >/dev/null; check $? "bk help funciona"
"$ROOT/bin/bk" no-existe >/dev/null 2>&1; [ $? -eq 2 ]; check $? "un comando desconocido sale con 2"

# Las instrucciones a los agentes llegan enteras: acentos y comillas simples (bash 3.2 rompía %q)
eval "$(sed -n '/^sq()/p' "$ROOT/bin/bk")"
got=$(eval "printf '%s' $(sq "Seguí: it's ñ")")
check_eq "$got" "Seguí: it's ñ" "sq conserva acentos y comillas"

# Instalador en un HOME vacío, sin terminal: no toca tmux.conf ni settings.json
H="$TMP/home"; mkdir -p "$H/.claude"; echo '{"theme":"dark"}' >"$H/.claude/settings.json"
HOME="$H" BK_REPO="$ROOT" bash "$ROOT/install.sh" </dev/null >/dev/null 2>&1; check $? "install.sh termina bien"
[ -L "$H/.local/bin/bk" ]; check $? "enlaza ~/.local/bin/bk"
check_eq "$(jq -c .hooks "$H/.claude/settings.json")" null "sin terminal no registra el hook"
[ ! -f "$H/.tmux.conf" ]; check $? "sin terminal no toca tmux.conf"

# Con BK_YES=s registra el hook en los eventos y lo saca al desinstalar
rm -rf "$H/.bk-shell"
HOME="$H" BK_REPO="$ROOT" BK_YES=s bash "$ROOT/install.sh" </dev/null >/dev/null 2>&1
check_eq "$(jq '.hooks | keys | length' "$H/.claude/settings.json")" 12 "con BK_YES=s registra el hook en 12 eventos"
grep -q 'bk-shell/tmux/bk.conf' "$H/.tmux.conf"; check $? "con BK_YES=s carga bk.conf en tmux.conf"
HOME="$H" bash "$H/.bk-shell/install.sh" --uninstall >/dev/null 2>&1
check_eq "$(jq -c .hooks "$H/.claude/settings.json")" "{}" "desinstalar saca el hook"
grep -q 'bk-shell' "$H/.tmux.conf"; [ $? -ne 0 ]; check $? "desinstalar saca bk.conf de tmux.conf"
[ "$(jq -r .theme "$H/.claude/settings.json")" = dark ]; check $? "no toca el resto de settings.json"

# bk mode <rol>: la 3.ª columna de roles.yml (el del proyecto manda), bk si no tiene; un rol que no existe falla.
# role_spec sigue devolviendo solo provider/modelo.
P="$TMP/proj"; mkdir -p "$P/.barrilete"; git init -q "$P"
printf 'planner: claude/opus sub\nreviewer: claude/sonnet   # sin modo\nimplementer: codex/gpt-5 bk # comentario\n' >"$P/.barrilete/roles.yml"
check_eq "$(cd "$P" && "$ROOT/bin/bk" mode planner)" sub "bk mode: el modo del roles.yml del proyecto"
check_eq "$(cd "$P" && "$ROOT/bin/bk" mode reviewer)" bk "bk mode: sin modo es bk"
check_eq "$(cd "$P" && "$ROOT/bin/bk" mode implementer)" bk "bk mode: modo seguido de un comentario"
check_eq "$(cd "$TMP" && "$ROOT/bin/bk" mode orchestrator)" bk "bk mode: fuera de un proyecto, el roles.yml de bk (sin modos) da bk"
(cd "$P" && "$ROOT/bin/bk" mode no-existe >/dev/null 2>&1); check $(( $? == 0 )) "bk mode: un rol desconocido falla"
(cd "$P" && "$ROOT/bin/bk" mode '.*' >/dev/null 2>&1); check $(( $? == 0 )) "bk mode: no acepta regex como rol"
check_eq "$(cd "$P" && "$ROOT/bin/bk" roles | awk '$1 == "planner" {print $2, $3}')" "claude/opus sub" "bk roles: provider/modelo y modo por separado"

# bk chat --rama: sin nombre, con un nombre en blanco o fuera de un repo git falla con un mensaje claro y no
# crea nada (ni worktree, ni rama, ni sesión). tmux en un servidor propio (TMUX_TMPDIR), nunca el tuyo.
if command -v tmux >/dev/null; then
  T="$TMP/tmux"; mkdir -p "$T" "$TMP/nogit"
  git init -q -b main "$TMP/repo" && git -C "$TMP/repo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
  bkt() { env -u TMUX TMUX_TMPDIR="$T" BK_SESSION="bk-test-agents-$$" BK_CLIENT=nocliente BK_STATE_DIR="$TMP/state" "$ROOT/bin/bk" "$@"; }
  sess="zz-bk-test-$$"
  TMUX_TMPDIR="$T" tmux -f /dev/null new-session -d -s "$sess" "sleep 300"
  nsess() { TMUX_TMPDIR="$T" tmux list-sessions 2>/dev/null | wc -l | tr -d ' '; }
  before=$(nsess)
  out=$(cd "$TMP/repo" && bkt chat --rama 2>&1 </dev/null); rc=$?
  [ $rc -ne 0 ] && grep -q 'bk chat --rama <nombre' <<<"$out"; check $? "chat --rama sin nombre falla con el uso"
  out=$(cd "$TMP/repo" && bkt chat --rama "   " "$sess" 2>&1 </dev/null); rc=$?
  [ $rc -ne 0 ] && grep -q 'bk chat --rama <nombre' <<<"$out"; check $? "chat --rama con nombre en blanco falla"
  out=$(cd "$TMP/repo" && bkt chat --rama "¡¿~~?!" "$sess" 2>&1 </dev/null); rc=$?
  [ $rc -ne 0 ] && grep -q 'no deja un nombre de rama' <<<"$out"; check $? "chat --rama con un nombre sin letras ni números falla"
  out=$(cd "$TMP/nogit" && bkt chat --rama algo "$sess" 2>&1 </dev/null); rc=$?
  [ $rc -ne 0 ] && grep -q 'necesita un repo git' <<<"$out"; check $? "chat --rama fuera de un repo git falla"
  check_eq "$(git -C "$TMP/repo" branch --list 'chat/*')" "" "chat --rama fallido no crea ramas"
  [ ! -e "$TMP/repo-wt" ] && [ ! -e "$TMP/nogit-wt" ]; check $? "chat --rama fallido no crea worktrees"
  check_eq "$(nsess)" "$before" "chat --rama fallido no crea sesiones de tmux"
  TMUX_TMPDIR="$T" tmux kill-session -t "=$sess" 2>/dev/null
fi

# bk song sin red ni mpv: géneros, tus radios y validaciones (no reproduce nada)
SG="$ROOT/lib/song.sh"; export BK_SONG_CACHE="$TMP/song" BK_CONFIG_DIR="$TMP/songconf"
check_eq "$("$SG" genres | tr '\n' ' ')" "techno house rock reggaeton cachengue " "bk song: los cinco géneros de fábrica"
check_eq "$("$SG" stations | awk -F'\t' '$3 == "cachengue"' | wc -l | tr -d ' ')" 3 "bk song: tres radios de cachengue"
"$SG" add mia https://example.com/a.mp3 fiesta Mi Radio >/dev/null; check $? "bk song add guarda una radio"
check_eq "$("$SG" stations | head -1)" "mia	Mi Radio	fiesta" "bk song: tus radios van primero, con su género"
check_eq "$("$SG" genres | head -1)" fiesta "bk song: un género tuyo aparece primero en genres"
"$SG" add mia https://example.com/b.mp3 fiesta >/dev/null
check_eq "$(grep -c '^mia|' "$BK_CONFIG_DIR/song-stations")" 1 "bk song add con la misma clave la reemplaza"
"$SG" add 'Mal!' https://x >/dev/null 2>&1; [ $? -ne 0 ]; check $? "bk song add rechaza una clave inválida"
"$SG" add ok 'ftp://x' >/dev/null 2>&1; [ $? -ne 0 ]; check $? "bk song add rechaza una url que no es http(s)"
"$SG" add ok 'https://x|y' >/dev/null 2>&1; [ $? -ne 0 ]; check $? "bk song add rechaza una url con |"
"$SG" rm mia >/dev/null; check $? "bk song rm saca una radio tuya"
"$SG" rm techno >/dev/null 2>&1; [ $? -ne 0 ]; check $? "bk song rm no saca una de fábrica"
"$SG" list nada >/dev/null 2>&1; [ $? -ne 0 ]; check $? "bk song list con un género que no existe falla"
check_eq "$("$SG" bar-status)" "off		" "bk song bar-status sin música: off"
"$SG" now >/dev/null 2>&1; [ $? -ne 0 ]; check $? "bk song now sin música falla con un mensaje"
unset BK_SONG_CACHE BK_CONFIG_DIR

# Nombrar agentes sin saber la ventana: bk say sin agente (al que te espera), por número o por palabra.
# En un servidor de tmux aparte (TMUX_TMPDIR) para no tocar el tuyo.
if command -v tmux >/dev/null; then
  NT="$TMP/names"; mkdir -p "$NT/state" "$NT/tmux"
  (
    export TMUX_TMPDIR="$NT/tmux" BK_STATE_DIR="$NT/state" BK_CACHE_DIR="$NT/cache"; unset TMUX
    tmux new-session -d -s agents -n i218 "cat > $NT/i218.txt"
    tmux new-window -d -t agents: -n pr241 "printf '⏺ Listo el borrador.\\n  ¿La publico en el PR?\\n'; cat > $NT/pr241.txt"
    tmux new-window -d -t agents: -n f237 "cat > $NT/f237.txt"
    for w in i218 pr241 f237; do
      p=$(tmux list-panes -t "agents:$w" -F '#{pane_id}')
      case $w in i218) st=working ;; pr241) st=done ;; f237) st=waiting ;; esac
      jq -n --arg p "$p" --arg s "$st" '{state: $s, pane: $p, ts: (now | floor), cwd: "/tmp"}' > "$NT/state/$w.json"
    done
    sleep 0.3
    "$ROOT/bin/bk" say "hola" </dev/null >/dev/null 2>"$NT/err"; echo "auto2=$?"
    grep -c '^  ?' "$NT/err" | sed 's/^/listados=/'
    jq '.state = "working"' "$NT/state/f237.json" > "$NT/x" && mv "$NT/x" "$NT/state/f237.json"
    "$ROOT/bin/bk" say "sí, publicala" </dev/null >/dev/null 2>&1; echo "auto1=$?"
    "$ROOT/bin/bk" say 218 "por número" </dev/null >/dev/null 2>&1; echo "num=$?"
    "$ROOT/bin/bk" say '#237' "con numeral" </dev/null >/dev/null 2>&1; echo "hash=$?"
    "$ROOT/bin/bk" say nada "no existe" </dev/null >/dev/null 2>&1; echo "nada=$?"
    "$ROOT/bin/bk" ls </dev/null | head -1 | cut -c1-7 | sed 's/^/primero=/'
    sleep 0.3; tmux kill-server
  ) > "$NT/out" 2>&1
  check_eq "$(grep '^auto2=' "$NT/out")" "auto2=1" "bk say sin agente con dos esperando: no adivina, falla"
  check_eq "$(grep '^listados=' "$NT/out")" "listados=2" "y lista a los dos que esperan"
  check_eq "$(grep '^auto1=' "$NT/out")" "auto1=0" "bk say sin agente con uno esperando: le escribe a ese"
  check_eq "$(cat "$NT/pr241.txt" 2>/dev/null)" "sí, publicala" "el que terminó preguntando recibe la respuesta"
  check_eq "$(cat "$NT/i218.txt" 2>/dev/null)" "por número" "bk say 218: por el número de la ventana"
  check_eq "$(cat "$NT/f237.txt" 2>/dev/null)" "con numeral" "bk say '#237': también con numeral"
  check_eq "$(grep '^nada=' "$NT/out")" "nada=1" "bk say a algo que no existe falla"
  check_eq "$(grep '^primero=' "$NT/out")" "primero=? pr241" "bk ls pone primero al que te espera"
fi

# Chats con nombre: bk say/go por el nombre del chat (bk name o @bk_name), nunca elegidos solos
if command -v tmux >/dev/null; then
  CT="$TMP/chats"; mkdir -p "$CT/state" "$CT/tmux"
  (
    export TMUX_TMPDIR="$CT/tmux" BK_STATE_DIR="$CT/state" BK_CACHE_DIR="$CT/cache" BK_CLIENT=nocliente; unset TMUX
    tmux new-session -d -s agents -n i218 "cat > $CT/i218.txt"
    tmux new-session -d -s bk-kit -n main "cat > $CT/chat1.txt"
    tmux new-session -d -s bk-kit-2 -n main "cat > $CT/chat2.txt"
    BK_FROM=bk-kit-2 "$ROOT/bin/bk" name orquestador >/dev/null 2>&1; echo "name=$?"
    echo "nombre=$(BK_FROM=bk-kit-2 "$ROOT/bin/bk" name)"
    "$ROOT/bin/bk" say orquestador "hola orq" </dev/null >/dev/null 2>&1; echo "say=$?"
    "$ROOT/bin/bk" say "a nadie" </dev/null >/dev/null 2>&1; echo "auto=$?"
    "$ROOT/bin/bk" stop orquestador </dev/null >/dev/null 2>&1; echo "stop=$?"
    "$ROOT/bin/bk" ls </dev/null | grep -c '◇' | sed 's/^/chats=/'
    sleep 0.3; tmux kill-server
  ) > "$CT/out" 2>&1
  check_eq "$(grep '^name=' "$CT/out")" "name=0" "bk name nombra el chat del workspace"
  check_eq "$(grep '^nombre=' "$CT/out")" "nombre=orquestador" "bk name sin argumento lo muestra"
  check_eq "$(cat "$CT/chat2.txt" 2>/dev/null)" "hola orq" "bk say <nombre del chat> le escribe a ese chat"
  check_eq "$(cat "$CT/chat1.txt" 2>/dev/null)" "" "y no a otro chat"
  check_eq "$(grep '^auto=' "$CT/out")" "auto=1" "bk say sin agente no elige un chat"
  check_eq "$(grep '^stop=' "$CT/out")" "stop=1" "bk stop no cierra un chat"
  check_eq "$(grep '^chats=' "$CT/out")" "chats=2" "bk ls lista los chats"
fi

finish
