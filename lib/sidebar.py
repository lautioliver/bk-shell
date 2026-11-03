#!/usr/bin/env python3
"""bk sidebar: tira izquierda del workspace con las sesiones activas, a lo Gentle Shell 4.

    1 ●     arriba: los workspaces (sesiones bk-*), en orden de creación; alt+1…9 salta a cada uno
    2 ○     abajo: los agentes (ventanas de la sesión "agents")
    ─       ● trabajando · ● rojo te espera · ○ libre/listo · · sin claude
    └⠋      debajo de un chat o agente: sus subagentes vivos (herramienta Agent); clic = su sesión en un popup
    1 ●     el actual va resaltado. Clic en una fila para ir ahí; con el mouse encima, su nombre
            (tipo N · proyecto · feature) aparece en la barra de tmux, como un tooltip.
"""
import curses, glob, json, os, re, shlex, subprocess, sys, time

TIP_MS = 2500           # cuánto queda el nombre en la barra de tmux al pasar el mouse
SUB_TTL = 600           # un subagente sin escribir su transcript en 10 min ya no cuenta como vivo

STATE = os.environ.get("BK_STATE_DIR", os.path.expanduser("~/.cache/bk/agents"))
AGENTS = os.environ.get("BK_SESSION", "agents")


def sh(*a):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""


def states():
    out = {}
    for f in glob.glob(os.path.join(STATE, "*.json")):
        try:
            s = json.load(open(f))
        except Exception:
            continue
        s["_sid"] = os.path.basename(f)[:-5]   # el hook nombra el archivo <session_id>.json
        p = s.get("pane")
        if p and (p not in out or s.get("ts", 0) > out[p].get("ts", 0)):
            out[p] = s
    return out


PROJECTS = os.path.expanduser("~/.claude/projects")
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def clean(text):
    """Sin caracteres de control: lo que escribe un modelo no debe mover el cursor ni cambiar colores."""
    return _CTRL.sub(" ", str(text or "")).strip()


def live_meta(meta_path, now=None):
    """El meta.json de un subagente si está vivo, si no None. Vivo = el meta no dice finished y su
    transcript (agent-<id>.jsonl, al lado) se escribió hace menos de SUB_TTL (uno que murió sin avisar
    no queda para siempre)."""
    now = time.time() if now is None else now
    j = meta_path[:-len(".meta.json")] + ".jsonl"
    try:
        if now - os.path.getmtime(j) > SUB_TTL:
            return None
        with open(meta_path) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("finished"):
        return None
    return meta


def live_subagents_in(d, now=None):
    """Subagentes vivos de una carpeta subagents/: [(descripción, en segundo plano?, transcript)],
    el más reciente primero."""
    out = []
    for m in glob.glob(os.path.join(glob.escape(d), "agent-*.meta.json")):
        meta = live_meta(m, now)
        if meta is None:
            continue
        j = m[:-len(".meta.json")] + ".jsonl"
        desc = clean(meta.get("description") or meta.get("agentType") or "subagente")
        try:
            mt = os.path.getmtime(j)
        except OSError:
            continue
        out.append((desc, meta.get("requestShape") == "background", mt, j))
    return [(d_, bg, j) for d_, bg, _, j in sorted(out, key=lambda x: -x[2])]


_subdirs, _subcache = {}, {}


def subagents(sid):
    """Subagentes vivos de una sesión de Claude Code (~/.claude/projects/*/<sid>/subagents/).
    Se recalcula como mucho una vez por segundo por sesión: la tira y el riel redibujan seguido."""
    if not sid or sid.startswith("bk-") or "/" in sid:   # bk-<ventana>.json: registro previo, sin sesión
        return []
    hit = _subcache.get(sid)
    if hit and time.time() - hit[0] < 1:
        return hit[1]
    d = _subdirs.get(sid)
    if not d or not os.path.isdir(d):
        found = glob.glob(os.path.join(glob.escape(PROJECTS), "*", glob.escape(sid), "subagents"))
        d = _subdirs[sid] = found[0] if found else ""
    out = live_subagents_in(d) if d else []
    _subcache[sid] = (time.time(), out)
    return out


