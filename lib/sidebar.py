#!/usr/bin/env python3
"""bk sidebar: tira izquierda del workspace con las sesiones activas, a lo Gentle Shell 4.

    1 ●     arriba: los workspaces (sesiones bk-*) por su número fijo, cada uno con su color; alt+1…9 salta
            a ese número. El número lo toma al crearse y no cambia aunque se cierren otros (bk _ids).
    i7 ○ ←3 abajo: los agentes bk (ventanas de la sesión "agents"), por el nombre de su ventana; ←3 = lo
            lanzó el workspace 3 (←i7: otro agente; ←?: esa ventana ya se cerró). bk lo anota (@bk_parent).
    ─       ⠋ trabajando · ! te espera o pide permiso · ? te pregunta · ⚠ ¿trabado? · ✓ listo · ○ libre ·
            · sin claude (glifo y color de cada estado: gentle.STATES, la misma tabla que el riel y el monitor)
    1 ●▪    ▪ a la derecha: la sesión ya carga mucho contexto (ámbar desde 200k, rojo desde 400k; cada
            llamada lo vuelve a leer y a pagar). El número exacto, en el tooltip.
    1 ○↪    ↪ a la derecha (en lugar del ▪): ese chat dejó el resumen para seguir en uno nuevo (lib/handoff.py);
            bk chat --desde N o «Seguir desde ws N» en alt+n lo toma.
    └⠋3.2 reviewer  debajo de un chat o agente: sus subagentes vivos (herramienta Agent), por <ventana>.<orden
            en que los lanzó> y su tipo; la descripción y el id, en el tooltip; clic = su sesión en un popup
    └○i7    y los agentes bk que lanzó, por su ventana; clic = ese agente
   ╭3 ○     a la izquierda: une los workspaces del mismo proyecto (BK_PROJECT): quedan juntos, donde está
   │5 ○     el número más bajo de cada proyecto; el número de cada uno no cambia.
   ╰6 ●
    1 ●     el actual va resaltado. Clic en una fila para ir ahí; con el mouse encima, su nombre
            (tipo N · proyecto · feature) aparece en la barra de tmux, como un tooltip.
"""
import curses, glob, json, os, re, shlex, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import agents as AG  # noqa: E402  (nombre de los chats: bk name, /rename o el título de Claude)
import gentle as G  # noqa: E402
import usage as U   # noqa: E402  (contexto de cada sesión: context_of, los umbrales y el formato)
import handoff as HO  # noqa: E402  (resúmenes listos para seguir en un chat nuevo)

BK = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin", "bk")

TIP_MS = 2500          # cuánto queda el nombre en la barra de tmux al pasar el mouse
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


KIND = {"general-purpose": "general"}   # el tipo de subagente, corto para la tira
_born = {}   # transcript de subagente → cuándo arrancó (no cambia: se lee una vez)


def started(j):
    """Cuándo arrancó un subagente: el timestamp de la 1.ª línea de su transcript (.jsonl), que no se reescribe.
    El meta.json no sirve: Claude Code lo reemplaza por uno nuevo cuando el subagente termina.
    Sin la 1.ª línea completa todavía (recién lanzado): "~", que ordena al final, y no se guarda."""
    if j not in _born:
        try:
            with open(j) as f:
                line = f.readline()
        except OSError:
            return "~"
        if not line.endswith("\n"):
            return "~"
        try:
            ts = json.loads(line).get("timestamp")
        except (ValueError, AttributeError):
            ts = None
        _born[j] = ts if isinstance(ts, str) and ts else "~"
    return _born[j]


def sub_name(owner, j):
    """Nombre de un subagente en la tira: <ventana>.<orden> <tipo> (3.2 reviewer = el 2.º que lanzó el ws 3).
    El orden cuenta también los que ya terminaron y sale de cuándo arrancó cada uno, así no cambia mientras vive."""
    d, me = os.path.dirname(j), j[:-len(".jsonl")] + ".meta.json"
    subs = sorted(glob.glob(os.path.join(glob.escape(d), "agent-*.jsonl")), key=lambda x: (started(x), x))
    try:
        with open(me) as f:
            kind = json.load(f).get("agentType") or "subagente"
    except (OSError, ValueError, AttributeError):
        kind = "subagente"
    kind = clean(kind) or "subagente"
    n = subs.index(j) + 1 if j in subs else "?"
    return f"{clean(owner)}.{n} {KIND.get(kind, kind)}"


