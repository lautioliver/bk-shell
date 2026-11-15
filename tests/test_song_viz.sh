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
check_eq "$(grep -c '^--af' "$TMP/mpv.args" 2>/dev/null)/$(grep -c '^--input-ipc-server=' "$TMP/mpv.args" 2>/dev/null)" "0/1" \
  "bk song: la radio arranca sin el filtro del espectro (cuesta CPU; solo con bk song ui abierta)"
AF=$("$ROOT/lib/song.sh" _viz)
check_eq "${AF:0:11}" "@viz:lavfi=" "bk song: el filtro del espectro tiene la etiqueta viz"
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

# bk song ui pone el filtro al abrir, lo repone si la estación cambia (un mpv nuevo arranca sin él) y lo saca al
# cerrar. Con tests/fake_mpv.py (anota af add / af remove) y la UI en una pseudo-terminal; q la cierra.
if command -v nc >/dev/null; then
  SC="$BK_SONG_CACHE"; mkdir -p "$SC"; LOG="$TMP/mpv.log"; : >"$LOG"
  python3 "$HERE/fake_mpv.py" "$SC/song.sock" "$LOG" 70 & FAKE=$!
  for _ in $(seq 1 30); do [ -S "$SC/song.sock" ] && break; sleep 0.1; done
  waitlog() { for _ in $(seq 1 50); do [ "$(grep -c "$1" "$LOG")" -ge "$2" ] && return 0; sleep 0.1; done; return 1; }
  python3 - "$ROOT/lib/song.sh" "$TMP/bin" "$LOG" "$SC/song.sock" <<'PY' >"$TMP/ui.out" 2>&1 &
import os, pty, socket, sys, threading, time
song, fakebin, log, sock = sys.argv[1:]
def count(s):
    return open(log).read().count(s)
def wait(s, n, secs=5):
    t = time.time() + secs
    while time.time() < t and count(s) < n:
        time.sleep(0.1)
    return count(s) >= n
pid, fd = pty.fork()
if pid == 0:
    os.environ["PATH"] = fakebin + ":" + os.environ["PATH"]
    os.execvp(song, [song, "ui"])
def drain():   # leer lo que dibuja la UI: si nadie lee la terminal, se llena y la UI se traba al escribir
    try:
        while os.read(fd, 65536):
            pass
    except OSError:
        pass
threading.Thread(target=drain, daemon=True).start()
ok = wait("af add viz", 1)
time.sleep(1.5)   # la UI mira el filtro una vez por segundo: si lo agregara de nuevo, se vería acá
print("abrir", ok, count("af add viz"))
s = socket.socket(socket.AF_UNIX); s.connect(sock)   # otra estación: el mpv nuevo viene sin el filtro
s.sendall(b'{"command":["af","remove","@viz"]}\n'); s.recv(100); s.close()
print("repone", wait("af add viz", 2))
os.write(fd, b"q")
for _ in range(50):   # hasta 5 s; si no sale, cortarla (que no quede corriendo)
    done, st = os.waitpid(pid, os.WNOHANG)
    if done:
        break
    time.sleep(0.1)
else:
    os.kill(pid, 9); _, st = os.waitpid(pid, 0)
print("cerrar", count("af remove viz") >= 2, os.WEXITSTATUS(st) if os.WIFEXITED(st) else "señal")
PY
  UI=$!
  for _ in $(seq 1 120); do kill -0 "$UI" 2>/dev/null || break; sleep 0.1; done
  kill "$UI" 2>/dev/null; wait "$UI" 2>/dev/null
  check_eq "$(grep '^abrir' "$TMP/ui.out")" "abrir True 1" "bk song ui: al abrir le pone el filtro al mpv que suena, una vez"
  check_eq "$(grep '^repone' "$TMP/ui.out")" "repone True" "bk song ui: si cambia de estación (mpv sin filtro), lo vuelve a poner"
  check_eq "$(grep '^cerrar' "$TMP/ui.out")" "cerrar True 0" "bk song ui: al cerrar (q) lo saca y sale bien"
  check_eq "$(grep -c '^af add viz' "$LOG")" 2 "bk song ui: no lo agrega de más mientras ya está"
  kill "$FAKE" 2>/dev/null; wait "$FAKE" 2>/dev/null
else
  echo "# sin nc: no se prueba bk song ui con el mpv falso"
fi
finish