_names, _branches = {}, {}


def feature(title, path):
    """En qué trabaja: el título de la sesión de Claude (sin el glifo de estado); si no hay, la rama."""
    t = clean(title)
    while t and not t[0].isalnum() and t[0] not in "#/":
        t = t[1:].lstrip()
    if t and t not in ("chat", "rail") and not t.endswith(".local"):
        return t
    if not path:
        return ""
    hit = _branches.get(path)
    if not hit or time.time() - hit[0] > 10:      # git una vez cada 10 s por carpeta, no en cada cuadro
        hit = _branches[path] = (time.time(), sh("git", "-C", path, "branch", "--show-current").strip())
    return hit[1]


def project_name(path):
    """Nombre del proyecto: el repo principal aunque la carpeta sea un worktree."""
    if path not in _names:
        top = sh("git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir").strip() if path else ""
        _names[path] = os.path.basename(top[:-5]) if top.endswith("/.git") else (os.path.basename(path or "") or "?")
    return _names[path]


def tip(text):
    """Texto para tmux display-message: '#' doble, así un título no se expande como formato (ni #(…))."""
    return clean(text).replace("#", "##")


def workspaces():
    rows = []
    for l in sh("tmux", "list-sessions", "-F", "#{session_created}\t#{session_name}").splitlines():
        t, _, name = l.partition("\t")
        if name.startswith("bk"):
            rows.append((int(t or 0), name))
    return [n for _, n in sorted(rows)]


def pane_state(st, pane, cmd, dead):
    if dead == "1" or cmd in ("zsh", "bash", "sh", "fish", "-zsh", "login", ""):
        return "none"
    d = st.get(pane) or {}
    state = d.get("state", "idle")
    # "working" sin ningún evento del hook en 5 min: probablemente trabado
    if state == "working" and d.get("tool") != "confianza" and time.time() - d.get("ts", time.time()) > 300:
        return "stale"
    if state == "waiting":
        return "question" if (d.get("wait") or ("question" if d.get("tool") == "AskUserQuestion" else "")) == "question" else "permission"
    return state


def items():
    st = states()
    out = []
    for name in workspaces():
        # Estado del chat del workspace (el pane que corre claude en la ventana main)
        state, subs, info = "none", [], None
        for l in sh("tmux", "list-panes", "-t", f"{name}:main", "-F",
                    "#{pane_id}\t#{pane_current_command}\t#{pane_dead}\t#{pane_start_command}\t#{pane_title}\t#{pane_current_path}").splitlines():
            pid, cmd, dead, start, title, path = (l.split("\t") + [""] * 6)[:6]
            if "bk rail" in start or "bk sidebar" in start:
                continue
            info = (project_name(path), feature(title, path))
            s = pane_state(st, pid, cmd, dead)
            if s != "none":
                state = s
                subs = subagents((st.get(pid) or {}).get("_sid"))
        out.append(("ws", name, name, state, info or (name[3:], "")))
        out += [("sub", d, "sub:" + j, "background" if bg else "working", ("", d)) for d, bg, j in subs]
    for l in sh("tmux", "list-panes", "-s", "-t", AGENTS, "-F",
                "#{window_name}\t#{pane_id}\t#{pane_current_command}\t#{pane_dead}\t#{@bk_ws}\t#{pane_title}\t#{pane_current_path}").splitlines():
        win, pid, cmd, dead, ws, title, path = (l.split("\t") + [""] * 7)[:7]
        if ws:      # el chat de un workspace, de visita mientras un agente ocupa su lugar
            continue
        state = pane_state(st, pid, cmd, dead)
        ref = (st.get(pid) or {}).get("ref") or ""
        out.append(("ag", win, f"{AGENTS}:{win}", state,
                    (f"{project_name(path)} · {win}", " ".join(x for x in (ref, feature(title, path)) if x))))
        if state != "none":
            out += [("sub", d, "sub:" + j, "background" if bg else "working", ("", d))
                    for d, bg, j in subagents((st.get(pid) or {}).get("_sid"))]
    return out


