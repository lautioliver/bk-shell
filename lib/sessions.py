#!/usr/bin/env python3
"""bk s: selector de sesiones de Claude Code, estilo Orca.

Arriba, las sesiones abiertas (con estado de la barra): Enter salta a su pane de tmux.
Abajo, las recientes de todas las carpetas (~/.claude/projects): Enter la retoma con
`claude --resume` en una ventana nueva de la sesión tmux "agents", en su carpeta.

Teclas: ↑↓/jk moverse · / buscar · enter abrir · x cerrar ventana · q/esc salir
"""
import curses, glob, json, os, re, subprocess, sys, time

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
    """pane_id -> dict(target, window, session, dead)."""
    out = {}
    for l in sh("tmux", "list-panes", "-a", "-F",
                "#{pane_id}\t#{session_name}\t#{window_index}\t#{window_name}\t#{pane_dead}").splitlines():
        p = l.split("\t")
        if len(p) == 5:
            out[p[0]] = {"session": p[1], "target": f"{p[1]}:{p[2]}", "window": p[3], "dead": p[4] == "1"}
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
            live[sid] = json.load(open(f))
        except Exception:
            pass
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
                       dead=pane["dead"] if pane else False)
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
                              "window": where(pane), "dead": False})
    # Ventanas de agentes muertas (el agente salió y la ventana quedó): para poder cerrarlas
    for pid, pane in panes.items():
        if pane["session"] == SESSION and pane["dead"] and not any(r.get("pane") == pid for r in rows_open):
            # Claude deja impreso "claude --resume <id>" al salir: con eso se puede revivir
            m = re.search(r"claude --resume ([0-9a-f-]{36})", sh("tmux", "capture-pane", "-p", "-t", pid))
            rows_open.append({"sid": m.group(1) if m else None, "cwd": "", "proj": "",
                              "title": "terminó · enter lo retoma acá" if m else "terminó (ventana abierta)",
                              "mtime": 0, "state": "ended", "target": pane["target"], "window": pane["window"],
                              "pane": pid, "dead": True})
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(cache, open(CACHE, "w"))
    except OSError:
        pass
    rows_open.sort(key=lambda r: -r["mtime"])
    return rows_open, rows_recent


def where(pane):
    """Nombre corto de dónde vive: la ventana si es un agente de bk, si no la sesión tmux (ej. bk)."""
    if not pane:
        return None
    return pane["window"] if pane["session"] == SESSION else f"{pane['session']}:{pane['window']}"


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


STATE_TXT = {"background": "2º plano", "working": "trabajando", "waiting": "te espera", "done": "listo", "idle": "libre", "ended": "terminó"}


def open_row(r):
    """Devuelve (mensaje_error | None, comando tmux a ejecutar después de cerrar curses)."""
    in_tmux = bool(os.environ.get("TMUX"))
    if r.get("dead"):
        # Pane muerto (remain-on-exit): no acepta teclas. Revivirlo con la misma sesión de Claude.
        if not r.get("sid"):
            return "esa ventana terminó y no sé qué sesión era: cerrala con x", None
        go = (["tmux", "switch-client", "-t", r["target"]] if in_tmux else ["tmux", "attach", "-t", r["target"]])
        return None, [["tmux", "respawn-pane", "-k", "-t", r["pane"], f"claude --resume {r['sid']}"], go]
    if r.get("target"):
        tgt = r["target"]
        if in_tmux:
            return None, [["tmux", "switch-client", "-t", tgt], ["tmux", "select-pane", "-t", r["pane"]]]
        return None, [["tmux", "attach", "-t", tgt]]
    if r.get("state"):  # abierta pero fuera de tmux (otra ventana de Ghostty): no duplicarla
        return "esa sesión está abierta en otra terminal: retomala ahí", None
    cwd = r["cwd"] if r["cwd"] and os.path.isdir(r["cwd"]) else os.path.expanduser("~")
    name = "r-" + re.sub(r"[^A-Za-z0-9#-]", "", r["proj"])[:12]
    has = subprocess.run(["tmux", "has-session", "-t", SESSION], capture_output=True).returncode == 0
    new = (["tmux", "new-window", "-t", SESSION + ":", "-n", name] if has
           else ["tmux", "new-session", "-d", "-s", SESSION, "-n", name])
    cmd = new + ["-c", cwd, f"claude --resume {r['sid']}"]
    after = [cmd, ["tmux", "set-option", "-w", "-t", f"{SESSION}:{name}", "remain-on-exit", "on"]]
    after.append(["tmux", "switch-client", "-t", f"{SESSION}:{name}"] if in_tmux
                 else ["tmux", "attach", "-t", f"{SESSION}:{name}"])
    return None, after


def ui(scr):
    curses.curs_set(0)
    curses.use_default_colors()
    for i, c in enumerate([183, 114, 222, 210, 111, 245], 1):
        curses.init_pair(i, c, -1)
    MAUVE, GREEN, YELLOW, RED, BLUE, GRAY = (curses.color_pair(i) for i in range(1, 7))
    st_color = {"background": YELLOW, "working": YELLOW, "waiting": RED, "done": GREEN, "idle": GRAY, "ended": GRAY}
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
                mark = "●"
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
            else:
                head = f"   {it['proj'][:29]:<29} "
                tail = f" {ago(it['mtime']):>12} "
                scr.addnstr(row, 0, " " * (W - 1), W - 1, attr)
                scr.addnstr(row, 0, head, W - 1, attr | BLUE)
                scr.addnstr(row, len(head), it["title"][: max(0, W - 1 - len(head) - len(tail))], W - 1 - len(head), attr)
                if W - 1 - len(tail) > len(head):
                    scr.addnstr(row, W - 1 - len(tail), tail, len(tail), attr | GRAY)
        help_ = "escribí para buscar · enter abrir · esc listo" if typing else \
            "↑↓ moverse · / buscar · enter abrir · x cerrar ventana · r recargar · q salir"
        scr.addnstr(H - 1, 0, (msg or help_)[: W - 1], W - 1, (RED if msg else GRAY))
        msg = ""
        scr.refresh()

        k = scr.get_wch()
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
            it = items[cur][1]
            if it.get("target") and it.get("pane") and tmux_panes().get(it["pane"], {}).get("session") == SESSION:
                subprocess.run(["tmux", "kill-window", "-t", it["target"]], capture_output=True)
                if it.get("sid"):
                    try:
                        os.remove(os.path.join(STATE, it["sid"] + ".json"))
                    except OSError:
                        pass
                opened, recent = load()
            else:
                msg = "solo cierro ventanas de agentes (sesión tmux 'agents')"
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
            print("●", x.get("window") or "afuera", x["proj"], STATE_TXT.get(x.get("state"), ""), x["title"][:60], sep="\t")
        for x in r[:20]:
            print(" ", x["proj"], ago(x["mtime"]), x["title"][:60], sep="\t")
        return
    after = curses.wrapper(ui)
    for cmd in after or []:
        if cmd[:2] == ["tmux", "attach"]:
            os.execvp("tmux", cmd)
        subprocess.run(cmd)


if __name__ == "__main__":
    main()