def full_id(j):
    """El id entero de un subagente (agent-<id>.jsonl → <id>), para el tooltip."""
    b = os.path.basename(j or "")
    return b[len("agent-"):-len(".jsonl")] if b.startswith("agent-") and b.endswith(".jsonl") else b


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
    """[(número fijo, sesión)] ordenados por número, el mismo de alt+N. Si a alguno le falta o se repite,
    `bk _ids` le da uno (y le rehace el header); eso pasa una sola vez por workspace."""
    rows, seen, ok = [], set(), True
    for l in sh("tmux", "list-sessions", "-F", "#{session_name}\t#{@bk_id}").splitlines():
        name, _, i = l.partition("\t")
        if not name.startswith("bk"):
            continue
        if not i.isdigit() or i in seen:
            ok = False
        seen.add(i)
        rows.append((i, name))
    if not ok:
        try:   # rehacer headers puede tardar más que el timeout de sh()
            out = subprocess.run([BK, "_ids"], capture_output=True, text=True, timeout=20).stdout
        except Exception:
            out = ""
        rows = [tuple(l.split("\t", 1)) for l in out.splitlines() if "\t" in l]
    return sorted((int(i), n) for i, n in rows if i.isdigit())


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


def link_projects(blocks):
    """Junta los workspaces del mismo proyecto y marca la línea que los une, en la columna 0.
    blocks: [(fila del workspace + su proyecto, [filas colgadas: subagentes y agentes bk])], en el orden de su
    número. El proyecto es el BK_PROJECT del workspace (no la carpeta del chat, que cambia con cada cd). Cada
    proyecto queda donde está su número más bajo; adentro, por número. Devuelve las filas con un glifo más al
    final: ╭ el primero, │ los del medio y las filas colgadas de adentro, ╰ el último, "" solo o fuera del grupo."""
    order, groups = [], {}
    for b in blocks:
        proj = b[0][7]
        if proj not in groups:
            order.append(proj)
            groups[proj] = []
        groups[proj].append(b)
    out = []
    for proj in order:
        g = groups[proj]
        for i, (ws, subs) in enumerate(g):
            if len(g) == 1:
                mark, inner = "", ""
            else:
                mark = "╭" if i == 0 else "╰" if i == len(g) - 1 else "│"
                inner = "" if i == len(g) - 1 else "│"
            out.append(ws[:7] + (mark,))
            out += [sub + (inner,) for sub in subs]
    return out


READY = set()   # workspaces cuyo chat dejó un resumen listo (lo llena items())


def ready_ws(chats, sids):
    """De [(workspace, sid del chat)], los workspaces cuyo chat dejó un resumen listo."""
    return {ws for ws, sid in chats if sid and sid in sids}


