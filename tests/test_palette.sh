#!/usr/bin/env bash
# lib/palette.py (alt+k): cada entrada corre un comando que bk conoce, y están las del handoff.
# No abre la paleta ni corre comandos: lee ENTRIES y el case principal de bin/bk.
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

finish
