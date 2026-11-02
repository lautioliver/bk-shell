#!/usr/bin/env python3
"""bk watch --all: columna de agentes del workspace, en un solo panel que se adapta.

0 agentes  -> un estado vacío con qué hacer.
N agentes  -> N secciones parejas: encabezado (ventana · rol · modelo · estado) + lo último que
              dijo/hizo, sin el pie de Claude Code. Los terminados van abajo, en una línea.
Se redibuja cada segundo. Solo lectura: para escribirles, bk say o el chat.
"""
import glob, json, os, re, subprocess, sys, time, unicodedata

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import gentle as G

SESSION = os.environ.get("BK_SESSION", "agents")
STATE = os.environ.get("BK_STATE_DIR", os.path.expanduser("~/.cache/bk/agents"))
SHELLS = {"zsh", "bash", "sh", "fish", "-zsh", "login"}
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

RST, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
MAUVE, GREEN, YELLOW, RED, BLUE, GRAY = (f"\033[38;5;{c}m" for c in (183, 114, 222, 210, 111, 245))
STATE_STYLE = {"working": ("● trabajando", YELLOW), "waiting": ("● te espera", RED),
               "background": ("◐ en 2º plano", YELLOW),
               "done": ("✓ listo", GREEN), "idle": ("· libre", GRAY)}


def sh(*a):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""


