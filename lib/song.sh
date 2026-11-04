#!/bin/bash
# bk song — radios en segundo plano con mpv, manejadas desde la terminal.
# mpv corre desacoplado de la terminal con un socket IPC; los demás subcomandos le hablan por ahí.
# Las radios van por género (techno, house, rock, reggaeton, cachengue) y podés sumar las tuyas.
set -euo pipefail

CACHE="${BK_SONG_CACHE:-$HOME/.cache/bk}"     # BK_SONG_CACHE (y BK_SONG_VOL=0): probar sin tocar la música que suena
CONF="${BK_CONFIG_DIR:-$HOME/.config/bk}"
MINE="$CONF/song-stations"                   # tus radios: clave|género|nombre|url, una por línea
SOCK="$CACHE/song.sock"
NOW="$CACHE/song.station"   # "clave<TAB>nombre<TAB>género" de lo que está sonando
HIST="$CACHE/song.history"  # "hora<TAB>estación<TAB>tema", lo último al final
LOG="$CACHE/song.log"
VIZ='@viz:lavfi=[astats=metadata=1:reset=1]'   # niveles por canal para el gráfico de bk song ui
LIB="$(dirname "$(readlink -f "$0")")"

die()  { printf 'bk song: %s\n' "$*" >&2; exit 1; }
info() { printf '\033[2m%s\033[0m\n' "$*"; }
bar()  { [ -n "${BK_SONG_CACHE:-}" ] || sketchybar --trigger song_update >/dev/null 2>&1 || true; }

# clave|género|nombre|url  (streams verificados con mpv; el primero de cada género es el default del género)
BUILTIN='techno|techno|Sunshine Live · Techno|http://stream.sunshine-live.de/techno/mp3-192/stream.sunshine-live.de/
technofm|techno|Techno.FM|http://stream.techno.fm/radio.mp3
deeptech|techno|1.FM · Deep Techno & Deep House|http://strm112.1.fm/deeptech_mobile_mp3
house|house|1.FM · Deep House|http://strm112.1.fm/deephouse_mobile_mp3
ibiza|house|Ibiza Global Radio|http://ibizaglobalradio.streaming-pro.com:8024/
dance|house|Intense Radio · We love Dance|https://secure.live-streams.nl/main
rockpop|rock|Rock & Pop 95.9|https://playerservices.streamtheworld.com/api/livestream-redirect/ROCKANDPOP.mp3
mega|rock|Mega 98.3 · Rock nacional|https://mega.stweb.tv/mega983/live/playlist.m3u8
prn|rock|PRN 103.5 · Puro Rock Nacional|https://srv591.grupomultimedios.com.ar:9902/listen/radio_zeta/radio.mp3
caroline|rock|Radio Caroline|http://78.129.202.200:8040/
vorterix|rock|Vorterix|https://ice2.edge-apps.net/radio1_high-20057.audio
perreo|reggaeton|Latina Reggaeton|http://latinareggaeton.ice.infomaniak.ch/latinareggaeton.mp3
chocolate|reggaeton|Chocolate FM|http://streaming5.elitecomunicacion.es:8082/live.mp3
popu|cachengue|La Popu · cumbia y cuarteto|https://liveradio.mediainbox.net/popular.mp3
cumbiambera|cachengue|FM Cumbiambera|https://streaming1.locucionar.com/proxy/fmcumbiambera?mp=/stream
cumbias|cachengue|Cumbias Inmortales|https://panel.retrolandigital.com/listen/cumbias_inmortales_radio/listen'

# Todas las estaciones: las tuyas primero (pisan a una de fábrica con la misma clave)
stations() {
  { if [ -f "$MINE" ]; then grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$MINE" || true; fi
    printf '%s\n' "$BUILTIN"; } | awk -F'|' 'NF >= 4 && !seen[$1]++'
}
genres() { stations | awk -F'|' '!g[$2]++ { print $2 }'; }

