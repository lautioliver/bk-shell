#!/usr/bin/env bash
# Proporciones del workspace: lib/layout.conf es la única fuente (bin/bk la carga, lib/gentle.py la lee).
# Con un tmux FALSO en el PATH que solo contesta y anota: nunca toca un tmux de verdad.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
cat > "$TMP/bin/tmux" <<SH
#!/usr/bin/env bash
echo "\$*" >> "$TMP/tmux.log"
case "\$1" in
  display-message) echo "\${FAKE_W:-200}" ;;
  list-panes) printf '%%1 bk rail\n%%2 bk sidebar\n' ;;
esac
SH
chmod +x "$TMP/bin/tmux"

# Una copia del repo, para poder cambiar layout.conf sin tocar el real
COPY="$TMP/ws"; mkdir -p "$COPY"; cp -R "$ROOT/bin" "$ROOT/lib" "$COPY/"
conf() { printf 'SIDE_W=%s\nRAIL_PCT=%s\nRAIL_MIN=%s\n' "$1" "$2" "$3" > "$COPY/lib/layout.conf"; }
layout() {   # layout <ancho de ventana>: lo que `bk _layout` le pide a tmux
  : > "$TMP/tmux.log"
  FAKE_W="$1" PATH="$TMP/bin:$PATH" HOME="$TMP/home" bash "$COPY/bin/bk" _layout bk-t >/dev/null 2>&1
}
resize() { grep -E "resize-pane -t $1 " "$TMP/tmux.log" | grep -oE -- '-x [0-9]+' | head -1; }

conf 5 34 46
layout 200
check_eq "$(resize %1)" "-x 68" "riel: 34 % de 200 columnas"
check_eq "$(resize %2)" "-x 5" "tira: SIDE_W"
layout 100
check_eq "$(resize %1)" "-x 46" "riel: con 100 columnas manda el mínimo"

conf 7 40 30
layout 200
check_eq "$(resize %1)" "-x 80" "cambiar layout.conf cambia el riel (40 % de 200)"
check_eq "$(resize %2)" "-x 7" "y la tira"
layout 50
check_eq "$(resize %1)" "-x 30" "y el mínimo del riel"

# El workspace nuevo también las usa (split-window del riel y de la tira)
grep -qE 'split-window .*-l "\$\{RAIL_PCT\}%"' "$ROOT/bin/bk"; check $? "bk crea el riel con RAIL_PCT"
grep -qE 'split-window .*-l "\$SIDE_W"' "$ROOT/bin/bk"; check $? "bk crea la tira con SIDE_W"
# Ningún número suelto de proporciones en bin/bk
SUELTO=$(grep -nE "34 */ *100|-l 34%|-hb -l 5 |-x 5\b|-lt 46|=46" "$ROOT/bin/bk" || true)
check_eq "$SUELTO" "" "sin anchos sueltos en bin/bk"

# bash y python leen lo mismo, con valores que NO son los de por defecto (sino da igual de dónde los saquen)
conf 7 40 30
PYV=$(python3 -c "import sys; sys.path.insert(0, '$COPY/lib'); import gentle as g; print(g.SIDE_W, g.RAIL_PCT, g.RAIL_MIN)")
BV=$(bash -c ". '$COPY/lib/layout.conf'; echo \$SIDE_W \$RAIL_PCT \$RAIL_MIN")
check_eq "$PYV" "7 40 30" "gentle lee layout.conf (valores distintos a los de por defecto)"
check_eq "$BV" "7 40 30" "y bash lee el mismo archivo"
finish
