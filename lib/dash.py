#!/usr/bin/env python3
"""bk dash: franja de estado del workspace. Dos tablas con bordes: agentes y uso del día.

Lado a lado si entran en el ancho del panel, una arriba de la otra si no. Se redibuja cada 5 s.
Fuentes: el estado que escribe el hook ($BK_STATE_DIR/*.json, default ~/.cache/bk/agents), las ventanas de
tmux y usage.py --json. --once dibuja una vez y sale.
"""
import glob, json, os, shutil, subprocess, sys, time, unicodedata

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import gentle as G  # noqa: E402  (paleta del tema, una sola para todas las vistas)
STATE = os.environ.get("BK_STATE_DIR") or os.path.expanduser("~/.cache/bk/agents")
SESSION = os.environ.get("BK_SESSION", "agents")

RST, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
C = {"mauve": G.ROSE, "green": G.GREEN, "yellow": G.AMBER, "red": G.RED, "blue": G.IRIS, "gray": G.MUTED}


def state_style(state):
    """("● trabajando", color ANSI): glifo y texto de gentle.STATES, la misma tabla que la tira y el riel."""
    g, txt, col = G.state_ansi(state)
    return f"{g} {txt}", col


def width(s):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 0 if unicodedata.combining(ch) else 1 for ch in s)


def pad(s, n, right=False):
    s = s if width(s) <= n else s[: n - 1] + "…"
    sp = " " * (n - width(s))
    return sp + s if right else s + sp


def table(title, head, rows, right_cols=(), colors=None):
    """rows: listas de str; colors: lista paralela de listas de códigos ANSI (o None)."""
    w = [max([width(h)] + [width(r[i]) for r in rows]) for i, h in enumerate(head)]
    top = "╭─ " + title + " " + "─" * max(0, sum(w) + 3 * len(w) - 1 - width(title) - 3) + "╮"
    out = [C["mauve"] + top + RST]
    cells = lambda r, cs=None: (C["mauve"] + "│" + RST) + (C["mauve"] + "│" + RST).join(
        " " + (cs[i] if cs and cs[i] else "") + pad(r[i], w[i], i in right_cols) + (RST if cs and cs[i] else "") + " "
        for i in range(len(head))) + (C["mauve"] + "│" + RST)
    out.append(cells(head, [BOLD] * len(head)))
    out.append(C["mauve"] + "├" + "┼".join("─" * (x + 2) for x in w) + "┤" + RST)
    for i, r in enumerate(rows):
        out.append(cells(r, colors[i] if colors else None))
    out.append(C["mauve"] + "╰" + "┴".join("─" * (x + 2) for x in w) + "╯" + RST)
    return out, sum(w) + 3 * len(w) + 1


def short_model(m):
    m = (m or "").replace("claude-", "")
    for fam in ("opus", "sonnet", "haiku", "fable"):
        if m.startswith(fam):
            ver = m[len(fam):].strip("-").replace("-", ".")
            return f"{fam} {ver}".strip()
    return m or "—"


def agents():
    panes = {}
    try:
        out = subprocess.run(["tmux", "list-panes", "-s", "-t", SESSION, "-F", "#{pane_id} #{window_name}"],
                             capture_output=True, text=True, timeout=2).stdout
        panes = dict(l.split(" ", 1) for l in out.splitlines() if " " in l)
    except Exception:
        pass
    items = []
    for f in glob.glob(os.path.join(STATE, "*.json")):
        try:
            items.append(json.load(open(f)))
        except Exception:
            pass
    order = {"waiting": 0, "working": 1, "background": 1, "done": 2, "idle": 3}
    items.sort(key=lambda a: (order.get(a.get("state"), 9), -a.get("ts", 0)))
    rows, colors, now = [], [], time.time()
    for a in items:
        parts = (a.get("cwd") or "").rstrip("/").split("/")
        proj = parts[-1] if parts else "—"
        if len(parts) > 1 and parts[-2].endswith("-wt"):
            proj = parts[-2][:-3] + "#" + parts[-1]
        win = panes.get(a.get("pane") or "", "")
        ref = a.get("ref") or ""
        st, col = state_style(a.get("state"))
        if a.get("phase") == "plan_pending" and a.get("state") in ("done", "idle"):
            st, col = "● plan: tu sí", C["red"]
        if a.get("tool") == "confianza":
            st, col = state_style("trust")
        mins = int((now - a.get("ts", now)) // 60)
        ago = f"{mins}m" if mins < 60 else f"{mins // 60}h{mins % 60:02d}"
        rows.append([win or "chat", proj + (f" {ref}" if ref and ref not in proj else ""),
                     a.get("role") or "—", short_model(a.get("model")), st, ago])
        colors.append([C["blue"] if win else C["gray"], "", "", C["gray"], col, C["gray"]])
    if not rows:
        rows, colors = [["—", "sin agentes", "", "", "", ""]], None
    return table("agentes", ["ventana", "proyecto", "rol", "modelo", "estado", "hace"], rows,
                 right_cols=(5,), colors=colors)


def human(n):
    for unit, d in (("M", 1e6), ("k", 1e3)):
        if n >= d:
            return f"{n / d:.1f}{unit}"
    return str(int(n))


def usage():
    try:
        u = json.loads(subprocess.run([sys.executable, os.path.join(HERE, "usage.py"), "--json"],
                                      capture_output=True, text=True, timeout=10).stdout)
    except Exception:
        return table("uso de hoy", ["modelo", "tokens", "costo"], [["—", "—", "—"]])
    rows, colors = [], []
    for m, v in sorted(u.get("by_model", {}).items(), key=lambda kv: -kv[1]["cost"]):
        rows.append([short_model(m), human(v["tokens"]), f"${v['cost']:.2f}", str(v["msgs"])])
        colors.append(["", "", C["yellow"], C["gray"]])
    rows.append(["total", human(u.get("tokens", 0)), f"${u.get('cost_usd', 0):.2f}", str(u.get("messages", 0))])
    colors.append([BOLD, BOLD, BOLD + C["yellow"], C["gray"]])
    return table("uso de hoy (≈ API)", ["modelo", "tokens", "costo", "resp"], rows, right_cols=(1, 2, 3), colors=colors)


def frame():
    # El tamaño real del pane (no $COLUMNS, que puede venir heredado de otra terminal)
    try:
        cols = os.get_terminal_size(sys.stdout.fileno()).columns
    except OSError:
        cols = shutil.get_terminal_size((100, 15)).columns
    (a, aw), (u, uw) = agents(), usage()
    if aw + 2 + uw <= cols:  # lado a lado
        h = max(len(a), len(u))
        a += [" " * aw] * (h - len(a))
        u += [""] * (h - len(u))
        return [x + "  " + y for x, y in zip(a, u)]
    return a + u


def main():
    once = "--once" in sys.argv
    if not once:
        sys.stdout.write("\033[?25l\033[2J")
    try:
        while True:
            lines = frame()
            sys.stdout.write(("" if once else "\033[H") + "".join(l + "\033[K\n" for l in lines) + ("" if once else "\033[J"))
            sys.stdout.flush()
            if once:
                return
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        if not once:
            sys.stdout.write("\033[?25h")


if __name__ == "__main__":
    main()
