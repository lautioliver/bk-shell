#!/usr/bin/env bash
# lib/palette.py (alt+k): cada entrada corre un comando que bk conoce, y están las del handoff.
# No abre la paleta ni corre comandos: lee ENTRIES y el case principal de bin/bk; las entradas propias
# (palette.d) las prueba con una pantalla falsa en el tmp.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
# shellcheck source=tests/lib.sh
. "$HERE/lib.sh"
export PYTHONDONTWRITEBYTECODE=1

out=$(python3 - "$ROOT" <<'EOF'
import re, sys, importlib.util
root = sys.argv[1]
sys.path.insert(0, f"{root}/lib")
spec = importlib.util.spec_from_file_location("palette", f"{root}/lib/palette.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
lines = open(f"{root}/bin/bk").read().splitlines()
# El case principal: desde la última línea `case "${1:-}" in` hasta su esac
start = max(i for i, l in enumerate(lines) if l.startswith('case "${1:-}" in'))
end = next(i for i in range(start, len(lines)) if lines[i].startswith("esac"))
known = set()
for l in lines[start + 1:end]:
    mm = re.match(r"^\s+([a-z][a-z0-9|-]*)\)", l)
    if mm:
        known.update(mm.group(1).split("|"))
for e in m.ENTRIES:
    print(f"cmd\t{e[2].split()[0] in known}\t{e[2]}")
cmds = {e[2]: e for e in m.ENTRIES}
for c, arg, kind in [("handoff", True, "show"), ("handoff --listos", False, "show"), ("chat --desde", True, "view")]:
    e = cmds.get(c)
    print(f"handoff\t{bool(e) and bool(e[3]) == arg and e[4] == kind}\t{c}")
EOF
) || { check 1 "no pude leer la paleta o bin/bk"; finish; exit 1; }

n_cmd=0; n_bad=0
while IFS=$'\t' read -r what good cmd; do
  case "$what" in
    cmd) n_cmd=$((n_cmd+1)); [ "$good" = True ] || { n_bad=$((n_bad+1)); check 1 "la paleta corre «bk ${cmd}», que bin/bk no conoce"; } ;;
    handoff) check "$([ "$good" = True ]; echo $?)" "la paleta tiene «bk ${cmd}» (con su argumento y su tipo)" ;;
  esac
done <<<"$out"
check "$([ "$n_cmd" -gt 0 ] && [ "$n_bad" -eq 0 ]; echo $?)" "las $n_cmd entradas corren comandos que bin/bk conoce"

# palette.d: las entradas propias de ${BK_CONFIG_DIR}/palette.d/*.tsv, después de las fijas
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/config/palette.d/carpeta.tsv"
T=$'\t'
cat >"$TMP/config/palette.d/b.tsv" <<EOF
# un comentario${T}con${T}tabs${T}x${T}run

Mías${T}Segunda${T}dos${T}${T}show
Mías${T}Con argumento${T}tres --x${T}qué${T}view
Mías${T}Faltan campos${T}cuatro${T}run
Mías${T}Tipo raro${T}cinco${T}${T}otro
Mías${T}Sin comando${T} ${T}${T}run
${T}Sin grupo${T}seis${T}${T}run
Mías${T}De más${T}siete${T}${T}run${T}extra
EOF
printf 'Mías\tPrimera\tuno\t\trun\r\n' >"$TMP/config/palette.d/a.tsv"
printf 'Mías\tNo es tsv\tocho\t\trun\n' >"$TMP/config/palette.d/c.txt"
printf '\xff\xfe roto\n' >"$TMP/config/palette.d/d.tsv"
out=$(BK_CONFIG_DIR="$TMP/config" HOME="$TMP/home" python3 - "$ROOT" <<'PY'
import sys, curses, importlib.util
root = sys.argv[1]
sys.path.insert(0, f"{root}/lib")
spec = importlib.util.spec_from_file_location("palette", f"{root}/lib/palette.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print("extra\t" + repr(m.extra_entries()))
todas = m.all_entries()
print("todas\t" + str(todas[:len(m.ENTRIES)] == m.ENTRIES and todas[len(m.ENTRIES):] == m.extra_entries()))
print("vacia\t" + repr(m.extra_entries(f"{root}/no-existe")))
# La paleta de verdad (ui) con una pantalla falsa: ni terminal ni curses de verdad
class Scr:
    def __init__(s, keys): s.keys = list(keys); s.text = []
    def getmaxyx(s): return (60, 120)
    def addnstr(s, y, x, t, n, *a): s.text.append(t[:n])
    def erase(s): s.text = []
    def move(s, *a): pass
    def refresh(s): pass
    def get_wch(s): return s.keys.pop(0)
curses.curs_set = lambda *a: None
curses.use_default_colors = lambda: None
m.G.curses_attrs = lambda: {k: 0 for k in ("rose", "petal", "muted", "iris")}
# buscar «Con argumento», enter, escribir el argumento, enter
print("ui\t" + repr(m.ui(Scr(list("con argumento") + ["\n"] + list("algo") + ["\n"]))))
s2 = Scr(["\x1b"]); m.ui(s2)
print("lista\t" + str(any("bk dos" in t for t in s2.text)))
PY
) || { check 1 "no pude leer palette.d"; finish; exit 1; }
get() { printf '%s\n' "$out" | awk -F'\t' -v k="$1" '$1 == k {print $2}'; }
check_eq "$(get extra)" "[('Mías', 'Primera', 'uno', None, 'run'), ('Mías', 'Segunda', 'dos', None, 'show'), ('Mías', 'Con argumento', 'tres --x', 'qué', 'view')]" \
  "palette.d: los .tsv en orden alfabético, sin comentarios, vacías ni mal formadas (campos de menos o de más, tipo raro, sin comando o sin grupo)"
check_eq "$(get todas)" "True" "palette.d: van después de las entradas fijas"
check_eq "$(get vacia)" "[]" "palette.d: sin la carpeta, ninguna"
check_eq "$(get ui)" "(('Mías', 'Con argumento', 'tres --x', 'qué', 'view'), 'algo')" "la paleta busca las propias y pide su argumento"
check_eq "$(get lista)" "True" "la paleta lista las propias con su comando"

finish
