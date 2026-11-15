#!/usr/bin/env python3
"""bk s: selector de sesiones de Claude Code, estilo Orca.

Arriba, las sesiones abiertas (con estado de la barra): Enter salta a su pane de tmux.
Abajo, las recientes de todas las carpetas (~/.claude/projects): Enter la retoma con
`claude --resume` en una ventana nueva de la sesión tmux "agents", en su carpeta.

Cada chat de un workspace lleva su número fijo (el de la tira y alt+N), con su color: "ws 3".
Con ↪ en lugar del ●: ese chat dejó el resumen para seguir en uno nuevo (lib/handoff.py). También en recientes,
si ya se cerró: bk chat --desde o «Seguir desde» en alt+n lo toma.

Teclas: ↑↓/jk moverse · / buscar · enter abrir · x cerrar (un agente: su ventana; un workspace: todo el
workspace, con confirmación; la conversación queda en recientes) · q/esc salir
"""
import curses, glob, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import gentle as G  # noqa: E402  (paleta del tema, una sola para todas las vistas)
import handoff as HO  # noqa: E402  (resúmenes listos para seguir en un chat nuevo)

PROJECTS = os.path.expanduser("~/.claude/projects")
STATE = os.environ.get("BK_STATE_DIR", os.path.expanduser("~/.cache/bk/agents"))
CACHE = os.path.expanduser("~/.cache/barrilete/sessions.json")
SESSION = os.environ.get("BK_SESSION", "agents")
RECENT = 80
SKIP_DIRS = re.compile(r"-private-tmp-|scratch-workspaces|scratchpad")