def items():
    """Las filas de la tira: (tipo, texto, destino del clic, estado, (proyecto, detalle), número o ←padre,
    contexto, línea). Tipos: ws (workspace; en el 6.º lugar, su número fijo), ag (agente bk; en el 6.º lugar,
    quién lo lanzó), sub (subagente de la herramienta Agent) y kid (agente bk colgado de la ventana que lo
    lanzó: la misma ventana que su fila ag de abajo)."""
    st = states()
    wss = workspaces()
    # Los agentes bk primero: cada ventana muestra debajo los que lanzó (@bk_parent, lo anota bk al lanzar)
    agents = []
    for l in sh("tmux", "list-panes", "-s", "-t", AGENTS, "-F",
                "#{window_name}\t#{pane_id}\t#{pane_current_command}\t#{pane_dead}\t#{@bk_ws}\t#{pane_title}"
                "\t#{pane_current_path}\t#{@bk_parent}").splitlines():
        win, pid, cmd, dead, ws, title, path, parent = (l.split("\t") + [""] * 8)[:8]
        if ws:      # el chat de un workspace, de visita mientras un agente ocupa su lugar
            continue
        state = pane_state(st, pid, cmd, dead)
        ref = (st.get(pid) or {}).get("ref") or ""
        ctx = U.context_of((st.get(pid) or {}).get("_sid")) if state != "none" else None
        agents.append((win, pid, state, ctx, parent,
                       (f"{project_name(path)} · {win}", " ".join(x for x in (ref, feature(title, path)) if x))))
    ids = {name: wid for wid, name in wss}
    wins = {a[0] for a in agents}

    def by(key):
        """Los agentes bk que lanzó esa ventana (ws:<sesión> o ag:<ventana>), como filas colgadas."""
        return [("kid", w, f"{AGENTS}:{w}", stt, info, None, None) for w, _, stt, _, par, info in agents if par == key]

    def subs_of(sid, owner):
        return [("sub", sub_name(owner, j), "sub:" + j, "background" if bg else "working", ("", d), None, None)
                for d, bg, j in subagents(sid)]

    blocks, chat_sids = [], []
    for wid, name in wss:
        # Estado del chat del workspace (el pane que corre claude en la ventana main)
        state, sid, info, ctx = "none", None, None, None
        for l in sh("tmux", "list-panes", "-t", f"{name}:main", "-F",
                    "#{pane_id}\t#{pane_current_command}\t#{pane_dead}\t#{pane_start_command}\t#{pane_title}\t#{pane_current_path}").splitlines():
            pid, cmd, dead, start, title, path = (l.split("\t") + [""] * 6)[:6]
            if "bk rail" in start or "bk sidebar" in start:
                continue
            # el nombre del chat: bk name / --nombre, si no el de /rename o el título que le puso Claude
            nm = sh("tmux", "show-options", "-qv", "-t", name, "@bk_name").strip() \
                or AG.session_title((st.get(pid) or {}).get("_sid"))
            info = (project_name(path), nm or feature(title, path))
            s = pane_state(st, pid, cmd, dead)
            if s != "none":
                state = s
                sid = (st.get(pid) or {}).get("_sid")
                ctx = U.context_of(sid)
                chat_sids.append((name, sid))
        info = info or (name[3:], "")
        proj = sh("tmux", "show-environment", "-t", f"={name}", "BK_PROJECT").strip().partition("=")[2] or info[0]
        if proj != info[0]:   # el tooltip dice el proyecto del workspace, no el de la carpeta del chat (cd)
            info = (project_name(proj), info[1])
        blocks.append((("ws", name, name, state, info, wid, ctx, proj),
                       (subs_of(sid, str(wid)) if sid else []) + by("ws:" + name)))
    out = link_projects(blocks)
    READY.clear()
    READY.update(ready_ws(chat_sids, HO.ready_sids()))
    for win, pid, state, ctx, parent, info in agents:
        kind, _, who = parent.partition(":")
        src = (str(ids[who]) if kind == "ws" and who in ids else who if kind == "ag" and who in wins
               else "?" if parent else "")
        out.append(("ag", win, f"{AGENTS}:{win}", state, info, src, ctx, ""))
        if state != "none":
            out += [r + ("",) for r in subs_of((st.get(pid) or {}).get("_sid"), win)]
        out += [r + ("",) for r in by("ag:" + win)]
    return out


def row_label(r):
    """El texto de una fila de la tira, después de su glifo: el número del workspace, el nombre del subagente
    (sub_name) o la ventana del agente bk (en la lista de abajo, con ←quién lo lanzó). Sin caracteres de control."""
    kind, label = r[0], clean(r[1])
    if kind == "ws":
        return str(r[5])
    if kind == "ag" and r[5]:
        return f"{label} ←{clean(r[5])}"
    return label


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


SPIN, HALF = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", "◐◓◑◒"


