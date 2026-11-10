#!/usr/bin/env python3
"""Baranda opcional (hook PreToolUse de Claude Code, matcher Bash): que ningún agente tire tu tmux por error.

Un agente que corre dentro de tmux y limpia "su" tmux de prueba con `TMUX_TMPDIR=/tmp/x tmux kill-server` mata el
tuyo: con la variable TMUX definida, tmux usa ese servidor e ignora TMUX_TMPDIR. Se caen todos los agentes, los
workspaces y los chats. Lo mismo con `pkill tmux` o un `kill <pid>` del server (en ps se ve como el comando que lo
arrancó, p. ej. "tmux new-session -d -s agents …", y parece un cliente colgado).

Regla:
  - `tmux kill-server` solo pasa si esa llamada apunta a otro servidor: `-L <nombre>`, `-S <socket>`, o TMUX
    sacada para ese tmux: `env -u TMUX tmux …` o `TMUX= tmux …` (valen solo para el comando que les sigue: en
    `env -u TMUX true; tmux kill-server` el kill-server va a tu servidor), o un `unset TMUX` de antes en el mismo
    grupo (`unset TMUX; tmux …`, `(unset TMUX; tmux …)`). Si no: se frena.
  - `pkill`/`killall` de tmux, `kill <pid>` de un server de tmux vivo y `kill` de lo que da pgrep/pidof/grep de
    tmux (`kill $(pgrep tmux)`, `pgrep tmux | xargs kill`): se frenan.
  - `tmux kill-session` de tus sesiones (`agents`, `bk-…`) sin apuntar a otro servidor: te pregunta.

Es una baranda contra accidentes, no contra alguien que quiera esquivarla (`t=tmux; $t kill-server` pasa).
Nunca falla: ante una entrada que no entiende, no dice nada y Claude Code sigue como siempre.
No se instala solo: install.sh pregunta si la registra (o a mano: ver "Baranda de tmux" en el README).
"""
import bisect
import json
import re
import subprocess
import sys

# Dónde puede empezar un comando: al principio o después de un separador, un paréntesis, una comilla o un `
CMD_START = r"(?:^|(?<=[\s;&|('\"`{]))"
# `unset … TMUX` o `TMUX=` sola (también `export TMUX=`): TMUX sale del shell hasta que se cierra el grupo
UNSET = re.compile(CMD_START + r"(unset(?:\s+-[fv])?(?:\s+\w+){0,20}?\s+TMUX\b|TMUX=(?:''|\"\")?(?=[ \t]*(?:$|[;&|\n)])))")
# `env …` o asignaciones delante de un comando: valen solo para ese comando
PREFIX = re.compile(CMD_START + r"(env\b|[A-Za-z_]\w*=)")
ASSIGN = re.compile(r"[A-Za-z_]\w*=")
EMPTY_TMUX = re.compile(r"TMUX=(''|\"\")?")
# tmux como comando: suelto o con su ruta (/usr/bin/tmux), no dentro de otra palabra ni de una ruta (~/.tmux/x)
TMUX_CALL = re.compile(r"(?<![\w.-])tmux(?=[\s'\"`;&|)]|$)([^;&|\n]*)")
OWN_SESSION = re.compile(r"-t\s*['\"]?=?(agents|bk-[\w.-]+)")
# tmux acepta prefijos únicos de sus comandos: kill-ser, kill-serv… son kill-server
KILL_SERVER = re.compile(r"(^|\s)['\"]?kill-ser(v(e(r)?)?)?\b")
KILL_SESSION = re.compile(r"(^|\s)['\"]?kill-ses(s(i(o(n)?)?)?)?\b")
# kill [opciones] <pids>. La señal de -s no es un número ni empieza con -: así cada token se lee de una sola
# manera y no hay backtracking exponencial (`kill -s -s -s … x`). `kill -s 9 <pid>` igual frena: el 9 cuenta
# como un pid más.
KILL_PIDS = re.compile(r"(?<![\w-])kill(?:\s+(?:-s\s+[^\s\d-]\S*|-\S+))*(?P<pids>(?:\s+\d+)+)")
KILL_WORD = re.compile(r"(?<![\w-])kill\b")
PID_FINDER = re.compile(r"\b(pgrep|pidof|grep)\b")
TMUX_WORD = re.compile(r"\btmux\b")
ENV_ARG = ("-u", "--unset", "-C", "--chdir", "-P", "-S", "--split-string")   # opciones de env con argumento


