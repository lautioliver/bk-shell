#!/usr/bin/env bash
# lib/tmux_guard.py: la baranda opcional que frena a un agente que iba a tirar tu tmux (kill-server sin apuntar a
# otro servidor, pkill/killall, kill <pid> del server) y pregunta antes de cerrar una sesión tuya.
# No corre nada: le pasa el JSON del hook y mira qué decide. ps y tmux son falsos (no mira tus procesos reales).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
# shellcheck source=tests/lib.sh
. "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
# Un server de tmux huérfano (ppid 1) con el 4242, un cliente con el 5555 y el server de este entorno con el 7777
printf '#!/bin/sh\nprintf "%%s\\n" " 4242     1 /usr/bin/tmux" " 5555  4000 tmux" " 6000     1 zsh"\n' >"$TMP/bin/ps"
printf '#!/bin/sh\necho 7777\n' >"$TMP/bin/tmux"
chmod +x "$TMP/bin/ps" "$TMP/bin/tmux"

# decide <comando> -> deny | ask | pass (lo que el hook le contesta a Claude Code)
decide() {
  local out
  out=$(python3 -c 'import json,sys; print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}))' "$1" \
        | PATH="$TMP/bin:$PATH" python3 "$ROOT/lib/tmux_guard.py")
  if [ -z "$out" ]; then echo pass; return; fi
  python3 -c 'import json,sys; print(json.loads(sys.argv[1])["hookSpecificOutput"]["permissionDecision"])' "$out"
}

# kill-server: frena si va a tu servidor, en cualquier forma
check_eq "$(decide 'tmux kill-server')" deny "tmux kill-server: frena"
check_eq "$(decide 'TMUX_TMPDIR=/tmp/x tmux kill-server')" deny "TMUX_TMPDIR no alcanza (con TMUX definida usa el real)"
check_eq "$(decide '/usr/bin/tmux kill-server')" deny "con la ruta de tmux: frena"
check_eq "$(decide 'tmux kill-ser')" deny "un prefijo de kill-server (tmux lo acepta): frena"
check_eq "$(decide 'echo a | tmux kill-server')" deny "en un pipeline: frena"
check_eq "$(decide 'sh -c "tmux kill-server"')" deny "dentro de sh -c: frena"
check_eq "$(decide 'x=`tmux kill-server`')" deny "entre backticks: frena"
check_eq "$(decide 'f() { tmux kill-server; }; f')" deny "dentro de una función: frena"
check_eq "$(decide '(unset TMUX; true); tmux kill-server')" deny "un unset TMUX en un subshell ya cerrado no vale"
# ...y pasa si apunta a otro servidor
check_eq "$(decide 'tmux -L prueba kill-server')" pass "tmux -L <nombre> kill-server: pasa"
check_eq "$(decide 'tmux -S /tmp/sock kill-server')" pass "tmux -S <socket> kill-server: pasa"
check_eq "$(decide 'env -u TMUX TMUX_TMPDIR=/tmp/x tmux kill-server')" pass "env -u TMUX: pasa"
check_eq "$(decide 'unset TMUX; tmux kill-server')" pass "unset TMUX antes: pasa"
check_eq "$(decide 'TMUX= tmux kill-server')" pass "TMUX= vacía: pasa"
check_eq "$(decide '(unset TMUX; tmux kill-server)')" pass "unset TMUX en el mismo subshell: pasa"
# pkill / killall / kill <pid>
check_eq "$(decide 'pkill -f tmux')" deny "pkill tmux: frena"
check_eq "$(decide 'killall tmux')" deny "killall tmux: frena"
check_eq "$(decide 'kill 4242')" deny "kill del pid de un server huérfano: frena"
check_eq "$(decide 'kill -9 7777')" deny "kill del server de este entorno (aunque no cuelgue del pid 1): frena"
check_eq "$(decide 'kill -s KILL 4242')" deny "kill -s KILL <server>: frena"
check_eq "$(decide 'kill 5555')" pass "kill de un cliente de tmux: pasa"
check_eq "$(decide 'kill 6000')" pass "kill de otro proceso: pasa"
# kill-session: pregunta solo por las tuyas
check_eq "$(decide 'tmux kill-session -t bk-demo')" ask "kill-session de un workspace: pregunta"
check_eq "$(decide 'tmux kill-session -t =agents')" ask "kill-session de agents: pregunta"
check_eq "$(decide 'tmux kill-ses -t bk-demo')" ask "un prefijo de kill-session: pregunta"
check_eq "$(decide 'tmux kill-session -t prueba')" pass "kill-session de otra sesión: pasa"
check_eq "$(decide 'tmux -L prueba kill-session -t bk-demo')" pass "kill-session en otro servidor: pasa"
# lo que no es para la baranda
check_eq "$(decide 'tmux list-sessions; cat ~/.tmux/kill-server')" pass "comandos de tmux que no matan nada: pasan"
check_eq "$(decide 'python3 lib/tmux_guard.py')" pass "tmux dentro de otra palabra: pasa"
check_eq "$(printf '{"tool_name": "Edit", "tool_input": {"command": "tmux kill-server"}}' | python3 "$ROOT/lib/tmux_guard.py")" "" \
  "otra herramienta (no Bash): nada"
out=$(printf 'no es json' | python3 "$ROOT/lib/tmux_guard.py" 2>&1); rc=$?
check_eq "$rc|$out" "0|" "una entrada rota: sale 0 sin decir nada"
out=$(printf '{"tool_name": "Bash", "tool_input": "tmux kill-server"}' | python3 "$ROOT/lib/tmux_guard.py" 2>&1); rc=$?
check_eq "$rc|$out" "0|" "tool_input que no es un objeto: sale 0 sin decir nada"
# entradas grandes: sin regex que se dispare (una pasada para los paréntesis)
big=$(python3 -c 'print("(" * 20000 + ")" * 20000 + " unset TMUX; " + "tmux ls; " * 300 + "kill " + "1 " * 5000 + "x")')
t0=$(date +%s)
decide "$big" >/dev/null
check "$([ $(( $(date +%s) - t0 )) -lt 5 ] && echo 0 || echo 1)" "un comando enorme y anidado se decide en menos de 5 s"

finish
