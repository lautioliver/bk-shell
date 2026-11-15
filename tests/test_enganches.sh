#!/usr/bin/env bash
# Los enganches para una capa propia (otro repo que suma comandos sin tocar este):
#   on-empty    "bk" / "bk ws" sin ningún workspace abierto corre antes el comando propio on-empty
#   launch-env  antes de lanzar un agente, las líneas NOMBRE=valor de launch-env van como -e a su ventana
#   GH_CONFIG_DIR del bot (bajo la caché de bk) no pasa a bk ni a lo que bk corre
#   bk help     "Comandos propios": los que hay, con su descripción
# (palette.d está en tests/test_palette.sh.) Todo con un tmux, un bk y demás herramientas falsos primero en un
# PATH mínimo: ningún comando propio de tu PATH real ni el tmux real.
set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"; ROOT="$(dirname "$HERE")"
# shellcheck source=tests/lib.sh
. "$HERE/lib.sh"
TMP=$(cd "$(mktemp -d)" && pwd -P); trap 'rm -rf "$TMP"' EXIT
unset TMUX TMUX_PANE BK_PLUGINS BK_RESTORING BK_SESSION DRY BK_ACCOUNT BK_ACCOUNT_CLAUDE BK_FROM BK_CLIENT \
      BK_WS_SESSION GH_CONFIG_DIR GH_TOKEN
export HOME="$TMP/home" XDG_STATE_HOME="$TMP/state" BK_STATE_DIR="$TMP/agents" BK_CONFIG_DIR="$TMP/config" \
       BK_CACHE_DIR="$TMP/cache" TMUX_TMPDIR="$TMP/tmux" LOG="$TMP/log" TRAMPA="$TMP/trampa" PYTHONDONTWRITEBYTECODE=1
mkdir -p "$HOME" "$TMP/fake" "$TMP/sys" "$TMP/plugins" "$TMP/pbin" "$TMUX_TMPDIR"

# Falsos: tmux anota cada llamada (un argumento por línea) y contesta list-sessions con $FAKE_SESSIONS
cat >"$TMP/fake/tmux" <<'EOF'
#!/bin/sh
{ echo "== tmux"; for a; do printf '%s\n' "$a"; done; } >> "$LOG"
case "$1" in
  list-sessions) [ -n "${FAKE_SESSIONS:-}" ] || exit 1; printf '%b\n' "$FAKE_SESSIONS" ;;
  has-session) exit "${FAKE_HAS:-0}" ;;
  new-session|new-window) echo %1 ;;
esac
exit 0
EOF
# bk trampa: si algo llama a "bk" del PATH en vez de este árbol, queda anotado
for t in bk sketchybar osascript open curl gh; do
  printf '#!/bin/sh\necho "%s $*" >> "$TRAMPA"\nexit 0\n' "$t" >"$TMP/fake/$t"