def cw(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def cut(line, width):
    """Corta a `width` columnas visibles conservando los códigos de color."""
    out, w, i = [], 0, 0
    while i < len(line):
        m = ANSI.match(line, i)
        if m:
            out.append(m.group(0)); i = m.end(); continue
        c = cw(line[i])
        if w + c > width:
            break
        out.append(line[i]); w += c; i += 1
    return "".join(out) + RST


def visible(line):
    return ANSI.sub("", line)


def states_by_pane():
    out = {}
    for f in glob.glob(os.path.join(STATE, "*.json")):
        try:
            s = json.load(open(f))
        except Exception:
            continue
        if s.get("pane"):
            # Si hay dos (pre-registro bk-* y el del hook), gana el más nuevo
            if s["pane"] not in out or s.get("ts", 0) > out[s["pane"]].get("ts", 0):
                out[s["pane"]] = s
    return out


PROJECT = os.environ.get("BK_PROJECT", "")


def in_project(path):
    if not PROJECT:
        return True
    p = (path or "").rstrip("/")
    return p == PROJECT or p.startswith(PROJECT + "/") or p.startswith(PROJECT + "-wt/")


def agents():
    live, ended = [], []
    st = states_by_pane()
    for l in sh("tmux", "list-panes", "-s", "-t", SESSION, "-F",
                "#{window_name}\t#{pane_id}\t#{pane_dead}\t#{pane_current_command}\t#{pane_current_path}\t#{@bk_ws}").splitlines():
        p = l.split("\t")
        if len(p) != 6 or p[5]:      # el chat de un workspace, de visita (alt+shift+N)
            continue
        win, pane, dead, cmd, path, _ = p
        a = {"win": win, "pane": pane, "state": st.get(pane, {})}
        if not in_project(a["state"].get("cwd") or path):
            continue
        (ended if dead == "1" or cmd in SHELLS else live).append(a)
    order = {"waiting": 0, "working": 1, "background": 1, "done": 2, "idle": 3}
    live.sort(key=lambda a: order.get(a["state"].get("state"), 9))
    return live, ended


def body(pane):
    lines = [l for l in sh("tmux", "capture-pane", "-p", "-e", "-S", "-200", "-t", pane).splitlines() if visible(l).strip()]
    seps = [i for i, l in enumerate(lines) if visible(l).startswith("─────")]
    if len(seps) >= 2:          # caja del prompt de Claude Code: fuera
        lines = lines[: seps[-2]]
    return lines


def model(m):
    m = (m or "").replace("claude-", "")
    for fam in ("opus", "sonnet", "haiku", "fable"):
        if m.startswith(fam):
            return (fam + " " + m[len(fam):].strip("-").replace("-", ".")).strip()
    return m


def header(a, W):
    s = a["state"]
    txt, col = STATE_STYLE.get(s.get("state"), (s.get("state") or "", GRAY))
    if s.get("tool") == "confianza":
        txt, col = "● confiá la carpeta", RED
    left = f"{MAUVE}{BOLD}▍{a['win']}{RST}"
    meta = " · ".join(x for x in (s.get("role"), model(s.get("model"))) if x)
    plain = f"▍{a['win']}  {meta}"
    pad = max(1, W - len(plain) - len(txt) - 1)
    return cut(f"{left}  {GRAY}{meta}{RST}" + " " * pad + f"{col}{txt}{RST}", W)


def frame(W, H):
    live, ended = agents()
    out = []
    if not live:
        mid = max(0, H // 2 - 4)
        out += [""] * mid
        out += G.card("Agents", [f"{G.MUTED}nadie trabajando{G.RST}", "",
                                 f"{G.PETAL}bk run <issue>{G.RST}   {G.MUTED}implementer{G.RST}",
                                 f"{G.PETAL}bk review <pr>{G.RST}   {G.MUTED}reviewer{G.RST}",
                                 f"{G.PETAL}alt+k{G.RST}            {G.MUTED}todos los comandos{G.RST}"],
                      min(W, 60), glyph="∗")
    else:
        room = H - (len(ended) + 1 if ended else 0)
        shown = live[: max(1, room // 6)]
        each = room // len(shown)
        for k, a in enumerate(shown):
            n = room - each * k if k == len(shown) - 1 else each
            s_ = a["state"]
            txt, col = STATE_STYLE.get(s_.get("state"), (s_.get("state") or "", GRAY))
            if s_.get("state") == "waiting":
                q = (s_.get("wait") or ("question" if s_.get("tool") == "AskUserQuestion" else "")) == "question"
                txt = "? te pregunta" if q else "! pide permiso"
            if s_.get("tool") == "confianza":
                txt, col = "● confiá la carpeta", RED
            busy = s_.get("state") in ("working", "background")
            frame_c = G.RED if s_.get("state") == "waiting" else G.IRIS if busy else G.FRAME
            content = n - 2
            lines = body(a["pane"])[-content:] if content > 0 else []
            rows = [""] * (content - len(lines)) + lines
            mins = int((time.time() - s_.get("ts", time.time())) // 60)
            right = " · ".join(x for x in (model(s_.get("model")), txt.strip("●◐✓· "), f"{mins}m") if x)
            title = f"{a['win']} {G.MUTED}· {s_.get('role') or 'agente'}"
            out += G.card(title, rows, W, color=frame_c, accent=G.IRIS if busy else G.ROSE, glyph="∗", right=right)
    if ended:
        out = out[: H - len(ended) - 1] + [""] * max(0, H - len(ended) - 1 - len(out))
        out.append(f"{GRAY}terminados · alt+s para retomar o cerrar{RST}")
        out += [cut(f"  {GRAY}✓ {a['win']}{RST}", W) for a in ended]
    return out[:H]


def main():
    # Teclas: q o esc cierran (en el popup de alt+a no había forma de salir). Solo si hay terminal.
    import select, termios, tty
    fd, old = sys.stdin.fileno(), None
    try:
        old = termios.tcgetattr(fd); tty.setcbreak(fd)
    except (termios.error, ValueError):
        pass
    sys.stdout.write("\033[?25l\033[2J")
    try:
        while True:
            try:
                sz = os.get_terminal_size(sys.stdout.fileno())
                W, H = sz.columns, sz.lines
            except OSError:
                W, H = 60, 30
            lines = (frame(W, H) + [""] * H)[:H]
            sys.stdout.write("\033[H" + "\033[K\n".join(cut(l, W) for l in lines) + "\033[K")
            sys.stdout.flush()
            if old is not None:
                r, _, _ = select.select([fd], [], [], 1)
                if r and os.read(fd, 16)[:1] in (b"q", b"\x1b"):
                    break
            else:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\033[?25h")


if __name__ == "__main__":
    main()
