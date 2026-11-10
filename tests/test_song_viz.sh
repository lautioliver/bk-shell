#!/usr/bin/env bash
# bk song: el filtro del espectro (VIZ) que song.sh le pasa a mpv. Con un mpv falso que anota sus argumentos
# (no suena nada, no toca tu radio: BK_SONG_CACHE y BK_CONFIG_DIR van a un tmp) y, si hay ffmpeg, corriendo el
# filtro de verdad sobre un tono de 440 Hz.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
export BK_SONG_CACHE="$TMP/song" BK_CONFIG_DIR="$TMP/conf" HOME="$TMP/home"
mkdir -p "$TMP/bin" "$HOME"
cat > "$TMP/bin/mpv" <<EOF
#!/bin/sh
for a in "\$@"; do printf '%s\n' "\$a"; done > "$TMP/mpv.args"
EOF
chmod +x "$TMP/bin/mpv"

PATH="$TMP/bin:$PATH" "$ROOT/lib/song.sh" techno >/dev/null 2>&1   # el mpv falso no suena: song.sh avisa y sale con error
AF=$(sed -n 's/^--af=//p' "$TMP/mpv.args" 2>/dev/null)
check_eq "${AF:0:11}" "@viz:lavfi=" "bk song: mpv arranca con el filtro viz"
check_eq "$(grep -o 'bandpass=f=' <<<"$AF" | wc -l | tr -d ' ')" 56 "bk song: 28 franjas, cada una filtrada dos veces"
check_eq "$(grep -o 'amerge=inputs=[0-9]*' <<<"$AF")" "amerge=inputs=29" "bk song: el audio y las 28 franjas van juntos a astats"
grep -q 'astats=metadata=1:reset=1:measure_perchannel=RMS_level' <<<"$AF"; check $? "bk song: astats mide el RMS de cada canal"
check_eq "${AF##*,}" "pan=stereo|c0=c0|c1=c1]" "bk song: pan devuelve solo los dos canales que suenan"

if command -v ffmpeg >/dev/null; then
  G=${AF#@viz:lavfi=[}; G=${G%]}
  # El canal más fuerte entre las franjas (3…30) con un tono de 440 Hz: el de la franja de 400 Hz (12.ª → canal 14)
  LOUD=$(ffmpeg -hide_banner -loglevel error -f lavfi -i "sine=f=440:r=44100:d=0.5" -af "$G,ametadata=mode=print:file=-" \
           -f null - 2>/dev/null | awk -F'[.=]' '/RMS_level=/ && $3 >= 3 && $5 != "-inf" { db[$3] = $5 "." $6 }
             END { b = 0; for (c in db) if (b == 0 || db[c] + 0 > db[b] + 0) b = c; print b }')
  check_eq "$LOUD" 14 "bk song: con ffmpeg, un tono de 440 Hz suena en la franja de 400 Hz (de graves a agudos)"
else
  echo "# sin ffmpeg: no se corre el filtro de verdad"
fi
finish
