#!/usr/bin/env bash
# usage.context_of: el contexto del último turno de una sesión, leyendo solo el final del transcript
# (lo usan la tira, el riel, bk monitor y lib/context_hook.py). Transcripts de prueba en un tmp, con HOME ahí:
# no toca ~/.claude.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
source "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
export HOME="$TMP/home"; mkdir -p "$HOME"

ctx() { python3 -B - "$ROOT/lib" "$@" <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import usage as U
cmd = sys.argv[2]
if cmd == "of":
    print(U.context_of(path=sys.argv[3]))
elif cmd == "level":
    print(U.ctx_level(int(sys.argv[3])) or "-")
elif cmd == "sid":   # transcript_of: busca en ~/.claude/projects/*/<sid>.jsonl
    print(U.transcript_of(sys.argv[3]))
elif cmd == "grow":   # lee, el archivo crece, vuelve a leer en el mismo proceso (la cache no puede quedar vieja)
    p = sys.argv[3]; a = U.context_of(path=p)
    open(p, "a").write(open(sys.argv[4]).read()); print(a, U.context_of(path=p))
PY
}

# Un turno del asistente con usage: in + cw (5m y 1h) + cr = contexto
turn() {  # in cw5 cw1h cr [extra]
  printf '{"type":"assistant","message":{"id":"m%s","model":"claude-opus-5-5","usage":{"input_tokens":%s,"output_tokens":50,"cache_creation_input_tokens":%s,"cache_creation":{"ephemeral_1h_input_tokens":%s},"cache_read_input_tokens":%s}}%s}\n' \
    "$RANDOM" "$1" "$(($2 + $3))" "$3" "$4" "${5:-}"
}

T="$TMP/a.jsonl"
{ turn 10 100 0 1000; turn 5 200 300 150000; } >"$T"
check_eq "$(ctx of "$T")" "150505" "toma el último turno y suma entrada + escrituras 5m y 1h + lectura de cache"

{ cat "$T"; turn 1 1 1 900000 ',"isSidechain":true'; } >"$TMP/b.jsonl"
check_eq "$(ctx of "$TMP/b.jsonl")" "150505" "ignora los turnos de un subagente (isSidechain)"

{ cat "$T"; printf '{"type":"assistant","message":{"id":"s","model":"<synthetic>","usage":{"input_tokens":0,"output_tokens":0}}}\n';
  printf '{"type":"user","message":{"content":"hola"},"usage":"no"}\n'; } >"$TMP/c.jsonl"
check_eq "$(ctx of "$TMP/c.jsonl")" "150505" "ignora los mensajes sintéticos y los del usuario"

# Más grande que lo que se lee del final (512 KB): la primera línea del pedazo queda cortada y no rompe
{ turn 1 0 0 7; head -c 700000 /dev/zero | tr '\0' 'x' | sed 's/.*/{"type":"user","message":{"content":"&"}}/'; echo; turn 2 0 0 333000; } >"$TMP/d.jsonl"
check_eq "$(ctx of "$TMP/d.jsonl")" "333002" "archivo grande: la primera línea del final leído viene cortada y no rompe"

# El último turno quedó a más de 512 KB del final (un tool_result enorme después): igual lo encuentra
{ turn 3 0 0 222000; head -c 700000 /dev/zero | tr '\0' 'y' | sed 's/.*/{"type":"user","message":{"content":"&"}}/'; echo; } >"$TMP/f.jsonl"
check_eq "$(ctx of "$TMP/f.jsonl")" "222003" "último turno lejos del final: lo busca en todo el archivo"

: >"$TMP/e.jsonl"
check_eq "$(ctx of "$TMP/e.jsonl")" "None" "sin turnos: None"
check_eq "$(ctx of "$TMP/no-existe.jsonl")" "None" "sin archivo: None"

turn 0 0 0 420000 >"$TMP/more.jsonl"
check_eq "$(ctx grow "$T" "$TMP/more.jsonl")" "150505 420000" "si el transcript crece, vuelve a leerlo"

check_eq "$(ctx level 199999)" "-" "por debajo de 200k: sin aviso"
check_eq "$(ctx level 200000)" "warn" "desde 200k: ámbar"
check_eq "$(ctx level 400000)" "high" "desde 400k: rojo"

# transcript_of: el sid es un nombre de archivo, no un patrón ni una ruta
mkdir -p "$HOME/.claude/projects/-tmp-demo"
turn 0 0 0 1 >"$HOME/.claude/projects/-tmp-demo/abc-123.jsonl"
check_eq "$(ctx sid abc-123)" "$HOME/.claude/projects/-tmp-demo/abc-123.jsonl" "transcript_of encuentra el de la sesión"
check_eq "$(ctx sid 'abc*')" "None" "transcript_of: un sid con * no es un patrón"
check_eq "$(ctx sid '../-tmp-demo/abc-123')" "None" "transcript_of: un sid con / no sale de la carpeta"
check_eq "$(ctx sid '')" "None" "transcript_of: sin sid, None"

finish
