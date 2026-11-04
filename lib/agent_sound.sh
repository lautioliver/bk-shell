#!/bin/bash
# Sonido cuando un agente cambia a un estado que te importa (lo llama hooks/agent_state.sh).
#   agent_sound.sh <estado_anterior> <estado_nuevo> [question|permission]
# Uno por tipo de pedido (no por agente): terminó · te pregunta · pide permiso · error.
# macOS: sonidos del sistema con afplay. Linux: los de freedesktop con paplay o aplay, si están.
# Si suena bk song, baja la radio mientras dura el aviso.
# Apagar: bk sound off. BK_SOUND_DRY=1 imprime el nombre en vez de sonar (tests).
prev="${1:-}" new="${2:-}" wait="${3:-}"
[ "$prev" = "$new" ] && exit 0   # sin cambio de estado, sin sonido (evita repetir avisos)
[ "$(cat "${BK_CONFIG_DIR:-$HOME/.config/bk}/sound" 2>/dev/null)" = off ] && exit 0

case "$new" in
  done)
    # Solo si venía trabajando: "done" desde libre es el aviso de inactividad, no un final
    case "$prev" in working|background|waiting) s=done ;; *) exit 0 ;; esac ;;
  waiting) [ "$wait" = question ] && s=question || s=permission ;;
  error) s=error ;;
  *) exit 0 ;;
esac

if [ -n "${BK_SOUND_DRY:-}" ]; then echo "$s"; exit 0; fi
case "$s" in
  done)       mac=Glass; fd=complete ;;
  question)   mac=Ping;  fd=dialog-question ;;
  permission) mac=Purr;  fd=dialog-warning ;;
  error)      mac=Basso; fd=dialog-error ;;
esac
f="/System/Library/Sounds/$mac.aiff"
if [ -n "${BK_SOUND_PLAYER:-}" ]; then player=(sh -c "$BK_SOUND_PLAYER")   # tests: un "sonido" falso
elif [ -f "$f" ] && command -v afplay >/dev/null; then player=(afplay -v 0.6 "$f")
else
  f="/usr/share/sounds/freedesktop/stereo/$fd.oga"
  if [ -f "$f" ] && command -v paplay >/dev/null; then player=(paplay "$f")
  elif [ -f "$f" ] && command -v pw-play >/dev/null; then player=(pw-play "$f")
  else exit 0
  fi
fi

# Si suena la radio de bk song, la baja mientras suena el aviso y después la deja como estaba.
# El candado evita que dos avisos seguidos guarden el volumen ya bajado como el "original".
SONG_CACHE="${BK_SONG_CACHE:-$HOME/.cache/bk}"
SOCK="$SONG_CACHE/song.sock" LOCK="$SONG_CACHE/song.duck"
mpv_vol() { printf '%s\n' "$1" | nc -U -w 1 "$SOCK" 2>/dev/null | head -n 1; }
play_ducked() {
  local orig="" mine=""
  # un candado de más de 30 s es de un aviso que murió a la mitad
  if [ -d "$LOCK" ]; then
    local m; m=$(stat -f %m "$LOCK" 2>/dev/null || stat -c %Y "$LOCK" 2>/dev/null || echo 0)
    [ $(( $(date +%s) - m )) -gt 30 ] && rmdir "$LOCK" 2>/dev/null
  fi
  if [ -S "$SOCK" ] && command -v nc >/dev/null && mkdir "$LOCK" 2>/dev/null; then
    mine=1
    orig=$(mpv_vol '{"command":["get_property","volume"]}' | sed -n 's/.*"data": *\([0-9.]*\).*/\1/p')
    orig=${orig%.*}
    if [[ "$orig" =~ ^[0-9]+$ ]] && [ "$orig" -gt 0 ]; then
      mpv_vol "{\"command\":[\"set_property\",\"volume\",$(( orig * 35 / 100 ))]}" >/dev/null
    else
      orig=""
    fi
  fi
  "${player[@]}" >/dev/null 2>&1
  if [ -n "$orig" ]; then
    sleep 0.3
    mpv_vol "{\"command\":[\"set_property\",\"volume\",$orig]}" >/dev/null
  fi
  if [ -n "$mine" ]; then rmdir "$LOCK" 2>/dev/null || true; fi
}
(play_ducked &)
exit 0
