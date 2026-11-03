#!/bin/bash
# Sonido cuando un agente cambia a un estado que te importa (lo llama hooks/agent_state.sh).
#   agent_sound.sh <estado_anterior> <estado_nuevo> [question|permission]
# Uno por tipo de pedido (no por agente): terminó · te pregunta · pide permiso · error.
# macOS: sonidos del sistema con afplay. Linux: los de freedesktop con paplay o aplay, si están.
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
if [ -f "$f" ] && command -v afplay >/dev/null; then
  (afplay -v 0.6 "$f" >/dev/null 2>&1 &)
else
  f="/usr/share/sounds/freedesktop/stereo/$fd.oga"
  if [ -f "$f" ] && command -v paplay >/dev/null; then (paplay "$f" >/dev/null 2>&1 &)
  elif [ -f "$f" ] && command -v pw-play >/dev/null; then (pw-play "$f" >/dev/null 2>&1 &)
  fi
fi
exit 0