def sh(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""


def tmux_panes():
    """pane_id -> dict(target, window, session, dead, wsid). wsid: el número fijo del workspace (@bk_id)."""
    out = {}
    for l in sh("tmux", "list-panes", "-a", "-F",
                "#{pane_id}\t#{session_name}\t#{window_index}\t#{window_name}\t#{pane_dead}\t#{@bk_id}").splitlines():
        p = l.split("\t")
        if len(p) == 6:
            out[p[0]] = {"session": p[1], "target": f"{p[1]}:{p[2]}", "window": p[3], "dead": p[4] == "1",
                         "wsid": p[5] if p[1].startswith("bk") and p[5].isdigit() else ""}
    return out


def transcript_meta(path, cache):
    """cwd, título y primer mensaje de un transcript (cacheado por mtime)."""
    st = os.stat(path)
    key = path
    c = cache.get(key)
    if c and c.get("mtime") == st.st_mtime:
        return c
    cwd = title = first = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not cwd and '"cwd"' in line:
                    m = re.search(r'"cwd":"((?:[^"\\]|\\.)*)"', line)
                    if m:
                        cwd = m.group(1).replace("\\\\", "\\")
                if '"custom-title"' in line or '"ai-title"' in line:
                    try:
                        o = json.loads(line)
                        t = o.get("customTitle") or o.get("aiTitle")
                        if t and (o.get("type") == "custom-title" or not title.startswith("!")):
                            title = ("!" + t) if o.get("type") == "custom-title" else t
                    except Exception:
                        pass
                elif not first and '"type":"user"' in line and '"isMeta":true' not in line:
                    try:
                        o = json.loads(line)
                        c_ = o.get("message", {}).get("content")
                        if isinstance(c_, list):
                            c_ = next((x.get("text") for x in c_ if isinstance(x, dict) and x.get("type") == "text"), "")
                        if isinstance(c_, str) and c_.strip() and not c_.startswith("<"):
                            first = " ".join(c_.split())[:120]
                    except Exception:
                        pass
    except OSError:
        pass
    c = {"mtime": st.st_mtime, "cwd": cwd, "title": title.lstrip("!"), "first": first}
    cache[key] = c
    return c


def load():
    panes = tmux_panes()
    live = {}  # session_id -> estado
    for f in glob.glob(os.path.join(STATE, "*.json")):
        sid = os.path.basename(f)[:-5]
        if sid.startswith("bk-"):
            continue
        try:
            s = json.load(open(f))
        except Exception:
            continue
        # Corría en un pane de tmux que ya no existe: la ventana se cerró sin que Claude mandara
        # Stop/SessionEnd y el estado quedó congelado ("trabajando" para siempre). Está muerta:
        # va a recientes (enter la retoma) y se borra el estado para que la tira no la cuente.
        # Solo si tmux respondió: sin servidor, `panes` vacío no prueba nada.
        if panes and s.get("pane") and s["pane"] not in panes:
            try:
                os.remove(f)
            except OSError:
                pass
            continue
        live[sid] = s
    try:
        cache = json.load(open(CACHE))
    except Exception:
        cache = {}
    files = []
    for d in os.listdir(PROJECTS) if os.path.isdir(PROJECTS) else []:
        if SKIP_DIRS.search(d):
            continue
        for p in glob.glob(os.path.join(PROJECTS, d, "*.jsonl")):
            try:
                files.append((os.path.getmtime(p), p))
            except OSError:
                pass
    files.sort(reverse=True)
    rows_open, rows_recent = [], []
    seen = set()
    for mtime, p in files[:RECENT]:
        sid = os.path.basename(p)[:-6]
        meta = transcript_meta(p, cache)
        cwd = meta["cwd"] or ""
        proj = project_name(cwd) if cwd else os.path.basename(os.path.dirname(p))
        row = {"sid": sid, "cwd": cwd, "proj": proj, "title": meta["title"] or meta["first"] or "(sin mensajes)",
               "mtime": mtime}
        if sid in live:
            s = live[sid]
            pane = panes.get(s.get("pane") or "")
            row.update(state=s.get("state"), role=s.get("role"), ref=s.get("ref"), pane=s.get("pane"),
                       target=pane["target"] if pane else None, window=where(pane),
                       dead=pane["dead"] if pane else False, session=pane["session"] if pane else None,
                       wsid=pane["wsid"] if pane else "")
            rows_open.append(row)
        else:
            rows_recent.append(row)
        seen.add(sid)
    # Abiertas sin transcript todavía (recién lanzadas): igual se muestran
    for sid, s in live.items():
        if sid not in seen:
            pane = panes.get(s.get("pane") or "")
            rows_open.append({"sid": sid, "cwd": s.get("cwd", ""), "proj": project_name(s.get("cwd", "")),
                              "title": "(recién abierta)", "mtime": s.get("ts", 0), "state": s.get("state"),
                              "role": s.get("role"), "ref": s.get("ref"), "pane": s.get("pane"),
                              "target": pane["target"] if pane else None,
                              "window": where(pane), "dead": False,
                              "session": pane["session"] if pane else None, "wsid": pane["wsid"] if pane else ""})
    # Ventanas de agentes muertas (el agente salió y la ventana quedó): para poder cerrarlas
    for pid, pane in panes.items():
        if pane["session"] == SESSION and pane["dead"] and not any(r.get("pane") == pid for r in rows_open):
            # Claude deja impreso "claude --resume <id>" al salir: con eso se puede revivir
            m = re.search(r"claude --resume ([0-9a-f-]{36})", sh("tmux", "capture-pane", "-p", "-t", pid))
            rows_open.append({"sid": m.group(1) if m else None, "cwd": "", "proj": "",
                              "title": "terminó · enter lo retoma acá" if m else "terminó (ventana abierta)",
                              "mtime": 0, "state": "ended", "target": pane["target"], "window": pane["window"],
                              "pane": pid, "dead": True, "session": pane["session"], "wsid": ""})
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(cache, open(CACHE, "w"))
    except OSError:
        pass
    rows_open.sort(key=lambda r: -r["mtime"])
    sids = HO.ready_sids()
    return mark_handoffs(rows_open, sids), mark_handoffs(rows_recent, sids)


def mark_handoffs(rows, sids):
    """Marca (handoff=True) las filas cuya sesión dejó un resumen listo."""
    for r in rows:
        r["handoff"] = bool(r.get("sid")) and r["sid"] in sids
    return rows


def where(pane):
    """Nombre corto de dónde vive: la ventana si es un agente de bk; "ws 3" si es el chat de un workspace (su
    número fijo, el de la tira); si no, la sesión tmux."""
    if not pane:
        return None
    if pane["session"] == SESSION:
        return pane["window"]
    if pane.get("wsid"):
        return f"ws {pane['wsid']}"
    return f"{pane['session']}:{pane['window']}"


def close_action(it, current):
    """Qué hace x sobre una fila: ("window", destino) un agente · ("session", sesión) un workspace entero ·
    ("error", motivo). El workspace desde el que abriste el selector no: te dejaría sin sesión."""
    sess = it.get("session")
    if it.get("target") and it.get("pane") and sess == SESSION:
        return "window", it["target"]
    if sess and sess.startswith("bk"):
        if sess == current:
            return "error", "es el workspace donde estás: cerralo desde otro"
        return "session", sess
    return "error", "solo cierro agentes (sesión tmux 'agents') y workspaces (bk-…)"


def project_name(cwd):
    parts = cwd.rstrip("/").split("/")
    if len(parts) > 1 and parts[-2].endswith("-wt"):
        return parts[-2][:-3] + "#" + parts[-1]
    if "/.claude/worktrees/" in cwd:
        return cwd.split("/.claude/worktrees/")[0].rsplit("/", 1)[-1] + "~wt"
    return parts[-1] or "~"


def ago(ts):
    if not ts:
        return ""
    d = time.time() - ts
    if d < 3600:
        return f"hace {int(d // 60)}m"
    if d < 86400:
        return time.strftime("hoy %H:%M", time.localtime(ts)) if time.localtime(ts).tm_yday == time.localtime().tm_yday \
            else time.strftime("ayer %H:%M", time.localtime(ts))
    if d < 7 * 86400:
        return time.strftime("%a %H:%M", time.localtime(ts))
    return time.strftime("%d/%m", time.localtime(ts))


STATE_TXT = {k: G.STATES[k][1] for k in ("background", "working", "waiting", "done", "idle", "ended")}


def current_workspace():
    """Workspace bk desde el que se abrió el selector (alt+s / prefijo+s), o None."""
    if not os.environ.get("TMUX"):
        return None
    s = os.environ.get("BK_FROM") or sh("tmux", "display-message", "-p", "#{session_name}").strip()
    return s if s.startswith("bk") else None


def open_row(r):
    """Devuelve (mensaje_error | None, pasos a ejecutar después de cerrar curses).

    Un paso es un comando (lista) o una función sin argumentos. Dentro de un workspace, el agente
    se trae al lugar del chat (como alt+shift+N; alt+q vuelve), en vez de saltar a la sesión "agents".
    """
    in_tmux = bool(os.environ.get("TMUX"))
    ws = current_workspace()

    def show(pane, target):
        # Agentes de la sesión "agents": al lugar del chat del workspace. Los que ya viven en un
        # workspace (su chat, o uno de visita): saltar ahí.
        if ws and target.split(":")[0] == SESSION:
            return [["bk", "_agent", ws, pane]]
        if in_tmux:
            return [["tmux", "switch-client", "-t", pane], ["tmux", "select-window", "-t", pane],
                    ["tmux", "select-pane", "-t", pane]]
        return [["tmux", "attach", "-t", pane]]

    if r.get("dead"):
        # Pane muerto (remain-on-exit): no acepta teclas. Revivirlo con la misma sesión de Claude.
        if not r.get("sid"):
            return "esa ventana terminó y no sé qué sesión era: cerrala con x", None
        return None, [["tmux", "respawn-pane", "-k", "-t", r["pane"], f"claude --resume {r['sid']}"]] \
            + show(r["pane"], r["target"])
    if r.get("target"):
        return None, show(r["pane"], r["target"])
    if r.get("state"):  # abierta pero fuera de tmux (otra ventana de Ghostty): no duplicarla
        return "esa sesión está abierta en otra terminal: retomala ahí", None
    cwd = r["cwd"] if r["cwd"] and os.path.isdir(r["cwd"]) else os.path.expanduser("~")
    name = "r-" + re.sub(r"[^A-Za-z0-9#-]", "", r["proj"])[:12]

    def resume():
        has = subprocess.run(["tmux", "has-session", "-t", SESSION], capture_output=True).returncode == 0
        new = (["tmux", "new-window", "-d", "-t", SESSION + ":", "-n", name] if has
               else ["tmux", "new-session", "-d", "-s", SESSION, "-n", name])
        pane = sh(*new, "-P", "-F", "#{pane_id}", "-c", cwd, f"claude --resume {r['sid']}").strip()
        if not pane:
            return
        subprocess.run(["tmux", "set-option", "-w", "-t", pane, "remain-on-exit", "on"], capture_output=True)
        for cmd in show(pane, f"{SESSION}:{name}"):
            if cmd[:2] == ["tmux", "attach"]:
                os.execvp("tmux", ["tmux", "attach", "-t", pane])
            subprocess.run(cmd)

    return None, [resume]


def ui(scr):
    curses.curs_set(0)
    curses.use_default_colors()
    A = G.curses_attrs(ids=True)   # con ids: el color de cada número de workspace, el de la tira y el header
    MAUVE, GREEN, YELLOW, RED, BLUE, GRAY = (A[k] for k in ("rose", "green", "amber", "red", "iris", "muted"))
    n_ids = len(G.ID_NIGHT)

    def id_attr(n):
        return A[f"id{(int(n) - 1) % n_ids}"] | curses.A_BOLD if n else BLUE
    confirm = None   # ("session", nombre, número, estado): esperando y/n para cerrar un workspace
    st_color = {k: A[G.STATES[k][2]] for k in STATE_TXT}   # el color de cada estado, el mismo que en la tira
    opened, recent = load()
    query, typing, sel, top, msg = "", False, 0, 0, ""

    while True:
        q = query.lower()
        match = lambda r: not q or q in f"{r['proj']} {r['title']} {r.get('window') or ''}".lower()
        items = [("h", "abiertas")] + [("r", r) for r in opened if match(r)] + \
                [("h", "recientes")] + [("r", r) for r in recent if match(r)]
        selectable = [i for i, it in enumerate(items) if it[0] == "r"]
        sel = max(0, min(sel, len(selectable) - 1))
        H, W = scr.getmaxyx()
        body = H - 3
        cur = selectable[sel] if selectable else -1
        if cur < top:
            top = cur
        if cur >= top + body:
            top = cur - body + 1
        scr.erase()
        scr.addnstr(0, 0, " sesiones de Claude ", W - 1, MAUVE | curses.A_BOLD)
        if query or typing:
            scr.addnstr(0, 21, f"/ {query}" + ("▏" if typing else ""), W - 22, YELLOW)
        for row, i in enumerate(range(top, min(len(items), top + body)), 1):
            kind, it = items[i]
            if kind == "h":
                scr.addnstr(row, 0, f"─ {it} " + "─" * W, W - 1, MAUVE)
                continue
            attr = curses.A_REVERSE if i == cur else 0
            if it.get("state"):
                mark = "↪" if it.get("handoff") else "●"
                where = it.get("window") or "afuera"
                st = STATE_TXT.get(it["state"], it["state"] or "")
                head = f" {mark} {where:<10} {it['proj'][:18]:<18} "
                tail = f" {st:<10} {ago(it['mtime']):>12} "
                mid = f"{(it.get('role') or '')[:11]:<11} {it['title']}"
                scr.addnstr(row, 0, " " * (W - 1), W - 1, attr)
                scr.addnstr(row, 0, head, W - 1, attr | (BLUE if it.get("target") else GRAY))
                scr.addnstr(row, len(head), mid[: max(0, W - 1 - len(head) - len(tail))], W - 1 - len(head), attr)
                if W - 1 - len(tail) > len(head):
                    scr.addnstr(row, W - 1 - len(tail), tail, len(tail), attr | st_color.get(it["state"], 0))
                if it.get("wsid"):   # "ws 3" con el color de su número
                    scr.addnstr(row, 3, where, W - 4, attr | id_attr(it["wsid"]))
            else:
                head = f" {'↪' if it.get('handoff') else ' '} {it['proj'][:29]:<29} "
                tail = f" {ago(it['mtime']):>12} "
                scr.addnstr(row, 0, " " * (W - 1), W - 1, attr)
                scr.addnstr(row, 0, head, W - 1, attr | BLUE)
                scr.addnstr(row, len(head), it["title"][: max(0, W - 1 - len(head) - len(tail))], W - 1 - len(head), attr)
                if W - 1 - len(tail) > len(head):
                    scr.addnstr(row, W - 1 - len(tail), tail, len(tail), attr | GRAY)
        help_ = "escribí para buscar · enter abrir · esc listo" if typing else \
            "↑↓ moverse · / buscar · enter abrir · x cerrar · r recargar · q salir"
        if not typing and 0 <= cur < len(items) and items[cur][0] != "h" and items[cur][1].get("handoff"):
            it = items[cur][1]
            help_ = (f"↪ dejó el resumen para seguir: bk chat --desde {it.get('wsid') or it['sid'][:8]} "
                     "(o «Seguir desde» en alt+n) · " + help_)
        if confirm:
            busy = " ¡su chat está trabajando!" if confirm[3] in ("working", "background", "waiting") else ""
            msg = f"¿Cerrar el workspace {confirm[2] or ''} ({confirm[1]}) con su chat, riel y tira?{busy} y/n"
        scr.addnstr(H - 1, 0, (msg or help_)[: W - 1], W - 1, (RED if msg else GRAY))
        msg = ""
        scr.refresh()

        k = scr.get_wch()
        if confirm:
            if k in ("y", "Y", "s", "S"):
                subprocess.run(["tmux", "kill-session", "-t", f"={confirm[1]}"], capture_output=True)
                opened, recent = load()
            confirm = None
            continue
        if typing:
            if k in ("\x1b", "\n") or k == curses.KEY_ENTER:
                typing = False
                if k != "\x1b":
                    continue
            elif k in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                query = query[:-1]
            elif isinstance(k, str) and k.isprintable():
                query += k
                sel = 0
            continue
        if k in ("q", "\x1b"):
            if query and k == "\x1b":
                query = ""
                continue
            return None
        if k in (curses.KEY_DOWN, "j"):
            sel += 1
        elif k in (curses.KEY_UP, "k"):
            sel -= 1
        elif k == "/":
            typing = True
        elif k == "r":
            opened, recent = load()
        elif k == "x" and cur >= 0:
            it = dict(items[cur][1])
            if it.get("pane"):   # dónde vive ahora (pudo moverse desde que se cargó la lista)
                it["session"] = tmux_panes().get(it["pane"], {}).get("session")
            what, arg = close_action(it, current_workspace())
            if what == "window":
                subprocess.run(["tmux", "kill-window", "-t", arg], capture_output=True)
                if it.get("sid"):
                    try:
                        os.remove(os.path.join(STATE, it["sid"] + ".json"))
                    except OSError:
                        pass
                opened, recent = load()
            elif what == "session":
                confirm = ("session", arg, it.get("wsid"), it.get("state"))
            else:
                msg = arg
        elif k in ("\n", curses.KEY_ENTER) and cur >= 0:
            err, after = open_row(items[cur][1])
            if err:
                msg = err
            else:
                return after


def main():
    if not sys.stdout.isatty():
        o, r = load()
        for x in o:
            print("↪" if x.get("handoff") else "●", x.get("window") or "afuera", x["proj"], STATE_TXT.get(x.get("state"), ""), x["title"][:60], sep="\t")
        for x in r[:20]:
            print("↪" if x.get("handoff") else " ", x["proj"], ago(x["mtime"]), x["title"][:60], sep="\t")
        return
    after = curses.wrapper(ui)
    for cmd in after or []:
        if callable(cmd):
            cmd()
            continue
        if cmd[:2] == ["tmux", "attach"]:
            os.execvp("tmux", cmd)
        subprocess.run(cmd)


if __name__ == "__main__":
    main()
