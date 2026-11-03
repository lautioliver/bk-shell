#!/usr/bin/env python3
"""bk new (alt+n, o el + de la tira): crear sin salir del workspace.

  ✿ Chat nuevo en <proyecto>     otro chat del proyecto: un workspace más en la tira (2, 3…)
  ✿ Chat en una rama aparte      un workspace más, en su propio worktree y rama (chat/<nombre>): varios cambios
                                 del mismo proyecto a la vez sin pisarse
  ✿ Chat sin proyecto            un claude en ~/.bk/chats, para preguntas sueltas
  ─ Abrir workspace de otro proyecto
    barrilete-kit      abierto   ir a ese workspace
    abra                         crearlo y entrar
  / escribir una ruta

Teclas: ↑↓/jk elegir · enter · / ruta · esc cerrar.  Usa BK_CLIENT y BK_FROM (los pone el atajo de tmux).
"""
import curses, glob, json, os, re, subprocess

BK = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin", "bk")
PROJECTS = os.path.expanduser("~/.claude/projects")
SKIP = re.compile(r"/private/|/tmp/|scratch|/Library/|/\.cache/|-wt/")


def sh(*a):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=4).stdout
    except Exception:
        return ""


def toplevel(path):
    top = sh("git", "-C", path, "rev-parse", "--show-toplevel").strip()
    return top or None


def open_sessions():
    """{ruta del proyecto: nombre de la sesión} de los workspaces abiertos."""
    out = {}
    for name in sh("tmux", "list-sessions", "-F", "#{session_name}").split():
        if not name.startswith("bk"):
            continue
        env = sh("tmux", "show-environment", "-t", name, "BK_PROJECT").strip()
        if env.startswith("BK_PROJECT="):
            out[env.split("=", 1)[1]] = name
    return out


def recent_projects(limit=25):
    """Carpetas de las sesiones de Claude más recientes, llevadas a la raíz de su repo."""
    files = []
    for p in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        try:
            files.append((os.path.getmtime(p), p))
        except OSError:
            pass
    seen, out = set(), []
    for _, p in sorted(files, reverse=True)[:200]:
        cwd = ""
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = re.search(r'"cwd":"((?:[^"\\]|\\.)*)"', line)
                    if m:
                        cwd = m.group(1)
                        break
        except OSError:
            continue
        if not cwd or SKIP.search(cwd + "/") or not os.path.isdir(cwd):
            continue
        top = toplevel(cwd) or cwd
        if top in seen or top == os.path.expanduser("~"):
            continue
        seen.add(top)
        out.append(top)
        if len(out) >= limit:
            break
    return out


def ui(scr):
    curses.curs_set(0)
    curses.use_default_colors()
    for k, v in {1: 218, 2: 182, 3: 244, 4: 147}.items():
        curses.init_pair(k, v, -1)
    ROSE, PETAL, MUTED, IRIS = (curses.color_pair(i) for i in range(1, 5))
    here = os.environ.get("BK_FROM", "")
    opened = open_sessions()
    here_proj = next((p for p, s in opened.items() if s == here), None)
    projs = list(opened)
    for p in recent_projects():
        if p not in projs:
            projs.append(p)
    items = []
    if here_proj:
        items.append(("chat", here_proj, f"✿ Chat nuevo en {os.path.basename(here_proj)}"))
        if os.path.exists(os.path.join(here_proj, ".git")):
            items.append(("branch", here_proj, f"✿ Chat en una rama aparte en {os.path.basename(here_proj)}"))
    items.append(("loose", "", "✿ Chat sin proyecto"))
    items += [("ws", p, p) for p in projs]
    sel, typing, path = 0, False, ""
    naming = False  # escribiendo el nombre de la rama aparte
    while True:
        H, W = scr.getmaxyx()
        scr.erase()
        scr.addnstr(0, 1, "✿ Nuevo", W - 2, ROSE | curses.A_BOLD)
        y = 2
        top = max(0, sel - (H - 6))
        shown_header = False
        for i, (kind, p, label) in enumerate(items):
            if i < top:
                continue
            if y >= H - 2:
                break
            if kind == "ws" and not shown_header:
                scr.addnstr(y, 1, "─ Workspace de un proyecto", W - 2, MUTED)
                y += 1
                shown_header = True
                if y >= H - 2:
                    break
            attr = curses.A_REVERSE if i == sel and not typing else 0
            if kind in ("chat", "branch", "loose"):
                scr.addnstr(y, 2, label, W - 3, attr | ROSE | curses.A_BOLD)
            else:
                name = os.path.basename(p)
                tag = "abierto" if p in opened else ""
                short = p.replace(os.path.expanduser("~"), "~")
                line = f"  {name:<22} {tag:<8} {short}"
                scr.addnstr(y, 2, line, W - 3, attr | (IRIS if tag else 0))
            y += 1
        if typing:
            scr.addnstr(H - 2, 1, ("nombre del cambio › " if naming else "ruta › ") + path, W - 2, PETAL)
            hint = "enter abrir · esc volver"
        else:
            hint = "↑↓ elegir · enter abrir · / escribir una ruta · esc cerrar"
        scr.addnstr(H - 1, 1, hint, W - 2, MUTED)
        scr.refresh()
        k = scr.get_wch()
        if typing:
            if k == "\x1b":
                typing, naming, path = False, False, ""
            elif k in ("\n", curses.KEY_ENTER) and naming:
                if path.strip():
                    return ("branch", path.strip())
            elif k in ("\n", curses.KEY_ENTER):
                d = os.path.expanduser(path.strip())
                if os.path.isdir(d):
                    return ("ws", os.path.abspath(d))
            elif k in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                path = path[:-1]
            elif isinstance(k, str) and k.isprintable():
                path += k
            continue
        if k in ("\x1b", "q"):
            return None
        if k in (curses.KEY_DOWN, "j"):
            sel = min(len(items) - 1, sel + 1)
        elif k in (curses.KEY_UP, "k"):
            sel = max(0, sel - 1)
        elif k == "/":
            typing = True
        elif k in ("\n", curses.KEY_ENTER) and items:
            kind, p, _ = items[sel]
            if kind == "branch":  # primero el nombre del cambio
                typing, naming, path = True, True, ""
                continue
            return (kind, p)


def main():
    choice = curses.wrapper(ui)
    if not choice:
        return
    kind, path = choice
    env = dict(os.environ)
    if kind == "chat":
        subprocess.run([BK, "chat", os.environ.get("BK_FROM", "")], env=env)
    elif kind == "branch":
        r = subprocess.run([BK, "chat", "--rama", path, os.environ.get("BK_FROM", "")], env=env)
        if r.returncode != 0:  # el popup se cerraría y el error de bk no se llegaría a leer
            try:
                input("\n(enter cierra)")
            except (EOFError, KeyboardInterrupt):
                pass
    elif kind == "loose":
        subprocess.run([BK, "chat", "--suelto", os.environ.get("BK_FROM", "")], env=env)
    else:
        sess = open_sessions().get(path)
        client = os.environ.get("BK_CLIENT")
        if sess:
            subprocess.run(["tmux", "switch-client"] + (["-c", client] if client else []) + ["-t", sess])
        else:
            subprocess.run([BK, "ws", path], env=env, cwd=path)


if __name__ == "__main__":
    main()
