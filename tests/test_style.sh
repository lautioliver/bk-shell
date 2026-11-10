#!/usr/bin/env bash
# bk _style (ws_style): el header, el borde de los popups y el pie salen de gentle.P, de día y de noche.
# Con un tmux FALSO en el PATH que solo anota los argumentos: nunca toca un tmux de verdad.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
cat > "$TMP/bin/tmux" <<SH
#!/usr/bin/env bash
echo "\$*" >> "$TMP/tmux.log"
SH
chmod +x "$TMP/bin/tmux"

estilo() {   # estilo <night|day>: corre `bk _style` con un HOME cuya config de Ghostty dice bk-<tema>
  local home="$TMP/home-$1"
  mkdir -p "$home/Library/Application Support/com.mitchellh.ghostty" "$home/.config/bk"
  echo "theme = bk-$1" > "$home/Library/Application Support/com.mitchellh.ghostty/config.ghostty"
  : > "$TMP/tmux.log"
  HOME="$home" PATH="$TMP/bin:$PATH" TMUX_PANE= bash "$ROOT/bin/bk" _style bk-t >/dev/null 2>&1
}
val() { grep -E -- " $1 " "$TMP/tmux.log" | head -1; }   # la línea de tmux que setea esa opción (con espacios: no status-right-length)

estilo night
check_eq "$(val popup-border-style | grep -o 'fg=#[0-9A-Fa-f]*')" "fg=#F095C8" "de noche, el borde de los popups es el rosa de gentle"
check_eq "$(val status-right | grep -o 'fg=#[0-9A-Fa-f]*')" "fg=#A99CF5" "de noche, el pie va en el iris de gentle"
estilo day
check_eq "$(val popup-border-style | grep -o 'fg=#[0-9A-Fa-f]*')" "fg=#8E4F75" "de día, el borde de los popups es el rosa de día"
check_eq "$(val status-right | grep -o 'fg=#[0-9A-Fa-f]*')" "fg=#5A5596" "de día, el pie va en el iris de día"
check_eq "$(val status-style | grep -o 'bg=#[0-9A-Fa-f]*')" "bg=#EEEBF2" "de día, el fondo del header es el tinte de día"

# El pie ya no trae un color propio: lo pone ws_style
FOOT=$(HOME="$TMP/home-day" PATH="$TMP/bin:$PATH" BK_STATE_DIR="$TMP/state" bash "$ROOT/bin/bk" _footer bk-t 2>/dev/null)
case "$FOOT" in *'#[fg=#'*) check 1 "el pie no fija un color propio";; *) check 0 "el pie no fija un color propio";; esac

# Ningún color hex fijo en bin/bk ni en tmux/bk.conf, salvo el respaldo del borde de popups
HEX=$(grep -nE '#[0-9a-fA-F]{6}' "$ROOT/bin/bk" "$ROOT/tmux/bk.conf" | grep -v 'popup-border-style' || true)
check_eq "$HEX" "" "sin colores hex sueltos en bin/bk ni en bk.conf (salvo el respaldo de noche)"
finish