usage() {
  cat <<'EOF'
uso: bk song [comando]

  bk song                   reproduce la radio default (techno)
  bk song <estación>        esa estación (bk song list)
  bk song <género>          la primera radio del género: techno, house, rock, reggaeton, cachengue
                            (o un género tuyo). Si no existe, busca en radio-browser.info, primero
                            en Argentina y después en todo el mundo (minimal, trance, jazz...)
  bk song <url>             cualquier stream o link (YouTube y SoundCloud andan con yt-dlp)
  bk song next | prev       siguiente / anterior estación del mismo género
  bk song pause             pausa / reanuda
  bk song ui                pantalla en vivo: gráfico, géneros, últimos temas y control con teclas
  bk song now               qué estación y qué tema están sonando
  bk song hist [N]          los últimos N temas que sonaron (default 15)
  bk song vol [+N|-N|N]     volumen (sin argumento: el actual)
  bk song fav [clave] [género]                   guarda lo que suena entre tus radios
  bk song add <clave> <url> [género] [nombre…]   suma una radio tuya
  bk song rm <clave>        saca una radio tuya
  bk song list [género]     estaciones por género (♫ la que suena, ★ las tuyas)
  bk song genres            géneros disponibles
  bk song stop              corta la música
EOF
}

# ---------- mpv ----------
# Sin pipes hacia grep -q/head: con pipefail, el SIGPIPE de nc da falsos "no está corriendo"
running() {
  [ -S "$SOCK" ] || return 1
  local r; r=$(ipc '{"command":["get_property","pid"]}')
  [[ "$r" == *'"error":"success"'* ]]
}

# ipc <json> -> primera línea de la respuesta de mpv
ipc() {
  local r; r=$(printf '%s\n' "$1" | nc -U -w 1 "$SOCK" 2>/dev/null) || true
  printf '%s\n' "${r%%$'\n'*}"
}

# prop <nombre> -> valor (vacío si no existe)
prop() { ipc "{\"command\":[\"get_property\",\"$1\"]}" | jq -r 'select(.error == "success") | .data // empty | tostring' 2>/dev/null; }

need_running() { running || die "no está sonando nada (bk song para arrancar)"; }

kill_player() {
  pkill -f -- "--input-ipc-server=$SOCK" 2>/dev/null || true
  rm -f "$SOCK" "$NOW"
}

# play <clave> <nombre> <url> [género]
play() {
  command -v mpv >/dev/null || die "falta mpv (brew install mpv)"
  mkdir -p "$CACHE"
  # Cambiar de estación conserva el volumen que tenías
  local vol="${BK_SONG_VOL:-100}" prev_url="" prev_now=""
  if running; then
    vol=$(prop volume); vol=${vol%.*}
    prev_url=$(prop path); prev_now=$(cat "$NOW" 2>/dev/null || true)   # para volver si lo nuevo no suena
  fi
  [[ "$vol" =~ ^[0-9]+$ ]] || vol=100
  kill_player
  nohup mpv --no-video --volume="$vol" --af="$VIZ" --no-terminal --idle=no --cache=yes --demuxer-max-bytes=2MiB \
    --ytdl-format=bestaudio/best --input-ipc-server="$SOCK" --title="bk-song" \
    --log-file="$LOG" --msg-level=all=warn "$3" >/dev/null 2>&1 </dev/null &
  disown
  printf '%s\t%s\t%s\n' "$1" "$2" "${4:-}" > "$NOW"
  # Esperar a que de verdad suene (hay audio decodificado), no solo a que mpv levante el socket:
  # un link de YouTube que no existe abre el socket y muere unos segundos después
  local i limit=40; [[ "$3" == *youtu* || "$3" == *soundcloud* ]] && limit=150
  for i in $(seq 1 "$limit"); do
    if running && [ -n "$(prop audio-codec-name)" ]; then
      printf '♫ %s\n' "$2"; bar
      return 0
    fi
    if [ "$i" -gt 5 ] && ! pgrep -f -- "--input-ipc-server=$SOCK" >/dev/null; then break; fi
    sleep 0.2
  done
  kill_player
  # el ERROR de yt-dlp dice el motivo ("video no disponible"); si no hay, la última queja de mpv
  local why; why=$(grep -m1 -o 'ERROR: [^"\]*' "$LOG" 2>/dev/null | sed 's/^ERROR: \(\[[^]]*\] [^:]*: \)\{0,1\}//')
  [ -n "$why" ] || why=$(grep -iE 'error|failed' "$LOG" 2>/dev/null | grep -v 'Exiting' | tail -n 1 | sed 's/^.*\] //')
  local back=""
  if [ -n "$prev_url" ] && [ -z "${BK_SONG_RESTORING:-}" ]; then
    local pk pn pg; IFS=$'\t' read -r pk pn pg <<<"$prev_now"
    BK_SONG_RESTORING=1 BK_SONG_VOL="$vol" play "${pk:-url}" "${pn:-?}" "$prev_url" "${pg:-}" >/dev/null 2>&1 \
      && back=" · sigue sonando ${pn:-lo de antes}"
  fi
  die "no pude reproducir eso${why:+: $why}${back} (log: $LOG)"
}