def current():
    pane = os.environ.get("TMUX_PANE", "")
    return sh("tmux", "display-message", "-p", "-t", pane, "#{session_name}").strip()


def go(target):
    # El cliente que mira este pane es el que cambia (no otro cliente conectado a otra ventana)
    pane = os.environ.get("TMUX_PANE", "")
    client = sh("tmux", "display-message", "-p", "-t", pane, "#{client_name}").strip()
    c = ["-c", client] if client else []
    if target == "__new__":
        sess = sh("tmux", "display-message", "-p", "-t", pane, "#{session_name}").strip()
        subprocess.Popen(["tmux", "display-popup"] + c + ["-w", "70%", "-h", "60%", "-E",
                          f"BK_CLIENT='{client}' BK_FROM='{sess}' bk new"])
        return
    if target.startswith("sub:"):
        # Subagente: su sesión en vivo, de solo lectura (bk sub). q/esc cierra.
        subprocess.Popen(["tmux", "display-popup"] + c + ["-w", "92%", "-h", "88%", "-T", " ↳ subagente ", "-E",
                          "bk sub " + shlex.quote(target[4:])])
        return
    if target.startswith(AGENTS + ":"):
        # El agente se ve encima, en un popup de solo lectura (bk watch): q/esc lo cierra, enter va a
        # su ventana para escribirle. Antes era un tmux dentro del popup y las teclas no le llegaban.
        win = target.split(":", 1)[1]
        subprocess.Popen(["tmux", "display-popup"] + c + ["-w", "92%", "-h", "88%", "-E",
                          f"BK_WATCH_KEYS=1 BK_WATCH_CLIENT='{client}' bk watch '{win}'"])
        return
    subprocess.run(["tmux", "switch-client"] + c + ["-t", target], capture_output=True)