def groups(cmd):
    """(close, inner): dónde se cierra cada grupo (por la posición de su apertura) y, para cada posición, el
    grupo más adentro que la contiene. Grupos: ( … ), $( … ), ` … `, '…' y "…" (lo de adentro de un sh -c "…"
    es otro shell). Una sola pasada, con una pila."""
    n = len(cmd)
    close, inner, stack, i = {}, [None] * (n + 1), [], 0
    while i < n:
        ch = cmd[i]
        inner[i] = stack[-1] if stack else None
        top = cmd[stack[-1]] if stack else ""
        if top == "'":                                  # adentro de '…' todo es literal
            if ch == "'":
                close[stack.pop()] = i
        elif ch == "\\" and i + 1 < n:                  # escapado: no abre ni cierra nada
            inner[i + 1] = inner[i]
            i += 2
            continue
        elif ch == top and ch in "`\"":
            close[stack.pop()] = i
        elif ch == ")" and top == "(":
            close[stack.pop()] = i
        elif ch == "(" and (top != '"' or (i and cmd[i - 1] == "$")):
            stack.append(i)
        elif ch == "`" or (ch in "'\"" and top != '"'):
            stack.append(i)
        i += 1
    inner[n] = stack[-1] if stack else None
    return close, inner


def command_end(cmd, i, close, inner):
    """Hasta dónde llega el comando simple que empieza en i: el primer ; & | o salto de línea de su mismo nivel,
    o el cierre del grupo que lo contiene. Un $( o ` en el medio lo corta: eso lo corre el shell de afuera."""
    g = inner[i]
    limit = close.get(g, len(cmd)) if g is not None else len(cmd)
    j = i
    while j < limit:
        ch = cmd[j]
        if ch == "'" and j in close:                    # '…' entero: literal
            j = close[j] + 1
            continue
        if ch == "`" or (ch == "$" and cmd[j + 1:j + 2] == "("):
            return j
        if ch in ";&|\n" and inner[j] == g:
            return j
        j += 1
    return limit


def prefix_isolates(cmd, s, e):
    """(¿el comando de cmd[s:e] corre sin TMUX?, dónde empieza la palabra que ya no es parte del prefijo).
    Sin TMUX: `TMUX= x`, `env -u TMUX x`, `env -i x`, `env TMUX= x`, con otras asignaciones u opciones en el medio
    (`env -u TMUX_PANE -u TMUX FOO=1 x`). Lee las palabras de a una y para en la primera que no es prefijo."""
    words = re.compile(r"\S+").finditer(cmd, s, e)
    out, w = False, next(words, None)
    while w and ASSIGN.match(w.group()):                # asignaciones de antes del comando
        out = out or bool(EMPTY_TMUX.fullmatch(w.group()))
        w = next(words, None)
    if not w or not re.fullmatch(r"(\S*/)?env", w.group()):
        return out, (w.start() if w else e)
    w = next(words, None)
    while w:
        a = w.group()
        if a in ("-i", "-", "--ignore-environment", "-uTMUX", "--unset=TMUX") or EMPTY_TMUX.fullmatch(a):
            out = True
        elif a in ENV_ARG:
            w = next(words, None)
            out = out or (a in ("-u", "--unset") and bool(w) and w.group() == "TMUX")
        elif not (a.startswith("-") or ASSIGN.match(a)):
            return out, w.start()                       # el comando que corre env
        w = next(words, None)
    return out, e