# ---------- estaciones ----------
station_line() { stations | awk -F'|' -v k="$1" '$1 == k { print; exit }'; }

play_key() {
  local line k g name url; line=$(station_line "$1")
  [ -n "$line" ] || return 1
  IFS='|' read -r k g name url <<<"$line"
  play "$k" "$name" "$url" "$g"
}

play_genre() {
  local k; k=$(stations | awk -F'|' -v g="$1" '$2 == g { print $1; exit }')
  [ -n "$k" ] || return 1
  play_key "$k"
}

# Género que no es de la lista: la radio más votada de radio-browser con ese tag, primero en Argentina
play_tag() {
  local tag="$1" res="" name url cc
  info "buscando radios '$tag' en radio-browser.info…"
  for cc in AR ""; do
    res=$(curl -fsS -m 10 -A 'bk-song/1' -G 'https://de1.api.radio-browser.info/json/stations/search' \
            --data-urlencode "tag=$tag" ${cc:+-d countrycode=$cc} -d order=votes -d reverse=true \
            -d hidebroken=true -d limit=1) || die "radio-browser.info no respondió"
    if [ "$(jq length <<<"$res")" -gt 0 ]; then break; fi
  done
  name=$(jq -r '.[0].name // empty' <<<"$res" | tr -d '\t\n|' | sed 's/^ *//' | cut -c1-60)
  url=$(jq -r '.[0].url_resolved // empty' <<<"$res")
  [[ "$url" =~ ^https?:// ]] || die "no encontré radios con el tag '$tag' (bk song genres)"
  play "tag:$tag" "$name" "$url" "$tag"
}

play_url() {
  local name
  case "$1" in
    *youtube.com*|*youtu.be*) name="YouTube" ;;
    *soundcloud.com*)         name="SoundCloud" ;;
    *) name=$(sed -E 's#^https?://([^/:?]+).*#\1#' <<<"$1") ;;
  esac
  play "url" "$name" "$1" ""
}

# cmd_step <1|-1>: siguiente o anterior dentro del género que suena (circular)
cmd_step() {
  local cur="" g="" line keys n i
  if [ -f "$NOW" ]; then IFS=$'\t' read -r cur _ g < "$NOW" || true; fi
  line=$(station_line "$cur"); [ -n "$line" ] && g=$(cut -d'|' -f2 <<<"$line")
  keys=$(stations | awk -F'|' -v g="$g" '$2 == g { print $1 }')
  [ -n "$keys" ] || keys=$(stations | cut -d'|' -f1)
  n=$(wc -l <<<"$keys" | tr -d ' ')
  i=$(awk -v c="$cur" '$0 == c { print NR; exit }' <<<"$keys")
  if [ -z "$i" ]; then i=1; else i=$(( (i - 1 + $1 + n) % n + 1 )); fi
  play_key "$(sed -n "${i}p" <<<"$keys")"
}

# ---------- historial ----------
# Anota el tema si cambió (lo llaman bar-status y now, que la tira y la barra ya consultan seguido)
note_title() {
  local station="$1" title="$2" last=""
  [ -n "$title" ] || return 0
  if [ -f "$HIST" ]; then last=$(tail -n 1 "$HIST" | cut -f3); fi
  [ "$last" = "$title" ] && return 0
  mkdir -p "$CACHE"
  printf '%s\t%s\t%s\n' "$(date +%H:%M)" "$station" "$title" >> "$HIST"
  # que no crezca para siempre
  if [ "$(wc -l < "$HIST")" -gt 500 ]; then tail -n 300 "$HIST" > "$HIST.tmp" && mv "$HIST.tmp" "$HIST"; fi
}