def dot_for(state, tick, attrs):
    """(glifo, atributo curses) de la fila de la tira: los de gentle.STATES; trabajando y 2º plano giran."""
    g, _, color = G.state_ui(state)
    if state == "working":
        g = SPIN[tick % len(SPIN)]
    elif state == "background":
        g = HALF[(tick // 2) % len(HALF)]
    return g, attrs[color]


def ctx_mark(ctx, ready=False):
    """La marca a la derecha de una fila: ↪ si su chat dejó un resumen listo ("ready"), si no el ▪ de contexto
    alto ("warn" | "high"); None por debajo de 200k o sin dato. (glifo, clave del color)"""
    if ready:
        return ("↪", "ready")
    lvl = U.ctx_level(ctx)
    return ("▪", lvl) if lvl else None


def row_tip(kind, num, info, ctx=None, parent="", ready=False):
    """El tooltip de una fila de workspace o agente: tipo N · proyecto · feature · quién lo lanzó · contexto
    · el resumen listo para seguir."""
    proj, feat = info
    return (f"{'workspace' if kind == 'ws' else 'agente'} {num} · {proj}" + (f" · {feat}" if feat else "")
            + (f" · lanzado por {parent}" if parent else "")
            + (f" · {U.human_ctx(ctx)} de contexto" if ctx else "")
            + (f" · ↪ resumen listo: bk chat --desde {num} (o alt+n)" if ready else ""))


def branch_tip(row):
    """El tooltip de una fila colgada: subagente (nombre · descripción · su id entero) o agente bk que lanzó."""
    kind, label, target, _, info = row[:5]
    if kind == "sub":
        return f"↳ subagente {label} · {info[1]} · agent-{full_id(target[len('sub:'):])}"
    return f"↳ agente bk {label} · {info[0]}" + (f" · {info[1]}" if info[1] else "")


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
    A = G.curses_attrs(ids=True)   # los mismos colores que el riel, de día y de noche (+ ids y contexto)
    MUTED, ROSE, IRIS = (A[k] for k in ("muted", "rose", "iris"))
    CTX = {"warn": A["warn"] | curses.A_BOLD, "high": A["high"] | curses.A_BOLD, "ready": ROSE | curses.A_BOLD}
    n_ids = len(G.ID_NIGHT)

    def id_attr(n):
        """El color del número de un workspace: el mismo que en el header (gentle.id_colour)."""
        return A[f"id{(n - 1) % n_ids}"] | curses.A_BOLD if n else 0

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
        if W < 4 or H < 3:   # mientras tmux reacomoda (zoom de la terminal) la tira puede quedar sin lugar
            scr.refresh()
            scr.getch()
            continue
        cur = current()
        rowmap.clear()
        y, sep = 1, False
        tips.clear()
        if hover_y >= 0 and time.time() - hover_t > TIP_MS / 1000:   # se fue el mouse: sacar el resaltado
            hover_y = -1
        for row in cache:
            kind, label, target, state, info, wid, ctx, link = row
            if kind == "ag" and not sep:
                if y < H - 1:
                    scr.addnstr(y, 1, "─" * max(1, W - 2), W - 2, MUTED)
                y += 1
                sep = True
            if y >= H - 1:
                break
            hl = curses.A_REVERSE if y == hover_y else 0
            if link:   # la línea que une los workspaces del mismo proyecto
                scr.addnstr(y, 0, link, 1, MUTED)
            dot, col = dot_for(state, tick, A)
            if kind in ("sub", "kid"):   # rama debajo de quien lo lanzó; clic = su sesión (sub) o el agente (kid)
                scr.addnstr(y, 1, "└", 1, MUTED | hl)
                scr.addnstr(y, 2, dot, 1, col)
                if W > 4:
                    scr.addnstr(y, 3, row_label(row), W - 4, (MUTED if kind == "sub" else IRIS) | hl)
                tips[y] = branch_tip(row)
                rowmap[y] = target
                y += 1
                continue
            here = kind == "ws" and label == cur
            # el número del workspace con su color; el agente bk, por el nombre de su ventana (no un número)
            num = wid if kind == "ws" else clean(label)
            idc = id_attr(num) if kind == "ws" else IRIS
            numattr = (idc | curses.A_BOLD | curses.A_REVERSE) if here else idc
            name = f"{num}" if kind == "ws" else f"{num}"[:max(1, W - 8)]
            scr.addnstr(y, 1, name, max(1, W - 2), numattr | hl)
            x = 1 + max(1, len(name)) + (0 if kind == "ws" and len(name) > 1 else 1)
            if x < W - 1:
                scr.addnstr(y, x, dot, 1, col)
            ready = kind == "ws" and label in READY
            mark = ctx_mark(ctx, ready)
            if mark and x + 1 < W - 1:
                scr.addnstr(y, x + 1, mark[0], 1, CTX[mark[1]])
            if kind == "ag" and wid and x + 2 < W - 1:   # ←quién lo lanzó
                scr.addnstr(y, x + 2, f"←{clean(wid)}", W - 1 - (x + 2), MUTED)
            tips[y] = row_tip(kind, num, info, ctx, wid if kind == "ag" else "", ready)
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