done
chmod +x "$TMP/fake"/*
# PATH mínimo: los falsos, lo que bk necesita y el sistema (/usr/bin y /bin no traen comandos bk-*)
for t in python3 jq git; do ln -s "$(command -v "$t")" "$TMP/sys/$t"; done
P="$TMP/fake:$TMP/pbin:$TMP/sys:/usr/bin:/bin"
for t in tmux bk sketchybar osascript open curl gh; do
  [ "$(PATH="$P" command -v "$t")" = "$TMP/fake/$t" ] || { echo "Bail out! $t no es el falso"; exit 1; }
done
for d in /usr/bin /bin; do
  for f in "$d"/bk-*; do [ -e "$f" ] && { echo "Bail out! hay comandos bk-* en $d"; exit 1; }; done
done
# Desde acá, todo el test corre con ese PATH: un "bk" que se escape de la función de abajo cae en la trampa
export PATH="$P"
bk() { BK_PLUGINS="$TMP/plugins" "$ROOT/bin/bk" "$@"; }
mk() { printf '%s\n' "$2" >"$1"; chmod +x "$1"; }   # mk <ruta> <contenido>

# ---------- on-empty ----------
mk "$TMP/plugins/on-empty" '#!/bin/sh
echo "ws=$BK_WS ses=$BK_SESSION dry=$DRY state=$BK_STATE_DIR" >> "'"$TMP"'/oe"
exit "${OE_RC:-0}"'
oe() { cat "$TMP/oe" 2>/dev/null; rm -f "$TMP/oe"; }
mkdir -p "$TMP/proj"
: >"$LOG"; (cd "$TMP/proj" && bk >/dev/null 2>&1); rc=$?
check_eq "$(oe)" "ws=$ROOT ses=agents dry=0 state=$TMP/agents" \
  "bk sin sesiones de tmux: corre on-empty con BK_WS, BK_SESSION, DRY y BK_STATE_DIR"
check_eq "$rc|$(grep -cx attach "$LOG")" "0|1" "y después abre el workspace igual"
(cd "$TMP/proj" && FAKE_SESSIONS='agents\notro-bk' bk ws >/dev/null 2>&1)
check_eq "$(oe)" "ws=$ROOT ses=agents dry=0 state=$TMP/agents" "bk ws con sesiones pero ninguna bk-*: corre on-empty"
(cd "$TMP/proj" && FAKE_SESSIONS='agents\nbk-proj' bk >/dev/null 2>&1)
check_eq "$(oe)" "" "con un workspace abierto (bk-proj) no corre on-empty"
(cd "$TMP/proj" && BK_RESTORING=1 bk >/dev/null 2>&1)
check_eq "$(oe)" "" "con BK_RESTORING no corre on-empty"
# on-empty corre con BK_RESTORING=1: si llama a bk ws para reabrir, esa llamada no lo vuelve a correr
mv "$TMP/plugins/on-empty" "$TMP/on-empty.bak"
mk "$TMP/plugins/on-empty" '#!/bin/sh
n=$(cat "'"$TMP"'/oe" 2>/dev/null | wc -l); echo "restoring=${BK_RESTORING-nada}" >> "'"$TMP"'/oe"
[ "$n" -ge 4 ] || "$BK_WS/bin/bk" ws >/dev/null 2>&1'
: >"$LOG"; (cd "$TMP/proj" && bk >/dev/null 2>&1)
check_eq "$(oe | tr '\n' ' ')|$(grep -cx attach "$LOG")" "restoring=1 |2" \
  "on-empty corre con BK_RESTORING=1: un bk ws adentro abre sin volver a correrlo"
mv "$TMP/on-empty.bak" "$TMP/plugins/on-empty"
: >"$LOG"; (cd "$TMP/proj" && OE_RC=5 bk >/dev/null 2>&1); rc=$?
check_eq "$rc|$(oe | wc -l | tr -d ' ')|$(grep -cx attach "$LOG")" "0|1|1" "si on-empty falla, bk abre el workspace igual"
out=$(cd "$TMP/proj" && bk --dry-run 2>&1)
check_eq "$(oe)" "" "con --dry-run no corre on-empty"
case "$out" in *"correría $TMP/plugins/on-empty"*) r=0 ;; *) r=1 ;; esac; check $r "con --dry-run dice que lo correría"
(cd "$TMP/proj" && bk ls >/dev/null 2>&1)
check_eq "$(oe)" "" "otro comando (bk ls) no corre on-empty"
rm "$TMP/plugins/on-empty"
: >"$LOG"; (cd "$TMP/proj" && bk >/dev/null 2>&1); rc=$?
check_eq "$rc|$(grep -cx attach "$LOG")" "0|1" "sin on-empty, bk abre el workspace como siempre"

# ---------- launch-env ----------
REPO="$TMP/repo"
git init -q -b main "$REPO"; mkdir -p "$REPO/scripts/agents"; : >"$REPO/scripts/agents/guardrails.py"
mk "$TMP/plugins/launch-env" '#!/bin/sh
echo "cwd=$BK_LAUNCH_CWD role=$BK_LAUNCH_ROLE ws=$BK_WS ses=$BK_SESSION" > "'"$TMP"'/le"
echo "BK_X=uno dos"
echo "_OK2=a=b"
echo "no es variable"
echo "1MAL=x"
echo " ESPACIO=x"
echo "export Y=2"
echo "BARRILETE_ROLE=pisado"
echo "ruido de launch-env" >&2
exit "${LE_RC:-0}"'
lanza() { rm -rf "$XDG_STATE_HOME" "$TMP/le"; : >"$LOG"; (cd "$REPO" && bk "$@" review 5 2>&1); }
# Los -e de la llamada que crea la ventana del agente, uno por línea
envs() { awk '/^== tmux/ {on=0} $0 == "new-session" || $0 == "new-window" {on=1} on && prev == "-e" {print} {prev=$0}' "$LOG"; }
out=$(lanza)
check_eq "$(cat "$TMP/le" 2>/dev/null)" "cwd=$REPO role=reviewer ws=$ROOT ses=agents" \
  "launch-env recibe BK_LAUNCH_CWD, BK_LAUNCH_ROLE y el entorno de los comandos propios"
check_eq "$(envs | grep -E '^(BK_X|_OK2)=')" "BK_X=uno dos
_OK2=a=b" "las líneas NOMBRE=valor van como -e a la ventana del agente"
check_eq "$(envs | grep -cE '^(no es|1MAL| ESPACIO|export )')" 0 "las demás líneas no"
check_eq "$(envs | grep '^BARRILETE_ROLE=' | tail -1)" "BARRILETE_ROLE=reviewer" "las variables de bk les ganan (van después)"
case "$out" in *ruido*) r=1 ;; *) r=0 ;; esac; check $r "lo que launch-env dice por stderr no se muestra"
out=$(LE_RC=3 lanza)
check_eq "$(envs | grep -c '^BK_X=')|$(grep -cxE "new-(session|window)" "$LOG")" "0|1" "si launch-env falla: sin sus variables, y el agente se lanza igual"
# launch_env sola, sin set -e (como si la llamara otro lado): si launch-env falla, ninguna variable aunque
# haya impreso alguna. Con el set -e de bin/bk daría lo mismo sin el «|| return 0»; así lo cuida igual.
sed -n '/^launch_env() {/,/^}/p' "$ROOT/bin/bk" >"$TMP/launch_env.sh"
le_sola() { bash -c 'find_plugin() { printf %s "$PLUG"; }; plugin_env() { :; }; . "$1"; launch_env /x reviewer' \
              _ "$TMP/launch_env.sh"; }
check_eq "$(PLUG="$TMP/plugins/launch-env" le_sola | grep -c '^BK_X=')|$(PLUG="$TMP/plugins/launch-env" LE_RC=3 le_sola)" "1|" \
  "launch_env sin set -e (como si la llamara otro lado): si launch-env falla, ninguna variable aunque haya impreso"
out=$(lanza --dry-run)
check_eq "$([ -e "$TMP/le" ]; echo $?)|$(grep -c . "$LOG")" "1|0" "con --dry-run no corre launch-env (ni toca tmux)"
rm "$TMP/plugins/launch-env"
out=$(lanza)
check_eq "$(grep -cxE "new-(session|window)" "$LOG")|$(envs | grep -c '^BARRILETE_ROLE=')" "1|1" "sin launch-env el agente se lanza como siempre"

# ---------- el gh del bot no pasa a bk ----------
mk "$TMP/plugins/gh-env" '#!/bin/sh
printf "%s|%s" "${GH_CONFIG_DIR-nada}" "${GH_TOKEN-nada}"'
check_eq "$(GH_CONFIG_DIR="$BK_CACHE_DIR/bot/gh" GH_TOKEN=t bk gh-env)" "nada|nada" \
  "GH_CONFIG_DIR bajo \$BK_CACHE_DIR/bot/: bk saca GH_CONFIG_DIR y GH_TOKEN"
check_eq "$(GH_CONFIG_DIR="$TMP/cache/bot" GH_TOKEN=t bk gh-env)" "nada|nada" "también si es la carpeta bot misma"
check_eq "$(unset BK_CACHE_DIR; GH_CONFIG_DIR="$HOME/.cache/bk/bot/x" GH_TOKEN=t bk gh-env)" "nada|nada" \
  "sin BK_CACHE_DIR, la caché es ~/.cache/bk"
check_eq "$(GH_CONFIG_DIR="$BK_CACHE_DIR/botnet" GH_TOKEN=t bk gh-env)" "$BK_CACHE_DIR/botnet|t" \
  "otra carpeta (…/botnet) queda como está"
check_eq "$(GH_CONFIG_DIR="$TMP/gh" GH_TOKEN=t bk gh-env)" "$TMP/gh|t" "tu GH_CONFIG_DIR queda como está"

# ---------- bk help: Comandos propios ----------
mk "$TMP/pbin/bk-hola" '#!/bin/sh
# bk hola [nombre]: saluda'
mk "$TMP/plugins/hola" '#!/bin/sh
# bk hola: el del directorio (lo tapa el del PATH)'
mk "$TMP/plugins/sin-desc" '#!/bin/sh
echo hola'
mk "$TMP/plugins/tarde" '#!/bin/sh
#
#
#
#
# bk tarde: en la línea 6, no cuenta'
mk "$TMP/plugins/otro" '#!/bin/sh
# bk otrox: es de otro nombre
# bk otro  : la buena'
mk "$TMP/pbin/bk-ls" '#!/bin/sh
# bk ls: tapado por bk ls'
mk "$TMP/pbin/bk-ws" '#!/bin/sh
# bk ws: tapado por bk ws ("" | ws en el case)'
mk "$TMP/plugins/help" '#!/bin/sh
# bk help: tapado'
printf '#!/bin/sh\n# bk raro: con \033[31mcolor\033[0m\n' >"$TMP/plugins/raro"; chmod +x "$TMP/plugins/raro"
# C1 en UTF-8 (U+009B CSI, U+009D OSC), un 0x9B suelto, BEL y un RLO (U+202E): se van; los acentos quedan
printf '#!/bin/sh\n# bk c1: a\302\2332J\302\2350;título\007b\342\200\256c\n' >"$TMP/plugins/c1"; chmod +x "$TMP/plugins/c1"
printf '#!/bin/sh\n# bk crudo: a\2332Jb\n' >"$TMP/plugins/crudo"; chmod +x "$TMP/plugins/crudo"
printf '#!/bin/sh\n# bk quieto: no es ejecutable\n' >"$TMP/plugins/quieto"
mk "$TMP/pbin/bk-con.punto" '#!/bin/sh'
mkdir -p "$TMP/plugins/carpeta"
help=$(bk help); sec=$(printf '%s\n' "$help" | sed -n '/^Comandos propios (/,$p')
check_eq "$(printf '%s\n' "$sec" | sed -n 1p | cut -c1-17)" "Comandos propios " "bk help termina con «Comandos propios»"
check_eq "$(printf '%s\n' "$sec" | sed 1d | awk '{print $1}' | tr '\n' ' ')" "c1 crudo gh-env hola otro raro sin-desc tarde " \
  "lista los del PATH y de BK_PLUGINS, uno por nombre, sin los que tapa bk ni los que no corren"
row() { printf '%s\n' "$sec" | awk -v n="$1" '$1 == n' | sed -E 's/^  [^ ]+ +//'; }
check_eq "$(row hola)" "[nombre]: saluda" "la descripción sale de «# bk <nombre>», del que corre (el del PATH)"
check_eq "$(row otro)" "la buena" "«# bk otrox» no es la de otro"
check_eq "$(row tarde)|$(row sin-desc)" "|" "sin «# bk <nombre>» en las primeras 5 líneas, sin descripción"
check_eq "$(row raro)" "con [31mcolor[0m" "sin secuencias de control: el ESC se va"
check_eq "$(row raro | LC_ALL=C tr -d '[:print:]\n' | wc -c | tr -d ' ')" "0" "la descripción no lleva caracteres de control"
check_eq "$(row c1)" "a2J0;títulobc" "sin C1 (CSI, OSC), BEL ni RLO; los acentos quedan"
check_eq "$(row crudo)" "a�2Jb" "un byte suelto que no es UTF-8 queda como �"
check_eq "$(printf '%s\n' "$sec" | sed 1d | python3 -c 'import sys
s = sys.stdin.buffer.read().decode("utf-8", "replace")
print(sum(1 for c in s if c != "\n" and not c.isprintable()))')" "0" "ninguna descripción lleva caracteres de control (C0, DEL ni C1)"
rm -rf "$TMP/plugins"/* "$TMP/pbin"/*
check_eq "$(bk help | sed -n '/^Comandos propios (/,$p' | sed 1d)" "  (ninguno)" "sin comandos propios: (ninguno)"
check_eq "$(bk nada 2>&1 >/dev/null | grep -c '^Comandos propios (')" 1 "un comando que no existe muestra lo mismo por stderr"

check_eq "$(grep -c '^bk ' "$TRAMPA" 2>/dev/null)" "0" "nadie llamó a un bk del PATH (bin/bk se llama a sí mismo por su ruta)"
finish
