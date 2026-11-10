#!/usr/bin/env bash
# Proporciones del workspace: lib/layout.conf es la única fuente (bin/bk la carga, lib/gentle.py la lee), y
# bk _layout: el chat no baja de LAYOUT_CHAT columnas y, sin lugar para el riel, pasa a zoom.
# Con un tmux FALSO en el PATH que solo contesta y anota: nunca toca un tmux de verdad.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/home" "$TMP/tmp"
cat > "$TMP/bin/tmux" <<SH
#!/usr/bin/env bash
echo "\$*" >> "$TMP/tmux.log"
case "\$1 \$*" in
  display-message*window_width*) echo "\${FAKE_W:-200}" ;;
  display-message*window_zoomed_flag*) echo "\${FAKE_ZOOM:-0}" ;;
  show-options*@bk_autozoom*) echo "\${FAKE_AUTO:-}" ;;
  list-panes*) printf '%%0 claude\n%%1 bk rail\n%%2 bk sidebar\n' ;;
esac
SH
chmod +x "$TMP/bin/tmux"

# Una copia del repo, para poder cambiar layout.conf sin tocar el real
COPY="$TMP/ws"; mkdir -p "$COPY"; cp -R "$ROOT/bin" "$ROOT/lib" "$COPY/"
conf() { printf 'SIDE_W=%s\nRAIL_PCT=%s\nRAIL_MIN=%s\nLAYOUT_CHAT=%s\nLAYOUT_RAIL=%s\n' "$@" > "$COPY/lib/layout.conf"; }
layout() {   # layout <ancho de ventana> [sesión]: lo que `bk _layout` le pide a tmux
  : > "$TMP/tmux.log"
  FAKE_W="$1" PATH="$TMP/bin:$PATH" HOME="$TMP/home" TMPDIR="$TMP/tmp" TMUX='' TMUX_PANE='' \
    bash "$COPY/bin/bk" _layout "${2:-bk-t}" >/dev/null 2>&1
}
resize() { grep -E "resize-pane -t $1 " "$TMP/tmux.log" | grep -oE -- '-x [0-9]+' | head -1; }
zoom() { grep -cE "resize-pane -Z -t %0" "$TMP/tmux.log"; }

# Con el layout.conf del repo (el de verdad): la tira de 14 y el riel al 34 %
cp "$ROOT/lib/layout.conf" "$COPY/lib/layout.conf"
layout 200
check_eq "$(resize %1)" "-x 68" "riel: 34 % de 200 columnas"
check_eq "$(resize %2)" "-x 14" "tira: SIDE_W, 14 columnas"
check_eq "$(zoom)" "0" "con lugar, el chat no pasa a pantalla completa"
layout 140
check_eq "$(resize %1)" "-x 44" "riel: se achica para dejarle 80 columnas al chat (140 - 80 - 2 - 14)"
layout 100
check_eq "$(resize %1)" "" "sin lugar para el riel (menos de LAYOUT_RAIL) no lo redimensiona"
check_eq "$(zoom)" "1" "y el chat pasa a pantalla completa"
check_eq "$(grep -c 'set-option -w -t bk-t:main @bk_autozoom 1' "$TMP/tmux.log")" "1" "y marca el zoom como suyo (@bk_autozoom)"
FAKE_ZOOM=1 FAKE_AUTO=1 layout 200
check_eq "$(zoom)$(resize %1)" "1-x 68" "zoom suyo y vuelve a haber lugar: lo deshace y acomoda el riel"
FAKE_ZOOM=1 layout 200
check_eq "$(zoom)$(resize %1)" "0" "zoom hecho a mano: no lo toca"
layout 200 agents
check_eq "$(wc -l < "$TMP/tmux.log" | tr -d ' ')" "0" "el hook es global: otra sesión (no bk*) no se toca"

# El lock por sesión: uno viejo (de un bk que murió) vence; uno fresco ajeno no se borra
mkdir "$TMP/tmp/bk-layout-bk-t.lock"; touch -t 200001010000 "$TMP/tmp/bk-layout-bk-t.lock"
layout 200
check_eq "$(resize %1)" "-x 68" "un lock viejo (más de 5 s) vence y acomoda igual"
check "$([ ! -d "$TMP/tmp/bk-layout-bk-t.lock" ]; echo $?)" "y al terminar suelta el lock"
mkdir "$TMP/tmp/bk-layout-bk-t.lock"
( sleep 2; touch "$TMP/tmp/bk-layout-bk-t.lock" ) &   # sigue vivo: lo renueva mientras esperamos
layout 200; wait
check_eq "$(resize %1)" "" "con el lock de otro vivo, no acomoda"
check "$([ -d "$TMP/tmp/bk-layout-bk-t.lock" ]; echo $?)" "y no le borra el lock al otro"
rmdir "$TMP/tmp/bk-layout-bk-t.lock"

conf 7 40 30 10 5
layout 200
check_eq "$(resize %1)" "-x 80" "cambiar layout.conf cambia el riel (40 % de 200)"
check_eq "$(resize %2)" "-x 7" "y la tira"
layout 50
check_eq "$(resize %1)" "-x 30" "y el mínimo del riel"
conf 7 40 30 30 25
layout 50
check_eq "$(resize %1)$(zoom)" "1" "y lo que el chat no cede y cuándo se esconde el riel (50 - 30 - 2 - 7 = 11 < 25: zoom)"

# El workspace nuevo también las usa (split-window del riel y de la tira)
grep -qE 'split-window .*-l "\$\{RAIL_PCT\}%"' "$ROOT/bin/bk"; check $? "bk crea el riel con RAIL_PCT"
grep -qE 'split-window .*-l "\$SIDE_W"' "$ROOT/bin/bk"; check $? "bk crea la tira con SIDE_W"
# Ningún número suelto de proporciones en bin/bk
SUELTO=$(grep -nE "34 */ *100|-l 34%|-hb -l [0-9]+ |-x [0-9]+\b|-lt 46|=46|SIDE_W=|LAYOUT_CHAT=|LAYOUT_RAIL=" "$ROOT/bin/bk" || true)
check_eq "$SUELTO" "" "sin anchos sueltos en bin/bk"
# El reacomodo al cambiar de tamaño es un hook global de tmux/bk.conf, no uno por sesión
grep -qE "^set-hook -g window-resized .*bk _layout #\{session_name\}" "$ROOT/tmux/bk.conf"; check $? "bk.conf registra window-resized → bk _layout"
check_eq "$(grep -c 'set-hook.*client-resized' "$ROOT/bin/bk")" "0" "bk ya no registra client-resized por sesión"

# bash y python leen lo mismo, con valores que NO son los de por defecto (sino da igual de dónde los saquen)
conf 7 40 30 10 5
PYV=$(python3 -B -c "import sys; sys.path.insert(0, '$COPY/lib'); import gentle as g; print(g.SIDE_W, g.RAIL_PCT, g.RAIL_MIN, g.LAYOUT['LAYOUT_CHAT'], g.LAYOUT['LAYOUT_RAIL'])")
BV=$(bash -c ". '$COPY/lib/layout.conf'; echo \$SIDE_W \$RAIL_PCT \$RAIL_MIN \$LAYOUT_CHAT \$LAYOUT_RAIL")
check_eq "$PYV" "7 40 30 10 5" "gentle lee layout.conf (valores distintos a los de por defecto)"
check_eq "$BV" "7 40 30 10 5" "y bash lee el mismo archivo"
finish