cur_title() {
  local t; t=$(prop metadata | jq -r '."icy-title" // .title // empty' 2>/dev/null | tr -d '\t\n' || true)
  [ -n "$t" ] || t=$(prop media-title | tr -d '\t\n')
  # mpv usa la url o el nombre del archivo cuando no hay título: eso no es un tema
  case "$t" in http*|*.mp3|*.aac|*.m3u8|stream|live|radio|listen|main) t="" ;; esac
  printf '%s' "$t"
}

cmd_now() {
  need_running
  local name title paused vol
  name=$(cut -f2 "$NOW" 2>/dev/null || true)
  title=$(cur_title)
  paused=$(prop pause); vol=$(prop volume)
  note_title "$name" "$title"
  printf '♫ %s%s\n' "${name:-?}" "$([ "$paused" = true ] && echo '  (en pausa)')"
  [ -n "$title" ] && printf '  %s\n' "$title"
  printf '  vol %s%%\n' "${vol%.*}"
}

cmd_hist() {
  local n="${1:-15}"
  [[ "$n" =~ ^[0-9]+$ ]] || die "bk song hist [N]"
  [ -s "$HIST" ] || { echo "todavía no sonó nada con datos del tema"; return 0; }
  tail -n "$n" "$HIST" | awk -F'\t' '{ printf "\033[2m%s\033[0m  %s  \033[2m· %s\033[0m\n", $1, $3, $2 }'
}

# Para Sketchybar y la tira: "estado<TAB>estación<TAB>tema" (estado: playing | paused | off)
cmd_bar_status() {
  if ! running; then
    # mpv murió solo (se cortó el stream): no dejar un socket viejo
    [ -e "$SOCK" ] && kill_player
    printf 'off\t\t\n'; return
  fi
  local name title state=playing
  name=$(cut -f2 "$NOW" 2>/dev/null || true)
  title=$(cur_title)
  note_title "$name" "$title"
  [ "$(prop pause)" = true ] && state=paused
  printf '%s\t%s\t%s\n' "$state" "$name" "$title"
}

cmd_vol() {
  need_running
  local v="${1:-}"
  case "$v" in
    "")              ;;
    [+-][0-9]*)      [[ "$v" =~ ^[+-][0-9]{1,3}$ ]] || die "volumen inválido: $v"
                     ipc "{\"command\":[\"add\",\"volume\",${v#+}]}" >/dev/null ;;
    *)               [[ "$v" =~ ^[0-9]{1,3}$ ]] || die "volumen inválido: $v (0-130, +N o -N)"
                     ipc "{\"command\":[\"set_property\",\"volume\",$v]}" >/dev/null ;;
  esac
  local cur; cur=$(prop volume); printf 'vol %s%%\n' "${cur%.*}"
}

cmd_list() {
  local cur="" only="${1:-}" mine=""
  if [ -f "$NOW" ] && running; then cur=$(cut -f1 "$NOW"); fi
  if [ -n "$only" ] && ! genres | grep -qxF -- "$only"; then die "no hay género '$only' (bk song genres)"; fi
  if [ -f "$MINE" ]; then mine=$(cut -d'|' -f1 "$MINE" | tr '\n' ' '); fi
  stations | awk -F'|' -v c="$cur" -v only="$only" -v mine=" $mine" '
    only != "" && $2 != only { next }
    $2 != g { g = $2; printf "\033[1m%s\033[0m\n", g }
    { printf "  %s%s %-12s %s\n", ($1 == c ? "♫" : " "), (index(mine, " " $1 " ") ? "★" : " "), $1, $3 }'
  info "otro género: bk song <tag> (busca en radio-browser.info) · tus radios: bk song fav / add"
}

# ---------- tus radios ----------
valid_key() { [[ "$1" =~ ^[a-z0-9][a-z0-9_-]{0,19}$ ]] || die "clave inválida: '$1' (minúsculas, números, - o _; hasta 20)"; }

