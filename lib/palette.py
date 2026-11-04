#!/usr/bin/env python3
"""bk palette (alt+k): todos los comandos de bk agrupados y con búsqueda, a lo gentle-shell.

Escribí para filtrar · ↑↓ elegir · enter correr (pide el argumento si hace falta) · esc salir.
Los que muestran texto (tokens, roles…) dejan la salida en pantalla hasta una tecla; las vistas
(cambios, sesiones, radio) reemplazan a la paleta.
"""
import curses, os, subprocess, sys

BK = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin", "bk")

# (grupo, etiqueta, comando, argumento pedido | None, tipo)  tipo: run | show | view
ENTRIES = [
    ("Agentes", "Implementar un issue", "run", "issue", "run"),
    ("Agentes", "Revisar un PR", "review", "PR", "run"),
    ("Agentes", "Corregir un PR abierto", "fix", "PR y la instrucción", "run"),
    ("Agentes", "Planear una feature", "plan", "idea", "run"),
    ("Agentes", "Orquestar (hasta 3 issues)", "orchestrate", None, "run"),
    ("Agentes", "Cerrar la ventana de un agente", "stop", "ventana", "run"),
    ("Vistas", "Agentes en vivo", "watch --all", None, "view"),
    ("Vistas", "Cambios sin commitear", "changes", None, "view"),
    ("Vistas", "Terminal rápida (alt+x)", "term", None, "view"),
    ("Vistas", "Ver un subagente en vivo", "sub", None, "view"),
    ("Vistas", "Sesiones de Claude", "s", None, "view"),
    ("Vistas", "Estadísticas de uso", "stats", None, "view"),
    ("Vistas", "Personalizar el workspace", "customize", None, "view"),
    ("Vistas", "Ver la pantalla de un agente", "peek", "ventana", "show"),
    ("GitHub", "Ver checks fallidos para re-correr", "rerun", None, "show"),
    ("GitHub", "Re-correr los checks fallidos", "rerun --go", None, "show"),
    ("Sesión", "Lista de agentes", "ls", None, "show"),
    ("Sesión", "Tokens y costo de hoy", "tokens", None, "show"),
    ("Sesión", "Roles y modelos", "roles", None, "show"),
    ("Música", "Radio (pantalla)", "song ui", None, "view"),
    ("Música", "Pausa / seguir", "song pause", None, "show"),
    ("Música", "Siguiente estación", "song next", None, "show"),
    ("Música", "Rock", "song rock", None, "show"),
    ("Música", "Reggaeton", "song reggaeton", None, "show"),
    ("Música", "Cachengue", "song cachengue", None, "show"),
    ("Música", "Techno", "song techno", None, "show"),
    ("Música", "House", "song house", None, "show"),
    ("Música", "Guardar la radio que suena", "song fav", None, "show"),
    ("Música", "Últimos temas", "song hist", None, "show"),
    ("Música", "Cortar la música", "song stop", None, "show"),
]


def ui(scr):
    curses.curs_set(1)
    curses.use_default_colors()
    for k, v in {1: 211, 2: 182, 3: 244, 4: 147}.items():
        curses.init_pair(k, v, -1)
    ROSE, PETAL, MUTED, IRIS = (curses.color_pair(i) for i in range(1, 5))
    q, sel, ask, arg, chosen = "", 0, False, "", None
    while True:
        H, W = scr.getmaxyx()
        scr.erase()
        scr.addnstr(0, 0, "─" * (W - 1), W - 1, MUTED)
        scr.addnstr(0, 2, " ✿ Comandos ", W - 3, ROSE | curses.A_BOLD)
        if ask:
            scr.addnstr(2, 2, f"{chosen[1]} · {chosen[3]}:", W - 3, PETAL)
            scr.addnstr(3, 2, "› " + arg, W - 3, curses.A_BOLD)
            scr.addnstr(H - 1, 1, "enter correr   esc volver", W - 2, MUTED)
            scr.move(3, min(W - 2, 4 + len(arg)))
        else:
            ql = q.lower()
            match = [e for e in ENTRIES if ql in f"{e[0]} {e[1]} {e[2]}".lower()]
            sel = max(0, min(sel, len(match) - 1))
            scr.addnstr(1, 2, "› " + q, W - 3, curses.A_BOLD)
            row, group = 3, None
            for i, e in enumerate(match):
                if row >= H - 1:
                    break
                if e[0] != group:
                    group = e[0]
                    scr.addnstr(row, 2, group, W - 3, PETAL | curses.A_BOLD)
                    row += 1
                attr = curses.A_REVERSE if i == sel else 0
                label = f"  {e[1]}"
                cmd = f"bk {e[2]}" + (f" <{e[3]}>" if e[3] else "")
                scr.addnstr(row, 2, label.ljust(max(0, W - 4 - len(cmd))) + cmd, W - 3, attr)
                if i != sel:
                    scr.addnstr(row, max(2, W - 2 - len(cmd)), cmd, len(cmd), MUTED)
                row += 1
            scr.addnstr(H - 1, 1, "escribí para buscar   ↑↓ elegir   enter correr   esc cerrar", W - 2, MUTED)
            scr.move(1, min(W - 2, 4 + len(q)))
        scr.refresh()
        k = scr.get_wch()
        if ask:
            if k == "\x1b":
                ask, arg = False, ""
            elif k in ("\n", curses.KEY_ENTER):
                if arg.strip():
                    return chosen, arg.strip()
            elif k in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                arg = arg[:-1]
            elif isinstance(k, str) and k.isprintable():
                arg += k
            continue
        if k == "\x1b":
            if q:
                q = ""
                continue
            return None, None
        if k in (curses.KEY_DOWN, "\x0e"):
            sel += 1
        elif k in (curses.KEY_UP, "\x10"):
            sel -= 1
        elif k in ("\n", curses.KEY_ENTER):
            if not match:
                continue
            chosen = match[sel]
            if chosen[3]:
                ask = True
            else:
                return chosen, ""
        elif k in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            q = q[:-1]; sel = 0
        elif isinstance(k, str) and k.isprintable():
            q += k; sel = 0


def main():
    entry, arg = curses.wrapper(ui)
    if not entry:
        return
    _, label, cmd, _, kind = entry
    argv = [BK] + cmd.split()
    if arg:
        # "fix" lleva PR + instrucción: el primer token es el PR, el resto la instrucción
        argv += arg.split(None, 1) if cmd == "fix" else arg.split()
    if kind == "view":
        os.execv(BK, argv)
    print(f"\033[38;2;240;149;200m✿\033[0m {label}  \033[2m{' '.join(['bk'] + argv[1:])}\033[0m\n")
    subprocess.run(argv)
    if kind == "show" or True:
        try:
            input("\n\033[2m(enter para cerrar)\033[0m")
        except (EOFError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    main()
