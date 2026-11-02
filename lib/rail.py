#!/usr/bin/env python3
"""bk rail: riel derecho del workspace, a lo gentle-shell (Status → Agents → Checkpoints → Changes → radio).

Solo lectura; se redibuja cada 2 s. El detalle está en los overlays: alt+a agentes, alt+g cambios,
alt+k comandos, alt+s sesiones.
"""
import glob, json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import gentle as G
from gentle import (AMBER, BOLD, FRAME, GREEN, IRIS, MUTED, PETAL, RED, ROSE, RST, TEXT,
                    card, cut, fit, lr, vlen)

HERE = os.path.dirname(os.path.realpath(__file__))
SESSION = os.environ.get("BK_SESSION", "agents")
STATE = os.environ.get("BK_STATE_DIR", os.path.expanduser("~/.cache/bk/agents"))
PRS = os.path.expanduser("~/.cache/bk/checkpoints_prs.json")
LESSONS = os.path.join(os.environ.get("BARRILETE_VAULT", os.path.expanduser("~/Documents/Obsidian Vault")),
                       "agents/lessons")
SHELLS = {"zsh", "bash", "sh", "fish", "-zsh", "login"}

_usage = {"t": 0, "data": None}
PROJECT = os.environ.get("BK_PROJECT", "")


def in_project(path):
    """¿Esta carpeta es del proyecto del workspace (el repo o sus worktrees de prepare.sh)?"""
    if not PROJECT:
        return True
    p = (path or "").rstrip("/")
    return p == PROJECT or p.startswith(PROJECT + "/") or p.startswith(PROJECT + "-wt/")


def sh(*a, cwd=None):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=4, cwd=cwd).stdout
    except Exception:
        return ""


def model(m):
    m = (m or "").replace("claude-", "")
    for fam in ("opus", "sonnet", "haiku", "fable"):
        if m.startswith(fam):
            return (fam + " " + m[len(fam):].strip("-").replace("-", ".")).strip()
    return m or "—"


def human(n):
    for u, d in (("M", 1e6), ("k", 1e3)):
        if n >= d:
            return f"{n / d:.1f}{u}"
    return str(int(n))


