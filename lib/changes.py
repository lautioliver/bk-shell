#!/usr/bin/env python3
"""bk changes (alt+g): lo que tus agentes dejaron sin commitear, a lo Gentle Changes.

Izquierda: un grupo por raíz (el repo actual + el worktree de cada agente vivo), con sus archivos.
Derecha: el diff contra HEAD del archivo elegido (los nuevos, completos).
Teclas: j/k elegir · ctrl+j/k o J/K scroll del diff · F diff a todo el ancho · o abrir en $EDITOR ·
        r recargar · q/esc salir
"""
import curses, glob, json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import gentle as G  # noqa: E402  (paleta del tema, una sola para todas las vistas)

SESSION = os.environ.get("BK_SESSION", "agents")
STATE = os.environ.get("BK_STATE_DIR", os.path.expanduser("~/.cache/bk/agents"))


def sh(*a, cwd=None):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=5, cwd=cwd).stdout
    except Exception:
        return ""


def roots():
    cands = [os.getcwd()]
    panes = set(sh("tmux", "list-panes", "-s", "-t", SESSION, "-F", "#{pane_id}").split())
    for f in glob.glob(os.path.join(STATE, "*.json")):
        try:
            s = json.load(open(f))
        except Exception:
            continue
        if s.get("cwd") and (not panes or s.get("pane") in panes or not s.get("pane")):
            cands.append(s["cwd"])
    seen, out = set(), []
    for c in cands:
        top = sh("git", "-C", c, "rev-parse", "--show-toplevel").strip()
        if top and top not in seen:
            seen.add(top); out.append(top)
    return out


def load():
    items = []  # ("root", top, branch) | ("file", top, code, path, add, del)
    for top in roots():
        st = [l for l in sh("git", "-C", top, "status", "--porcelain", "-uall").splitlines() if l.strip()]
        if not st:
            continue
        num = {}
        for l in sh("git", "-C", top, "diff", "--numstat", "HEAD").splitlines():
            p = l.split("\t")
            if len(p) == 3:
                num[p[2]] = (p[0], p[1])
        items.append(("root", top, sh("git", "-C", top, "branch", "--show-current").strip()))
        for l in st:
            code = l[:2].strip() or "M"
            path = l[3:].split(" -> ")[-1]
            a, d = num.get(path, ("", ""))
            if code == "??":
                code = "A"
                try:
                    a = str(sum(1 for _ in open(os.path.join(top, path), errors="replace")))
                except OSError:
                    a = ""
            items.append(("file", top, code, path, a, d))
    return items


def diff(item):
    _, top, code, path, *_ = item
    if code == "A" and not sh("git", "-C", top, "ls-files", "--", path).strip():
        try:
            body = open(os.path.join(top, path), errors="replace").read().splitlines()
        except OSError:
            return ["(no se puede leer)"]
        return [f"@@ -0,0 +1,{len(body)} @@"] + ["+" + l for l in body]
    out = sh("git", "-C", top, "diff", "HEAD", "--", path).splitlines()
    return out[4:] if len(out) > 4 else out or ["(sin diff de texto)"]


def ui(scr):
    curses.curs_set(0)
    curses.use_default_colors()
    A = G.curses_attrs()
    ROSE, PETAL, GREEN, RED, AMBER, MUTED, IRIS = (A[k] for k in ("rose", "petal", "green", "red", "amber", "muted", "iris"))
    items = load()
    files = [i for i, it in enumerate(items) if it[0] == "file"]
    sel, scroll, full, cache = 0, 0, False, {}
    while True:
        H, W = scr.getmaxyx()
        scr.erase()
        title = f" ✎ Changes · {len({it[1] for it in items})} worktrees "
        scr.addnstr(0, 0, "─" * (W - 1), W - 1, MUTED)
        scr.addnstr(0, 2, title, W - 3, ROSE | curses.A_BOLD)
        if not files:
            scr.addnstr(2, 2, "Nada sin commitear en el repo ni en los worktrees de los agentes.", W - 3, MUTED)
            scr.addnstr(H - 1, 1, "r refresh   esc close", W - 2, MUTED)
            scr.refresh()
            k = scr.getch()
            if k in (ord("q"), 27):
                return
            if k == ord("r"):
                items = load(); files = [i for i, it in enumerate(items) if it[0] == "file"]
            continue
        sel = max(0, min(sel, len(files) - 1))
        cur = items[files[sel]]
        LW = 0 if full else max(28, min(48, W // 3))
        body_h = H - 2
        if not full:
            # Lista: mantener el elegido a la vista
            pos = files[sel]
            top = max(0, pos - body_h + 2)
            for row, i in enumerate(range(top, min(len(items), top + body_h)), 1):
                it = items[i]
                if it[0] == "root":
                    label = f"▾ {os.path.basename(it[1])} · {it[2]}"
                    scr.addnstr(row, 1, label, LW - 2, PETAL | curses.A_BOLD)
                else:
                    code, path, a, d = it[2], it[3], it[4], it[5]
                    mark = "▸ " if i == files[sel] else "  "
                    col = GREEN if code == "A" else RED if code == "D" else AMBER
                    attr = curses.A_BOLD if i == files[sel] else 0
                    scr.addnstr(row, 1, mark, 2, ROSE)
                    scr.addnstr(row, 3, code, 1, col | attr)
                    name = path if len(path) <= LW - 7 else "…" + path[-(LW - 8):]
                    scr.addnstr(row, 5, name, LW - 6, (ROSE if i == files[sel] else 0) | attr)
            for y in range(1, H - 1):
                scr.addnstr(y, LW, "│", 1, MUTED)
        # Diff
        key = (cur[1], cur[3])
        if key not in cache:
            cache[key] = diff(cur)
        lines = cache[key]
        scroll = max(0, min(scroll, max(0, len(lines) - body_h)))
        x = LW + 2
        for row, l in enumerate(lines[scroll: scroll + body_h], 1):
            col = GREEN if l.startswith("+") else RED if l.startswith("-") else AMBER if l.startswith("@@") else 0
            scr.addnstr(row, x, l.expandtabs(4), max(1, W - x - 1), col)
        hint = "j/k select   ctrl+j/k scroll   F fullscreen   o open   r refresh   esc close"
        scr.addnstr(H - 1, 1, hint, W - 2, MUTED)
        scr.refresh()
        k = scr.getch()
        if k in (ord("q"), 27):
            return
        elif k in (ord("j"), curses.KEY_DOWN):
            sel += 1; scroll = 0
        elif k in (ord("k"), curses.KEY_UP):
            sel -= 1; scroll = 0
        elif k in (10, ord("J"), curses.KEY_NPAGE):      # ctrl+j llega como 10
            scroll += body_h // 2
        elif k in (11, ord("K"), curses.KEY_PPAGE):      # ctrl+k
            scroll -= body_h // 2
        elif k == ord("F"):
            full = not full
        elif k == ord("r"):
            items = load(); files = [i for i, it in enumerate(items) if it[0] == "file"]; cache = {}
        elif k == ord("o"):
            curses.endwin()
            subprocess.run([os.environ.get("EDITOR", "vi"), os.path.join(cur[1], cur[3])])
            scr.refresh()


if __name__ == "__main__":
    curses.wrapper(ui)
