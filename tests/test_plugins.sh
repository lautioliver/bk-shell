#!/usr/bin/env bash
# Comandos propios como en git: "bk foo" que bk no conoce corre bk-foo del PATH o $BK_PLUGINS/foo.
# Y la carpeta de estado: bin/bk y cada lib que la lee usan BK_STATE_DIR, con ~/.cache/bk/agents por defecto.
set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"; ROOT="$(dirname "$HERE")"
# shellcheck source=tests/lib.sh
. "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
# Nada real: HOME, config y caché en el tmp; sin tmux (bk no lo necesita para esto)
export HOME="$TMP/home" BK_CONFIG_DIR="$TMP/config" BK_CACHE_DIR="$TMP/cache"
unset TMUX TMUX_PANE BK_STATE_DIR BK_PLUGINS
mkdir -p "$HOME" "$TMP/bin" "$TMP/plugins" "$HOME/.config/bk/plugins"
BK="$ROOT/bin/bk"

plugin() {   # plugin <ruta> <nombre que imprime> [código de salida]
  printf '#!/bin/sh\nprintf "%s:"; for a; do printf "%%s|" "$a"; done; printf "state=%%s\\n" "$BK_STATE_DIR"; exit %s\n' "$2" "${3:-0}" > "$1"
  chmod +x "$1"
}
plugin "$TMP/bin/bk-hola" hola
plugin "$TMP/plugins/chau" chau
plugin "$TMP/plugins/hola" hola-del-dir
plugin "$HOME/.config/bk/plugins/casa" casa
plugin "$TMP/plugins/falla" falla 7
plugin "$TMP/bin/bk-help" help-falso
plugin "$TMP/escapado" escapado
printf '#!/bin/sh\necho no-ejecutable\n' > "$TMP/plugins/quieto"
P="$TMP/bin:$PATH"

check_eq "$(PATH="$P" BK_PLUGINS="$TMP/plugins" "$BK" hola a "b c")" "hola:a|b c|state=$HOME/.cache/bk/agents" \
  "bk hola corre bk-hola del PATH con los argumentos (y BK_STATE_DIR por defecto en ~/.cache/bk/agents)"
check_eq "$(PATH="$P" BK_PLUGINS="$TMP/plugins" BK_STATE_DIR="$TMP/st" "$BK" chau x)" "chau:x|state=$TMP/st" \
  "bk chau corre \$BK_PLUGINS/chau, con el BK_STATE_DIR de quien llama"
check_eq "$(PATH="$P" "$BK" casa)" "casa:state=$HOME/.cache/bk/agents" \
  "sin BK_PLUGINS, la carpeta es ~/.config/bk/plugins"
PATH="$P" BK_PLUGINS="$TMP/plugins" "$BK" --dry-run chau y >/dev/null; check $? "las opciones de bk (--dry-run) no tapan al comando propio"
PATH="$P" BK_PLUGINS="$TMP/plugins" "$BK" falla >/dev/null; check_eq "$?" 7 "el código de salida del comando propio es el de bk"
# El entorno del comando propio: BK_WS (la raíz de bk), BK_SESSION (la sesión de los agentes) y DRY
printf '#!/bin/sh\nprintf "ws=%%s ses=%%s dry=%%s" "$BK_WS" "$BK_SESSION" "${DRY-sin}"\n' >"$TMP/plugins/entorno"; chmod +x "$TMP/plugins/entorno"
ent() { env -u BK_SESSION -u DRY PATH="$P" BK_PLUGINS="$TMP/plugins" "$@"; }
check_eq "$(ent "$BK" entorno)" "ws=$ROOT ses=agents dry=0" "el comando propio recibe BK_WS, BK_SESSION (agents por defecto) y DRY=0"
check_eq "$(ent "$BK" --dry-run entorno)" "ws=$ROOT ses=agents dry=1" "con --dry-run, DRY=1"
check_eq "$(ent DRY=1 BK_SESSION=otra "$BK" entorno)" "ws=$ROOT ses=otra dry=0" "BK_SESSION de quien llama pasa; un DRY=1 del entorno sin --dry-run no"
out=$(PATH="$P" BK_PLUGINS="$TMP/plugins" "$BK" help)
case "$out" in uso:*) r=0 ;; *) r=1 ;; esac; check $r "los comandos de bk ganan: bk-help del PATH no tapa bk help"
case "$out" in *'${BK_PLUGINS:-~/.config/bk/plugins}/foo'*) r=0 ;; *) r=1 ;; esac; check $r "bk help cuenta cómo sumar comandos"
# no existe, no es ejecutable, o el nombre es una ruta: uso por stderr y sale con 2, sin correr nada
for args in "nada" "quieto" "../escapado" "-x" "hola/../hola"; do
  out=$(PATH="$P" BK_PLUGINS="$TMP/plugins" "$BK" "$args" 2>&1); rc=$?
  case "$out" in uso:*) u=0 ;; *) u=1 ;; esac
  check_eq "$rc $u" "2 0" "bk $args: no hay comando propio que correr (sale con 2 y muestra el uso)"
done

# Carpeta de estado por defecto en cada lib que la lee: nunca la de otra herramienta
for m in agents rail sidebar sessions changes agents_view; do
  got=$(cd "$TMP" && python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import $m as M
print(getattr(M, 'STATE', None) or getattr(M, 'STATE_DIR', None))" "$ROOT/lib")
  check_eq "$got" "$HOME/.cache/bk/agents" "lib/$m.py: sin BK_STATE_DIR lee ~/.cache/bk/agents"
done
check_eq "$(grep -rl 'sketchybar/agents' "$ROOT/bin" "$ROOT/lib" "$ROOT/hooks" | wc -l | tr -d ' ')" 0 \
  "ningún archivo de bk lee el estado de la carpeta de Sketchybar"
[ -d "$HOME/.cache/bk/agents" ]; check_eq "$?" 1 "los comandos propios no crean la carpeta de estado"

finish