def ago(ts):
    m = int((time.time() - ts) // 60) if ts else 0
    return f"{m}m" if m < 60 else f"{m // 60}h{m % 60:02d}"


def usage():
    if time.time() - _usage["t"] > 30:
        try:
            _usage["data"] = json.loads(sh(sys.executable, os.path.join(HERE, "usage.py"), "--json"))
        except Exception:
            pass
        _usage["t"] = time.time()
    return _usage["data"] or {}


def states():
    by_pane, all_ = {}, []
    for f in glob.glob(os.path.join(STATE, "*.json")):
        try:
            s = json.load(open(f))
        except Exception:
            continue
        all_.append(s)
        p = s.get("pane")
        if p and (p not in by_pane or s.get("ts", 0) > by_pane[p].get("ts", 0)):
            by_pane[p] = s
    return by_pane, all_


def agents(by_pane):
    live, ended = [], []
    for l in sh("tmux", "list-panes", "-s", "-t", SESSION, "-F",
                "#{window_name}\t#{pane_id}\t#{pane_dead}\t#{pane_current_command}\t#{pane_current_path}\t#{@bk_ws}").splitlines():
        p = l.split("\t")
        if len(p) != 6 or p[5]:      # p[5]: el chat de un workspace, de visita (alt+shift+N)
            continue
        a = {"win": p[0], "pane": p[1], "s": by_pane.get(p[1], {})}
        if not in_project(a["s"].get("cwd") or p[4]):
            continue
        (ended if p[2] == "1" or p[3] in SHELLS else live).append(a)
    return live, ended


STATE_UI = {  # estado -> (glifo, texto, color)
    "working": ("●", "working", IRIS), "background": ("◐", "2º plano", AMBER),
    "waiting": ("●", "te espera", RED), "done": ("✓", "listo", GREEN), "idle": ("·", "libre", MUTED),
    "error": ("✗", "error", RED),
}


def inner(W):
    return W - 5 if G.ui()["cards"] == "float" else W - 4


def roles_profile(top):
    """Perfil de modelos: rol → modelo, de roles.yml (o el del proyecto)."""
    out = sh(os.path.join(os.path.dirname(HERE), "bin", "bk"), "roles", cwd=top or None)
    pairs = []
    for l in out.splitlines():
        p = l.split()
        if len(p) >= 2 and "/" in p[1]:
            pairs.append((p[0], p[1].split("/")[-1]))
    short = {"implementer": "impl", "implementer-light": "light", "reviewer": "review",
             "planner": "plan", "orchestrator": "orq"}
    return " · ".join(f"{short.get(r, r)} {m}" for r, m in pairs) or "—"


_profile = {"t": 0, "v": "—"}


def card_status(W, cwd, live):
    top = sh("git", "-C", cwd, "rev-parse", "--show-toplevel").strip()
    path = (PROJECT or top or cwd).replace(os.path.expanduser("~"), "~")
    br = sh("git", "-C", cwd, "branch", "--show-current").strip()
    dirty = len([l for l in sh("git", "-C", cwd, "status", "--porcelain").splitlines() if l.strip()])
    if time.time() - _profile["t"] > 60:
        _profile["v"], _profile["t"] = roles_profile(PROJECT or top), time.time()
    u = usage()
    n = inner(W)
    rows = [f"{MUTED}Project{RST}", f" {TEXT}{path}{RST}",
            f" {MUTED}Branch{RST}  {TEXT}{BOLD}{br or '—'}{RST}" + (f" {AMBER}±{dirty}{RST}" if dirty else ""),
            f" {MUTED}Profile{RST} {TEXT}{_profile['v']}{RST}", ""]
    if PROJECT:
        name = os.path.basename(PROJECT)
        pt = sum(v["tokens"] for k, v in u.get("by_project", {}).items() if k == name or k.startswith(name + "#"))
        pc = sum(v["cost"] for k, v in u.get("by_project", {}).items() if k == name or k.startswith(name + "#"))
        rows += [f"{MUTED}Usage{RST}", f" {MUTED}hoy acá{RST} {TEXT}{human(pt)}{RST} {MUTED}·{RST} {AMBER}${pc:.2f}{RST}"
                 f"  {MUTED}total{RST} {TEXT}${u.get('cost_usd', 0):.2f}{RST}"]
    else:
        rows += [f"{MUTED}Usage{RST}", f" {MUTED}hoy{RST} {TEXT}{human(u.get('tokens', 0))}{RST} {MUTED}tok ·{RST} "
                 f"{AMBER}${u.get('cost_usd', 0):.2f}{RST} {MUTED}≈API{RST}"]
    rows += ["", f"{MUTED}Integrations{RST}",
             f" {ROSE}✿{RST} {TEXT}claude code{RST} {MUTED}· ready{RST}   {PETAL}{RST}{TEXT}gh{RST} {MUTED}· ready{RST}"]
    working = sum(1 for a in live if a["s"].get("state") in ("working", "background", "waiting"))
    right = f"{len(live)} agente{'s' if len(live) != 1 else ''}" + (f" · {working} activo{'s' if working != 1 else ''}" if working else "")
    return card("Status", [fit(r, n) for r in rows], W, right=right)


SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"      # trabajando: gira mientras el agente vive
HALF = "◐◓◑◒"              # en segundo plano: gira más lento
STALE = 300                # "working" sin ningún evento del hook en 5 min: ¿trabado?
TICK = [0]
_memo = {}


def memo(key, ttl, fn):
    """Lo caro (git, gh, usage) se recalcula cada `ttl` s; el spinner se dibuja 4 veces por segundo."""
    v = _memo.get(key)
    if v and time.time() - v[0] < ttl:
        return v[1]
    out = fn()
    _memo[key] = (time.time(), out)
    return out


def card_agents(W, live, ended):
    if not live and not ended:
        return card("Agents", [f"{MUTED}nadie trabajando · {PETAL}alt+k{MUTED} para lanzar{RST}"], W, glyph="∗")
    rows = []
    inner_w = inner(W)
    for a in live:
        s = a["s"]
        g, txt, col = STATE_UI.get(s.get("state"), ("·", s.get("state") or "—", MUTED))
        if s.get("tool") == "confianza":
            g, txt, col = "●", "confiá carpeta", RED
        if s.get("state") == "background" and s.get("background"):
            txt = f"{s['background']} en 2º plano"
        idle = time.time() - s.get("ts", time.time())
        if s.get("state") == "working" and s.get("tool") != "confianza":
            if idle > STALE:
                g, col, txt = "⚠", AMBER, f"¿trabado? {int(idle // 60)}m quieto"
            else:
                g = SPIN[TICK[0] % len(SPIN)]
        elif s.get("state") == "background":
            g = HALF[(TICK[0] // 2) % len(HALF)]
        left = f"{col}{g}{RST} {TEXT}{BOLD}{a['win']}{RST} {MUTED}{s.get('role') or ''}{RST}"
        right = f"{PETAL}{model(s.get('model'))}{RST}  {col}{txt}{RST}  {MUTED}{ago(s.get('ts', 0)):>5}{RST}"
        rows.append(lr(left, right, inner_w))
        if s.get("state") == "working" and s.get("tool") not in (None, "-", "confianza"):
            rows.append(f"  {MUTED}↳ {s['tool']}{RST}")
    for a in ended:
        rows.append(f"{MUTED}✓ {a['win']} · terminó{RST}")
    active = sum(1 for a in live if a["s"].get("state") in ("working", "background", "waiting"))
    busy = any(a["s"].get("state") in ("working", "background") for a in live)
    waiting = any(a["s"].get("state") == "waiting" for a in live)
    color = RED if waiting else IRIS if busy else FRAME
    title = f"Agents {MUTED}· {active} active · {len(ended)} finished"
    return card(title, rows, W, color=color, accent=IRIS if busy else ROSE, glyph="∗")


def card_checkpoints(W, all_states):
    rows = []
    for s in all_states:
        if not in_project(s.get("cwd")):
            continue
        if s.get("phase") == "plan_pending" and s.get("state") in ("done", "idle"):
            rows.append(f"{RED}●{RST} plan por aprobar {MUTED}· {os.path.basename(s.get('cwd', ''))}{RST}")
    # Refrescar en segundo plano cada ~2 min (bk _prs consulta GitHub con gh)
    try:
        age = time.time() - os.path.getmtime(PRS)
    except OSError:
        age = 1e9
    if age > 120:
        subprocess.Popen([os.path.join(os.path.dirname(HERE), "bin", "bk"), "_prs"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        prs = json.load(open(PRS))
    except Exception:
        prs = []
    others = 0
    if PROJECT:
        url = sh("git", "-C", PROJECT, "remote", "get-url", "origin").strip()
        repo = url.rstrip("/").removesuffix(".git").split("/")[-1].split(":")[-1] if url else os.path.basename(PROJECT)
        mine = [p for p in prs if p.get("repo", "").split("/")[-1] == repo]
        others, prs = len(prs) - len(mine), mine
    for p in prs:
        if p.get("kind", "merge") == "merge":
            rows.append(f"{GREEN}●{RST} mergear {TEXT}{p['repo'].split('/')[-1]}#{p['number']}{RST} {MUTED}{p['title']}{RST}")
        else:
            rows.append(f"{AMBER}●{RST} review {TEXT}{p['repo'].split('/')[-1]}#{p['number']}{RST} {MUTED}{p['title']}{RST}")
    for f in glob.glob(os.path.join(LESSONS, "*.md")):
        try:
            head = open(f, encoding="utf-8").read(600)
        except OSError:
            continue
        if "status: proposed" in head:
            title = next((l[2:] for l in head.splitlines() if l.startswith("# ")), os.path.basename(f))
            rows.append(f"{AMBER}󰌵{RST} lección {MUTED}{title}{RST}")
    n = len(rows)
    if others:
        rows.append(f"{MUTED}+{others} en otros proyectos{RST}")
    if not rows:
        return []
    return card(f"Checkpoints {MUTED}· {n} tuyos", rows, W, color=FRAME, glyph="◆")


def card_changes(W, roots, max_rows):
    rows, nfiles = [], 0
    for root in roots:
        top = sh("git", "-C", root, "rev-parse", "--show-toplevel").strip()
        if not top:
            continue
        st = [l for l in sh("git", "-C", top, "status", "--porcelain").splitlines() if l.strip()]
        if not st:
            continue
        num = {}
        for l in sh("git", "-C", top, "diff", "--numstat", "HEAD").splitlines():
            p = l.split("\t")
            if len(p) == 3:
                num[p[2]] = (p[0], p[1])
        br = sh("git", "-C", top, "branch", "--show-current").strip()
        name = os.path.basename(top)
        rows.append(f"{PETAL}▾{RST} {TEXT}{name}{RST} {MUTED}{br}{RST}")
        for l in st:
            code, path = l[:2].strip() or "M", l[3:]
            a, d = num.get(path, ("", ""))
            col = GREEN if code in ("A", "??") else RED if code == "D" else AMBER
            stat = (f"{GREEN}+{a}{RST} {RED}-{d}{RST}" if a and a != "-" else "")
            rows.append(lr(f"  {col}{'A' if code == '??' else code}{RST} {TEXT}{path}{RST}", stat, inner(W)))
            nfiles += 1
    if not rows:
        return card("Changes", [f"{MUTED}sin cambios sin commitear{RST}"], W, glyph="✎")
    if len(rows) > max_rows:
        rows = rows[: max_rows - 1] + [f"{MUTED}… {PETAL}alt+g{MUTED} para ver todo{RST}"]
    return card(f"Changes {MUTED}· {nfiles} archivos", rows, W, glyph="✎")


def line_radio(W):
    song = os.path.join(HERE, "song.sh")
    if not os.access(song, os.X_OK):
        return []
    parts = sh(song, "bar-status").rstrip("\n").split("\t")
    if not parts or parts[0] not in ("playing", "paused"):
        return []
    state, station, title = (parts + ["", "", ""])[:3]
    icon = "♫" if state == "playing" else "⏸"
    return [cut(f" {ROSE}{icon}{RST} {TEXT}{station}{RST} {MUTED}{title}{RST}", W)]



# ---------- Barrilete: el dron con la rienda, la mascota del kit ----------
BLUE = G.fg("8FB0FF")       # ojos, como en el logo
RIENDA = G.fg("B4122B")     # la rienda roja


# Pixel art con medios bloques (▀ = píxel de arriba con fg, de abajo con bg), copiado del logo:
# hélices, cuerpo claro, visor oscuro con ojos azules y la rienda roja.
PET_COL = {"P": "C9CED8", "S": "C9CED8", "B": "E9ECF1", "V": "14161A", "E": "8FB0FF", "R": "B4122B", "X": "F07178"}
PET_BODY = [
    "...S..........S...",
    "...S.BBBBBBBB.S...",
    "....BBBBBBBBBB....",
    "....BVVVVVVVVB....",
    "....BVV{a}VV{a}VVB....",
    "....BVV{b}VV{b}VVB....",
    "....BVVVVVVVVB....",
    "....BBBBBBBBBB....",
    ".....BBBBBBBB.....",
    "........RR........",
    "..................",
]


def _props(w):
    row = ["."] * 18
    for c in (3, 14):
        for x in range(c - w // 2, c + w // 2 + 1):
            row[x] = "P"
    return "".join(row)


def _pixels(rows):
    out = []
    for y in range(0, len(rows), 2):
        top, bot = rows[y], rows[y + 1]
        line = ""
        for a, b in zip(top, bot):
            if a == "." and b == ".":
                line += RST + " "
            elif a == ".":
                line += RST + G.fg(PET_COL[b]) + "▄"
            elif b == ".":
                line += RST + G.fg(PET_COL[a]) + "▀"
            else:
                line += G.fg(PET_COL[a]) + G.bg(PET_COL[b]) + "▀"
        out.append(line + RST)
    return out


def mascot(W, live):
    """Barrilete según lo que hacen los agentes: vuela, espera o duerme. Anima con TICK."""
    t = TICK[0]
    states = [a["s"].get("state") for a in live]
    if "waiting" in states:
        mood, note = "wait", "te espera algo"
    elif "working" in states:
        mood, note = "fly", "volando"
    elif "background" in states:
        mood, note = "slow", "en segundo plano"
    else:
        mood, note = "sleep", "durmiendo"
    # Hélices: al girar se ven más cortas (7 → 5 → 1 → 5 píxeles)
    spin = [7, 5, 1, 5]
    w = spin[t % 4] if mood in ("fly", "wait") else spin[(t // 3) % 4] if mood == "slow" else 7
    if mood == "sleep":
        a, b = "V", "E"                       # ojos cerrados: solo la mitad de abajo
    elif mood == "wait":
        a = b = "X"                           # ojos rojos
    elif t % 24 == 0:
        a = b = "V"                           # parpadeo
    else:
        a = b = "E"
    rows = [_props(w)] + [r.replace("{a}", a).replace("{b}", b) for r in PET_BODY]
    art = _pixels(rows)
    sway = "╰─╮" if (t // 6) % 2 else "╭─╯"   # la correa se mueve
    zz = ["  z", " z ", "z  "][(t // 6) % 3] if mood == "sleep" else "   "
    art[0] = art[0] + f"{MUTED}{zz}{RST}"
    art.append(" " * 8 + f"{RIENDA}{sway}{RST}")
    bob = (t // 4) % 2 if mood in ("fly", "slow") else 0  # sube y baja
    pad = " " * max(0, (W - 18) // 2)
    lines = [""] * bob + [pad + l for l in art] + [""] * (1 - bob)
    label = f"{MUTED}✿ barrilete · {note}{RST}"
    lines.append(" " * max(0, (W - vlen(label)) // 2) + label)
    return lines


def frame(W, H, cwd):
    by_pane, all_ = states()
    live, ended = agents(by_pane)
    out = []
    out += memo(("status", W), 2, lambda: card_status(W, cwd, live))
    out += card_agents(W, live, ended)
    out += memo(("ckpt", W), 2, lambda: card_checkpoints(W, all_))
    radio = memo(("radio", W), 5, lambda: line_radio(W))
    room = H - len(out) - len(radio) - 2
    # Lugar para Barrilete (9 filas): Changes puede usar el resto si le quedan al menos 4
    if room - 9 >= 4:
        room -= 9
    def changes():
        roots = [cwd] + [a["s"].get("cwd") for a in live if a["s"].get("cwd")]
        seen, uniq = set(), []
        for r in roots:
            top = sh("git", "-C", r, "rev-parse", "--show-toplevel").strip() if r else ""
            if top and top not in seen:
                seen.add(top); uniq.append(top)
        return card_changes(W, uniq, room - 2)
    if room >= 3:
        out += memo(("changes", W, room), 2, changes)
    # Barrilete abajo, si queda lugar (necesita 8 filas)
    pet = mascot(W, live) if H - len(out) - len(radio) - 1 >= 9 else []
    out += [""] * max(0, H - len(out) - len(pet) - len(radio) - 1)
    out += pet
    out += radio
    out.append(fit(f"{PETAL}alt+a{RST} {MUTED}agentes{RST}  {PETAL}alt+g{RST} {MUTED}cambios{RST}  "
                   f"{PETAL}alt+k{RST} {MUTED}comandos{RST}  {PETAL}alt+s{RST} {MUTED}sesiones{RST}", W))
    return out[:H]


def main():
    cwd = os.getcwd()
    sys.stdout.write("\033[?25l\033[2J")
    try:
        while True:
            try:
                sz = os.get_terminal_size(sys.stdout.fileno())
                W, H = sz.columns, sz.lines
            except OSError:
                W, H = 50, 40
            lines = (frame(W, H, cwd) + [""] * H)[:H]
            sys.stdout.write("\033[H" + "\033[K\n".join(cut(l, W) for l in lines) + "\033[K")
            sys.stdout.flush()
            TICK[0] += 1
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h")


if __name__ == "__main__":
    main()