def isolations(cmd):
    """Dónde se saca TMUX: [(desde, hasta)], ordenados y sin solaparse. `env -u TMUX x` y `TMUX= x` valen solo
    para el comando x (y lo que corra adentro de él, como sh -c "…"); `unset TMUX` vale hasta que se cierra su
    grupo (un ( … ), unas comillas) o hasta el final, salvo en un pipeline o con & (ahí corre en un subshell y no
    queda). Lineal: cada comando se recorre una vez aunque tenga mil asignaciones o env encadenados."""
    if "TMUX" not in cmd and "env" not in cmd:   # env -i saca todo sin nombrar a TMUX
        return []
    close, inner = groups(cmd)
    out, last = [], (None, -1, -1)                 # (grupo, fin) del último comando medido; hasta dónde se leyó

    def end_of(s):
        nonlocal last
        if s < last[1] and inner[s] == last[0]:
            return last[1]
        e = command_end(cmd, s, close, inner)
        last = (inner[s], e, last[2])
        return e

    for m in PREFIX.finditer(cmd):
        s = m.start(1)
        if s < last[2] and inner[s] == last[0]:
            continue                               # ya leído como parte del prefijo de antes
        e = end_of(s)
        iso, upto = prefix_isolates(cmd, s, e)
        last = (last[0], last[1], upto)
        if iso:
            out.append((s, e))
    for m in UNSET.finditer(cmd):
        s = m.start(1)
        e = end_of(s)
        b = s - 1
        while b >= 0 and cmd[b] in " \t":
            b -= 1
        piped_in = b >= 0 and cmd[b] == "|" and not (b and cmd[b - 1] == "|")
        if (cmd[e:e + 1] in ("|", "&") and cmd[e:e + 2] not in ("||", "&&")) or piped_in:
            continue
        g = inner[s]
        out.append((s, close.get(g, len(cmd)) if g is not None else len(cmd) + 1))
    merged = []
    for s, u in sorted(out):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], u)
        else:
            merged.append([s, u])
    return merged


def isolated(iso, at):
    """¿La posición at cae adentro de alguna aislación (de isolations: ordenadas y sin solaparse)?"""
    k = bisect.bisect_left(iso, [at, -1]) - 1
    return k >= 0 and iso[k][0] < at < iso[k][1]


def pid_hunt(cmd):
    """¿Mata los PIDs que salen de buscar tmux? `kill $(pgrep tmux)`, `pgrep tmux | xargs kill`,
    `ps … | grep tmux | awk … | xargs kill`: en el mismo comando (sin ; ni & en el medio) hay un kill y un
    pgrep/pidof/grep con tmux después."""
    for chain in re.split(r"[;&\n]", cmd):
        if KILL_WORD.search(chain):
            m = PID_FINDER.search(chain)
            if m and TMUX_WORD.search(chain, m.end()):
                return True
    return False


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
        pids = {int(x) for x in m.group("pids").split()}
        if servers is None:   # ps y tmux una sola vez, y solo si hay un kill <pid>
            servers = tmux_server_pids()
        hit = pids & servers
        if hit:
            return ("deny", f"kill {' '.join(map(str, sorted(hit)))}: ese PID es el SERVER de tmux (aunque en ps se vea "
                            "como 'tmux new-session …': el server conserva el comando que lo arrancó). Matarlo cierra "
                            "todos los chats y agentes. Para cerrar una sesión usá bk stop o tmux kill-session -t <nombre>.")
    if re.search(r"\b(pkill|killall)\b[^;&|\n]*\btmux\b", cmd):
        return ("deny", "pkill/killall sobre tmux: mataría el server con todos los chats y agentes.")
    if pid_hunt(cmd):
        return ("deny", "kill de los PIDs que da pgrep/pidof/grep de tmux: entre ellos está el SERVER, y matarlo "
                        "cierra todos los chats y agentes. Para cerrar una sesión usá bk stop o tmux kill-session.")
    iso = isolations(cmd)
    for m in TMUX_CALL.finditer(cmd):
        args, at = m.group(1), m.start()
        # otro servidor: -L/-S en esta llamada, o TMUX sacada y todavía vigente (env -u TMUX / TMUX= delante de
        # este mismo comando, o un unset TMUX de antes cuyo grupo no se cerró)
        other_server = re.search(r"(^|\s)-[LS]\s*\S", args) or isolated(iso, at)
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
