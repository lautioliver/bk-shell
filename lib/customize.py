#!/usr/bin/env python3
"""bk customize (alt+,): estilo del workspace, a lo /gentle:customize.

Izquierda las categorías, derecha las opciones; se aplica al instante en todos los workspaces
abiertos (sesiones bk-*): guarda ~/.config/bk/ui.json, rehace el header y reinicia los rieles.
Teclas: ←/→ o tab paneles · ↑/↓ mover · enter aplicar · esc cerrar
"""
import curses, os, subprocess, sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import gentle as G

BK = os.path.join(os.path.dirname(HERE), "bin", "bk")
CATS = [
    ("Cards", "cards", [("float", "panel con fondo y barra ▎ (Gentle Shell 4)"),
                        ("neon", "marco redondeado (Gentle Shell 3)")]),
    ("Density", "density", [("comfortable", "aire arriba y abajo de cada título"),
                            ("compact", "sin filas vacías")]),
    ("Header", "header", [("top", "barra arriba"), ("bottom", "barra abajo")]),
]


CAT_W = 18        # columna de categorías (izquierda)
DESC_OFFSET = 36  # la descripción de cada opción empieza tantas columnas después de la lista…
DESC_MIN = 20     # …y si no le quedan al menos estas, pasa a una línea propia debajo de las opciones


def desc_col(W):
    """Columna de la descripción al lado de cada opción, o None si no entra (se muestra debajo)."""
    col = CAT_W + DESC_OFFSET
    return col if W - col - 1 >= DESC_MIN else None


def current(key, conf):
    if key == "theme":
        return subprocess.run([BK, "theme"], capture_output=True, text=True).stdout.split(" ")[0].strip()
    return conf.get(key)


def apply(key, value):
    if key == "theme":
        subprocess.run([BK, "theme", value], capture_output=True)
    else:
        conf = G.ui()
        conf[key] = value
        G.save_ui(conf)
    # Rehacer header y rieles de todos los workspaces abiertos
    out = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True).stdout
    for s in out.split():
        if not s.startswith("bk"):
            continue
        subprocess.run([BK, "_style", s], capture_output=True)
        panes = subprocess.run(["tmux", "list-panes", "-t", f"{s}:main", "-F", "#{pane_id} #{pane_start_command}"],
                               capture_output=True, text=True).stdout
        for l in panes.splitlines():
            pid, _, cmd = l.partition(" ")
            if "bk rail" in cmd:
                env = subprocess.run(["tmux", "show-environment", "-t", s, "BK_PROJECT"],
                                     capture_output=True, text=True).stdout.strip()
                args = ["tmux", "respawn-pane", "-k", "-t", pid]
                if env.startswith("BK_PROJECT="):
                    args += ["-e", env]
                subprocess.run(args + ["bk rail"], capture_output=True)


def ui(scr):
    curses.curs_set(0)
    curses.use_default_colors()
    A = G.curses_attrs()
    ROSE, PETAL, MUTED, TEXT = A["rose"], A["petal"], A["muted"], A["text"]
    cat, opt, pane, msg = 0, 0, 0, ""
    while True:
        conf = G.ui()
        H, W = scr.getmaxyx()
        scr.erase()
        scr.addnstr(0, 1, "✿ Customize", W - 2, ROSE | curses.A_BOLD)
        scr.addnstr(1, 1, "VISUAL · CATEGORÍAS", W - 2, MUTED)
        LW = CAT_W
        for i, (name, _, _) in enumerate(CATS):
            attr = curses.A_REVERSE if (i == cat and pane == 0) else (PETAL | curses.A_BOLD if i == cat else TEXT)
            scr.addnstr(3 + i, 2, f" {name:<14}", LW - 2, attr)
        for y in range(2, H - 3):
            scr.addnstr(y, LW, "│", 1, MUTED)
        name, key, opts = CATS[cat]
        cur = current(key, conf)
        for i, (val, desc) in enumerate(opts):
            mark = "▸ " if (i == opt and pane == 1) else "  "
            label = f"{name}: {val}" + ("  (actual)" if val == cur else "")
            attr = (ROSE | curses.A_BOLD) if (i == opt and pane == 1) else TEXT
            scr.addnstr(3 + i, LW + 2, mark + label, W - LW - 3, attr)
            dc = desc_col(W)
            if dc is not None:
                scr.addnstr(3 + i, dc, desc, max(0, W - dc - 1), MUTED)
            elif i == opt and pane == 1:   # terminal angosta: la de la opción elegida, debajo de la lista
                scr.addnstr(3 + len(opts) + 1, LW + 2, desc, max(0, W - LW - 3), MUTED)
        if msg:
            scr.addnstr(H - 3, 2, msg, W - 3, PETAL)
        scr.addnstr(H - 1, 1, "←/→ o tab paneles · ↑/↓ mover · enter aplicar · esc cerrar", W - 2, MUTED)
        scr.refresh()
        k = scr.getch()
        msg = ""
        if k in (27, ord("q")):
            return
        if k in (9, curses.KEY_RIGHT, ord("l")):
            pane = 1
        elif k in (curses.KEY_LEFT, ord("h"), curses.KEY_BTAB):
            pane = 0
        elif k in (curses.KEY_DOWN, ord("j")):
            if pane == 0:
                cat = (cat + 1) % len(CATS); opt = 0
            else:
                opt = (opt + 1) % len(opts)
        elif k in (curses.KEY_UP, ord("k")):
            if pane == 0:
                cat = (cat - 1) % len(CATS); opt = 0
            else:
                opt = (opt - 1) % len(opts)
        elif k in (10, curses.KEY_ENTER):
            if pane == 0:
                pane = 1
            else:
                apply(key, opts[opt][0])
                msg = f"✿ {name}: {opts[opt][0]} aplicado"


if __name__ == "__main__":
    curses.wrapper(ui)