save_station() {   # save_station clave género nombre url
  local k="$1" g="${2:-mías}" name="$3" url="$4"
  valid_key "$k"
  [[ "$url" =~ ^https?://[^[:space:]|]+$ ]] || die "la url tiene que empezar con http:// o https:// (sin espacios ni |)"
  [[ "$g" =~ ^[[:alnum:]áéíóúñ_-]{1,20}$ ]] || die "género inválido: '$g' (una palabra, hasta 20)"
  name=$(tr -d '|\t\n' <<<"$name" | cut -c1-60)
  mkdir -p "$CONF"
  if [ -f "$MINE" ]; then awk -F'|' -v k="$k" '$1 != k' "$MINE" > "$MINE.tmp"; mv "$MINE.tmp" "$MINE"; fi
  printf '%s|%s|%s|%s\n' "$k" "$g" "${name:-$k}" "$url" >> "$MINE"
  printf '★ %s guardada en %s (%s)\n' "$k" "$g" "${name:-$k}"
}

cmd_fav() {
  need_running
  local cur="" name="" g="" url k
  IFS=$'\t' read -r cur name g < "$NOW" || true
  url=$(prop path)
  k="${1:-}"
  if [ -z "$k" ]; then
    k="${cur#tag:}"; [ "$k" = url ] && k="$name"
    k=$(iconv -f utf-8 -t ascii//TRANSLIT <<<"$k" 2>/dev/null | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-' | cut -c1-20)
  fi
  save_station "$k" "${2:-${g:-mías}}" "$name" "$url"
}

cmd_rm() {
  local k="${1:-}"; [ -n "$k" ] || die "bk song rm <clave>"
  { [ -f "$MINE" ] && cut -d'|' -f1 "$MINE" | grep -qxF -- "$k"; } || die "'$k' no es una radio tuya (bk song list)"
  awk -F'|' -v k="$k" '$1 != k' "$MINE" > "$MINE.tmp"; mv "$MINE.tmp" "$MINE"
  echo "sacada $k"
}

case "${1:-}" in
  "")              play_key techno ;;
  stop)            running || [ -f "$NOW" ] || { echo "no estaba sonando nada"; exit 0; }
                   kill_player; bar; echo "■ música cortada" ;;
  pause)           need_running; ipc '{"command":["cycle","pause"]}' >/dev/null; bar
                   if [ "$(prop pause)" = true ]; then echo "⏸ en pausa"; else echo "▶ sonando"; fi ;;
  bar-status)      cmd_bar_status ;;
  next)            cmd_step 1 ;;
  prev)            cmd_step -1 ;;
  stations)        stations | awk -F'|' '{ print $1 "\t" $3 "\t" $2 }' ;;
  genres)          genres ;;
  ui)              command -v mpv >/dev/null || die "falta mpv (brew install mpv)"
                   running || play_key techno >/dev/null
                   # Si mpv lo arrancó una versión vieja de bk song, sumarle el medidor en caliente
                   prop af | grep -q '"viz"' || ipc "{\"command\":[\"af\",\"add\",\"$VIZ\"]}" >/dev/null
                   exec python3 "$LIB/song_ui.py" "$SOCK" "$LIB/song.sh" ;;
  now|status)      cmd_now ;;
  hist|history)    shift; cmd_hist "${1:-15}" ;;
  vol|volume)      shift; cmd_vol "${1:-}" ;;
  list|ls)         shift; cmd_list "${1:-}" ;;
  fav)             shift; cmd_fav "$@" ;;
  add)             shift; [ $# -ge 2 ] || die "bk song add <clave> <url> [género] [nombre…]"
                   k="$1" u="$2"; shift 2; g="${1:-mías}"; [ $# -gt 0 ] && shift
                   save_station "$k" "$g" "${*:-$k}" "$u" ;;
  rm)              shift; cmd_rm "${1:-}" ;;
  -h|--help|help)  usage ;;
  http://*|https://*) play_url "$1" ;;
  *)               play_key "$1" || play_genre "$1" || play_tag "$*" ;;
esac
