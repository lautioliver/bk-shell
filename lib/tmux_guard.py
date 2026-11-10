#!/usr/bin/env python3
"""Baranda opcional (hook PreToolUse de Claude Code, matcher Bash): que ningún agente tire tu tmux por error.

Un agente que corre dentro de tmux y limpia "su" tmux de prueba con `TMUX_TMPDIR=/tmp/x tmux kill-server` mata el
tuyo: con la variable TMUX definida, tmux usa ese servidor e ignora TMUX_TMPDIR. Se caen todos los agentes, los
workspaces y los chats. Lo mismo con `pkill tmux` o un `kill <pid>` del server (en ps se ve como el comando que lo
arrancó, p. ej. "tmux new-session -d -s agents …", y parece un cliente colgado).

Regla:
  - `tmux kill-server` solo pasa si esa llamada apunta a otro servidor: `-L <nombre>`, `-S <socket>`, o TMUX
    sacada antes en el mismo comando (`env -u TMUX tmux …`, `unset TMUX; …`, `TMUX= tmux …`). Si no: se frena.
  - `pkill`/`killall` de tmux, y `kill <pid>` de un server de tmux vivo: se frenan.
  - `tmux kill-session` de tus sesiones (`agents`, `bk-…`) sin apuntar a otro servidor: te pregunta.

Es una baranda contra accidentes, no contra alguien que quiera esquivarla (`t=tmux; $t kill-server` pasa).
Nunca falla: ante una entrada que no entiende, no dice nada y Claude Code sigue como siempre.
No se instala solo: ver "Baranda de tmux" en el README.
"""
import json
import re
import subprocess
import sys

ISOLATED = re.compile(r"(^|[\s;&|('\"`])(env\s+(-\S+\s+)*-u\s+TMUX\b|unset\s+(\S+\s+)*TMUX\b|TMUX=(\s|''|\"\"))")
# tmux como comando: suelto o con su ruta (/usr/bin/tmux), no dentro de otra palabra ni de una ruta (~/.tmux/x)
TMUX_CALL = re.compile(r"(?<![\w.-])tmux(?=[\s'\"`;&|)]|$)([^;&|\n]*)")
OWN_SESSION = re.compile(r"-t\s*['\"]?=?(agents|bk-[\w.-]+)")
# tmux acepta prefijos únicos de sus comandos: kill-ser, kill-serv… son kill-server
KILL_SERVER = re.compile(r"(^|\s)['\"]?kill-ser(v(e(r)?)?)?\b")
KILL_SESSION = re.compile(r"(^|\s)['\"]?kill-ses(s(i(o(n)?)?)?)?\b")
KILL_PIDS = re.compile(r"(?<![\w-])kill((\s+-s\s+\S+)|(\s+-\S+))*((\s+\d+)+)")


def isolations(cmd):
    """Dónde se saca TMUX en el comando: [(posición, hasta dónde vale)]. Un `unset TMUX` dentro de un ( … ) vale
    hasta que ese grupo se cierra; afuera de todo grupo, hasta el final. Una sola pasada, con una pila."""
    starts = [m.start(2) for m in ISOLATED.finditer(cmd)]   # el token, sin el ( o ; de antes
    if not starts:
        return []
    inner, close, opens, k = {}, {}, [], 0
    for i, ch in enumerate(cmd):
        while k < len(starts) and starts[k] == i:   # el grupo más adentro que lo contiene, si hay
            inner[i] = opens[-1] if opens else None
            k += 1
        if ch == "(":
            opens.append(i)
        elif ch == ")" and opens:
            close[opens.pop()] = i
    end = len(cmd) + 1
    return [(s, end if inner[s] is None else close.get(inner[s], end)) for s in starts]


def sh(*a):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def tmux_server_pids():
    """PIDs de los servers de tmux vivos: los procesos tmux huérfanos (el server se separa de quien lo arrancó)
    y el del servidor al que apunta este entorno, que en Linux con systemd --user puede no colgar del pid 1."""
    pids = set()
    for line in sh("ps", "-A", "-o", "pid=,ppid=,comm=").splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[2].strip().endswith("tmux") and parts[1] == "1" and parts[0].isdigit():
            pids.add(int(parts[0]))
    own = sh("tmux", "display-message", "-p", "#{pid}").strip()
    if own.isdigit():
        pids.add(int(own))
    return pids


def decide(cmd):
    """None si pasa; ("deny"|"ask", motivo) si no."""
    servers = None
    for m in KILL_PIDS.finditer(cmd):
        pids = {int(x) for x in m.group(4).split()}
        if servers is None:   # ps y tmux una sola vez, y solo si hay un kill <pid>
            servers = tmux_server_pids()
        hit = pids & servers
        if hit:
            return ("deny", f"kill {' '.join(map(str, sorted(hit)))}: ese PID es el SERVER de tmux (aunque en ps se vea "
                            "como 'tmux new-session …': el server conserva el comando que lo arrancó). Matarlo cierra "
                            "todos los chats y agentes. Para cerrar una sesión usá bk stop o tmux kill-session -t <nombre>.")
    if re.search(r"\b(pkill|killall)\b[^;&|\n]*\btmux\b", cmd):
        return ("deny", "pkill/killall sobre tmux: mataría el server con todos los chats y agentes.")
    iso = isolations(cmd)
    for m in TMUX_CALL.finditer(cmd):
        args, at = m.group(1), m.start()
        # otro servidor: -L/-S en esta llamada, o TMUX sacada antes y todavía vigente (no en un ( … ) ya cerrado)
        other_server = re.search(r"(^|\s)-[LS]\s*\S", args) or any(s < at < until for s, until in iso)
        if other_server:
            continue
        if KILL_SERVER.search(args):
            return ("deny", "tmux kill-server sin apuntar a otro servidor: con la variable TMUX definida, tmux usa TU "
                            "servidor real (TMUX_TMPDIR no alcanza) y se caen todos los agentes y chats. Para un tmux de "
                            "prueba usá `env -u TMUX TMUX_TMPDIR=… tmux kill-server` o `tmux -L <nombre> kill-server`.")
        if KILL_SESSION.search(args) and OWN_SESSION.search(args):
            return ("ask", "tmux kill-session sobre una sesión tuya (agents o un workspace bk-…). ¿Seguro?")
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(data, dict) or data.get("tool_name") != "Bash" or not isinstance(data.get("tool_input"), dict):
        return
    cmd = str(data["tool_input"].get("command", ""))
    if "tmux" not in cmd and "kill" not in cmd:
        return
    d = decide(cmd)
    if d:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": d[0],
                                                 "permissionDecisionReason": "baranda tmux: " + d[1]}},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
