#!/usr/bin/env python3
"""bk sidebar: tira izquierda del workspace con las sesiones activas, a lo Gentle Shell 4.

    1 ●     arriba: los workspaces (sesiones bk-*), en orden de creación; alt+1…9 salta a cada uno
    2 ○     abajo: los agentes (ventanas de la sesión "agents")
    ─       ● trabajando · ● rojo te espera · ○ libre/listo · · sin claude
    1 ●     el actual va resaltado. Clic en una fila para ir ahí.
"""
import curses, glob, json, os, subprocess, time

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
        p = s.get("pane")
        if p and (p not in out or s.get("ts", 0) > out[p].get("ts", 0)):
            out[p] = s
    return out


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
        state = "none"
        for l in sh("tmux", "list-panes", "-t", f"{name}:main", "-F",
                    "#{pane_id}\t#{pane_current_command}\t#{pane_dead}\t#{pane_start_command}").splitlines():
            pid, cmd, dead, start = (l.split("\t") + ["", "", "", ""])[:4]
            if "bk rail" in start or "bk sidebar" in start:
                continue
            s = pane_state(st, pid, cmd, dead)
            if s != "none":
                state = s
        out.append(("ws", name, name, state))
    for l in sh("tmux", "list-panes", "-s", "-t", AGENTS, "-F",
                "#{window_name}\t#{pane_id}\t#{pane_current_command}\t#{pane_dead}\t#{@bk_ws}").splitlines():
        win, pid, cmd, dead, ws = (l.split("\t") + ["", "", "", "", ""])[:5]
        if ws:      # el chat de un workspace, de visita mientras un agente ocupa su lugar
            continue
        out.append(("ag", win, f"{AGENTS}:{win}", pane_state(st, pid, cmd, dead)))
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
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    for k, v in {1: 222, 2: 210, 3: 244, 4: 218, 5: 147}.items():
        curses.init_pair(k, v, -1)
    WORK, WAIT, MUTED, ROSE, IRIS = (curses.color_pair(i) for i in range(1, 6))
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
        for kind, label, target, state in cache:
            if kind == "ag" and not sep:
                if y < H - 1:
                    scr.addnstr(y, 1, "─" * max(1, W - 2), W - 2, MUTED)
                y += 1
                sep = True
            if y >= H - 1:
                break
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
            scr.addnstr(y, 1, f"{num:>1}", 2, numattr)
            scr.addnstr(y, 3, dot, 1, col)
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
            if my in rowmap and b & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED | curses.BUTTON1_RELEASED):
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
            break
        except Exception:
            os.makedirs(os.path.dirname(log), exist_ok=True)
            with open(log, "a") as f:
                f.write(time.strftime("%F %T ") + traceback.format_exc() + "\n")
            time.sleep(1)