def ui(scr):
    curses.curs_set(0)
    curses.use_default_colors()
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    # Movimiento del mouse sin botones (modo 1003): ncurses no siempre lo pide solo. Sin SGR (1006):
    # el terminfo de tmux espera el formato X10 (kmous=\E[M) y curses leería mal el otro.
    sys.stdout.write("\033[?1003h"); sys.stdout.flush()
    me = os.environ.get("TMUX_PANE", "")
    client = sh("tmux", "display-message", "-p", "-t", me, "#{client_name}").strip() if me else ""
    hover_y, hover_t, tips = -1, 0.0, {}
    try:   # de día, tonos oscuros y apagados (los de noche son pastel y sobre fondo claro gritan)
        from gentle import is_day
        day = is_day()
    except Exception:
        day = False
    colors = {1: 137, 2: 131, 3: 243, 4: 132, 5: 60} if day else {1: 222, 2: 210, 3: 244, 4: 218, 5: 147}
    for k, v in colors.items():
        curses.init_pair(k, v, -1)
    WORK, WAIT, MUTED, ROSE, IRIS = (curses.color_pair(i) for i in range(1, 6))

    def tooltip(text):
        """El nombre de la fila, como tooltip en la barra de tmux (la tira no cambia de ancho)."""
        if text:
            sh("tmux", "display-message", *(["-c", client] if client else []), "-d", str(TIP_MS), tip(text))
    scr.timeout(250)          # 4 cuadros por segundo para el spinner; los datos, cada 1 s
    rowmap = {}
    tick, cache, cache_t = 0, [], 0
    while True:
        tick += 1
        if time.time() - cache_t > 1:
            cache, cache_t = items(), time.time()
        H, W = scr.getmaxyx()
        scr.erase()
        cur = current()
        rowmap.clear()
        y, n_ws, n_ag, sep = 1, 0, 0, False
        tips.clear()
        if hover_y >= 0 and time.time() - hover_t > TIP_MS / 1000:   # se fue el mouse: sacar el resaltado
            hover_y = -1
        for kind, label, target, state, info in cache:
            if kind == "ag" and not sep:
                if y < H - 1:
                    scr.addnstr(y, 1, "─" * max(1, W - 2), W - 2, MUTED)
                y += 1
                sep = True
            if y >= H - 1:
                break
            hl = curses.A_REVERSE if y == hover_y else 0
            if kind == "sub":   # subagente: rama debajo de su chat, sin número; clic = su sesión
                scr.addnstr(y, 1, "└", 1, MUTED | hl)
                dot = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[tick % 10] if state == "working" else "◐◓◑◒"[(tick // 2) % 4]
                scr.addnstr(y, 3, dot, 1, WORK)
                tips[y] = f"↳ subagente · {info[1]}"
                rowmap[y] = target
                y += 1
                continue
            if kind == "ws":
                n_ws += 1; num = n_ws
            else:
                n_ag += 1; num = n_ag
            if state == "working":
                dot, col = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[tick % 10], WORK
            elif state == "background":
                dot, col = "◐◓◑◒"[(tick // 2) % 4], WORK
            elif state == "stale":
                dot, col = "~", WAIT
            elif state == "question":
                dot, col = "?", WAIT
            elif state == "permission":
                dot, col = "!", WAIT
            elif state == "waiting":
                dot, col = "●", WAIT
            elif state == "none":
                dot, col = "·", MUTED
            else:
                dot, col = "○", MUTED
            here = kind == "ws" and label == cur
            numattr = (ROSE | curses.A_BOLD | curses.A_REVERSE) if here else (IRIS if kind == "ag" else 0)
            scr.addnstr(y, 1, f"{num:>1}", 2, numattr | hl)
            scr.addnstr(y, 3, dot, 1, col)
            proj, feat = info
            tips[y] = f"{'workspace' if kind == 'ws' else 'agente'} {num} · {proj}" + (f" · {feat}" if feat else "")
            rowmap[y] = target
            y += 1
        # "+" al pie: Nuevo (chat o workspace), igual que alt+n
        if H - 2 > y:
            scr.addnstr(H - 2, 1, " + ", W - 1, ROSE | curses.A_BOLD)
            rowmap[H - 2] = "__new__"
        scr.refresh()
        k = scr.getch()
        if k == curses.KEY_MOUSE:
            try:
                _, mx, my, _, b = curses.getmouse()
            except curses.error:
                continue
            click = (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED | curses.BUTTON1_RELEASED
                     | curses.BUTTON1_DOUBLE_CLICKED)
            # Movimiento: el curses de macOS no lo marca con REPORT_MOUSE_POSITION sino con otros bits;
            # todo evento que no sea un clic izquierdo cuenta como "el mouse está acá"
            if not b & click:
                if my != hover_y:          # entró a otra fila: mostrar su nombre una vez
                    tooltip(tips.get(my, ""))
                hover_y, hover_t = my, time.time()
                continue
            if my in rowmap:
                go(rowmap[my])


if __name__ == "__main__":
    # Nunca cerrarse: si algo falla (un clic raro, tmux ocupado), anotar el error y volver a dibujar.
    # Si el proceso termina, tmux cierra el pane y la tira desaparece del workspace.
    import traceback
    log = os.path.expanduser("~/.cache/bk/sidebar.log")
    while True:
        try:
            curses.wrapper(ui)
        except KeyboardInterrupt:
            sys.stdout.write("\033[?1003l"); sys.stdout.flush()   # devolver el mouse como estaba
            break
        except Exception:
            os.makedirs(os.path.dirname(log), exist_ok=True)
            with open(log, "a") as f:
                f.write(time.strftime("%F %T ") + traceback.format_exc() + "\n")
            time.sleep(1)
