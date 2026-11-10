#!/usr/bin/env python3
"""bk rail: riel derecho del workspace, a lo gentle-shell (Status → Agents → Changes → radio; los checkpoints están en bk monitor y bk checkpoints).

Solo lectura; se redibuja cada 2 s. El detalle está en los overlays: alt+a agentes, alt+g cambios,
alt+k comandos, alt+s sesiones.
"""
import glob, json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import gentle as G
import agents as AG   # título del issue/PR y "¿te pregunta algo?" (lo mismo que bk ls)
from gentle import (AMBER, BOLD, FRAME, GREEN, IRIS, MUTED, PETAL, RED, ROSE, RST, TEXT,
                    card, cut, fit, lr, vlen)

HERE = os.path.dirname(os.path.realpath(__file__))
SESSION = os.environ.get("BK_SESSION", "agents")
STATE = os.environ.get("BK_STATE_DIR", os.path.expanduser("~/.cache/bk/agents"))
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
        s["_sid"] = os.path.basename(f)[:-5]   # el hook nombra el archivo <session_id>.json
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


def state_row(state):
    """(glifo, texto, color ANSI) de un estado, los de gentle.STATES (la misma tabla que la tira y el monitor)."""
    return G.state_ansi(state)


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
_account = {"t": 0, "v": ""}


def session_account():
    """La cuenta de esta sesión (bk account use): "" si es la default en los dos proveedores."""
    if time.time() - _account["t"] > 5:
        pane = os.environ.get("TMUX_PANE", "")
        got = []
        for prov in ("claude", "codex"):
            v = sh("tmux", "show-options", "-qv", *(["-t", pane] if pane else []), f"@bk_account_{prov}").strip()
            if v and v != "default":
                got.append(v if prov == "claude" else f"codex {v}")
        _account["v"], _account["t"] = " · ".join(got), time.time()
    return _account["v"]


def wait_kind(s):
    """question = te hizo una pregunta (AskUserQuestion) · permission = pide permiso."""
    return s.get("wait") or ("question" if s.get("tool") == "AskUserQuestion" else "permission")


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
            f" {MUTED}Profile{RST} {TEXT}{_profile['v']}{RST}"]
    acct = session_account()
    if acct:
        rows.append(f" {MUTED}Cuenta{RST}  {ROSE}{acct}{RST}")
    rows.append("")
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


def sub_rows(sid, inner_w, indent="  "):
    """Filas de los subagentes vivos de una sesión (los de la herramienta Agent, que no son ventanas)."""
    from sidebar import subagents
    rows = []
    for desc, bg, _ in subagents(sid):
        g = HALF[(TICK[0] // 2) % len(HALF)] if bg else SPIN[TICK[0] % len(SPIN)]
        tag = f"{AMBER}2º plano{RST}" if bg else f"{IRIS}trabajando{RST}"
        rows.append(lr(f"{indent}{MUTED}↳{RST} {IRIS}{g}{RST} {TEXT}{desc}{RST}", tag, inner_w))
    return rows


def chat_sid(by_pane):
    """Sesión de Claude del chat de este workspace (el pane de la ventana del riel que no es tira ni riel)."""
    me = os.environ.get("TMUX_PANE", "")
    if not me:
        return None
    for l in sh("tmux", "list-panes", "-t", me, "-F", "#{pane_id}\t#{pane_start_command}").splitlines():
        pid, start = (l.split("\t") + [""])[:2]
        if pid != me and "bk sidebar" not in start and "bk rail" not in start:
            return (by_pane.get(pid) or {}).get("_sid")
    return None


def card_agents(W, live, ended, here_sid=None):
    here = sub_rows(here_sid, inner(W), indent="") if here_sid else []
    if not live and not ended and not here:
        return card("Agents", [f"{MUTED}nadie trabajando · {PETAL}alt+k{MUTED} para lanzar{RST}"], W, glyph="∗")
    rows = []
    inner_w = inner(W)
    titles = AG.load_titles()
    tks = AG.task_by_window()   # tareas (ADR-005): cómo cerró y qué quedó en la cola de bk say

    def asks(a):
        s = a["s"]
        if s.get("state") == "waiting":
            return True
        if s.get("state") in ("done", "idle"):
            a["_q"] = AG.last_question(a.get("pane"))
            return bool(a["_q"])
        return False

    live = sorted(live, key=lambda a: not asks(a))   # los que te esperan, primero
    for a in live:
        s = a["s"]
        g, txt, col = state_row(s.get("state"))
        if s.get("state") == "waiting":
            g, txt, col = state_row("question" if wait_kind(s) == "question" else "permission")
        if s.get("tool") == "confianza":
            g, txt, col = state_row("trust")
        if s.get("state") == "background" and s.get("background"):
            txt = f"{s['background']} en 2º plano"
        if a.get("_q"):
            g, txt, col = state_row("question")
        idle = time.time() - s.get("ts", time.time())
        if s.get("state") == "working" and s.get("tool") != "confianza":
            if idle > STALE:
                g, _, col = state_row("stale")
                txt = f"¿trabado? {int(idle // 60)}m quieto"
            else:
                g = SPIN[TICK[0] % len(SPIN)]
        elif s.get("state") == "background":
            g = HALF[(TICK[0] // 2) % len(HALF)]
        left = f"{col}{g}{RST} {TEXT}{BOLD}{a['win']}{RST} {MUTED}{s.get('role') or ''}{RST}"
        right = f"{PETAL}{model(s.get('model'))}{RST}  {col}{txt}{RST}  {MUTED}{ago(s.get('ts', 0)):>5}{RST}"
        rows.append(lr(left, right, inner_w))
        # qué es: "#218 (PR #240) Agregar las reglas…" (de la cache de bk ls; acá no se consulta GitHub)
        m = AG.WIN_RE.match(a["win"])
        num = (m.group(2) if m else "") or (s.get("ref") or "").lstrip("#")
        t = AG.title_for(s.get("cwd"), num, titles, fetch=False) if num else ""
        if t:
            pr = AG.pr_of_branch(s.get("cwd"), titles, fetch=False) if m and m.group(1) == "i" else ""
            rows.append(f"  {MUTED}{cut(f'#{num}' + (f' (PR #{pr})' if pr else '') + ' ' + t, inner_w - 2)}{RST}")
        if a.get("_q"):
            rows.append(f"  {AMBER}«{cut(a['_q'], inner_w - 4)}»{RST}")
        if s.get("state") == "working" and s.get("tool") not in (None, "-", "confianza"):
            rows.append(f"  {MUTED}↳ {s['tool']}{RST}")
        q = AG.queued(tks[a["win"]]) if a["win"] in tks else 0
        if q:
            rows.append(f"  {PETAL}✉ {q} mensaje{'s' if q != 1 else ''} en cola{RST} {MUTED}· al terminar el turno{RST}")
        rows += sub_rows(s.get("_sid"), inner_w)
    for a in ended:
        g, txt = AG.task_note(tks.get(a["win"])) or ("✓", "terminó")
        col = AMBER if g == "⚠" else RED if g == "✕" else MUTED
        rows.append(f"{col}{g}{RST} {MUTED}{a['win']} · {cut(txt, inner_w - len(a['win']) - 5)}{RST}")
    if here:
        rows = [f"{MUTED}este chat:{RST}"] + here + rows
    active = sum(1 for a in live if a["s"].get("state") in ("working", "background", "waiting")) + len(here)
    busy = bool(here) or any(a["s"].get("state") in ("working", "background") for a in live)
    waiting = any(a["s"].get("state") == "waiting" or a.get("_q") for a in live)
    color = RED if waiting else IRIS if busy else FRAME
    title = f"Agents {MUTED}· {active} active · {len(ended)} finished"
    return card(title, rows, W, color=color, accent=IRIS if busy else ROSE, glyph="∗")


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
PET_COL = {"P": "C9CED8", "S": "C9CED8", "B": "E9ECF1", "V": "14161A", "E": "8FB0FF", "R": "B4122B", "X": "F07178",
           "Q": "E6B450",   # Q: ojos de pregunta (amarillos)
           "L": "6E7FB0"}   # L: párpados (dormido)
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


def alive_states(all_states):
    """Estado de cada sesión de Claude que sigue viva en tmux: el chat de este workspace (yo incluido)
    y los agentes de cualquier proyecto. Las que quedaron de ventanas cerradas no cuentan."""
    panes = set(sh("tmux", "list-panes", "-a", "-F", "#{pane_id}").split())
    return [("question" if wait_kind(s) == "question" else "waiting") if s.get("state") == "waiting" else s.get("state")
            for s in all_states if s.get("pane") in panes]


def mascot(W, states):
    """Barrilete según lo que hacen las sesiones: vuela, espera o duerme. Anima con TICK."""
    t = TICK[0]
    if "question" in states:
        mood, note = "ask", "te hizo una pregunta"
    elif "waiting" in states:
        mood, note = "wait", "pide permiso"
    elif "working" in states:
        mood, note = "fly", "volando"
    elif "background" in states:
        mood, note = "slow", "en segundo plano"
    else:
        mood, note = "sleep", "durmiendo"
    # Hélices: al girar se ven más cortas (7 → 5 → 1 → 5 píxeles)
    spin = [7, 5, 1, 5]
    w = spin[t % 4] if mood in ("fly", "wait", "ask") else spin[(t // 3) % 4] if mood == "slow" else 7
    if mood == "sleep":
        a, b = "V", "V"                       # ojos cerrados: los dibujan los párpados (abajo)
    elif mood == "wait":
        a = b = "X"                           # ojos rojos: pide permiso
    elif mood == "ask":
        a, b = ("Q", "Q") if (t // 2) % 2 else ("Q", "V")   # ojos amarillos que titilan: te pregunta
    elif t % 24 == 0:
        a = b = "V"                           # parpadeo
    else:
        a = b = "E"
    rows = [_props(w)] + [r.replace("{a}", a).replace("{b}", b) for r in PET_BODY]
    if mood == "sleep":
        # Dormido: párpados, una rayita de 2 píxeles por ojo en la mitad de abajo
        rows[6] = "....BVLLVVLLVB...."
    art = _pixels(rows)
    zz = ["  z", " z ", "z  "][(t // 6) % 3] if mood == "sleep" else "   "
    art[0] = art[0] + f"{MUTED}{zz}{RST}"
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
    here_sid = memo(("chat_sid",), 2, lambda: chat_sid(by_pane))
    out += card_agents(W, live, ended, here_sid)
    radio = memo(("radio", W), 5, lambda: line_radio(W))
    room = H - len(out) - len(radio) - 2
    # Lugar para Barrilete (8 filas): Changes puede usar el resto si le quedan al menos 4
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
    pet = mascot(W, memo(("alive",), 1, lambda: alive_states(all_))) if H - len(out) - len(radio) - 1 >= 9 else []
    out += [""] * max(0, H - len(out) - len(pet) - len(radio) - 1)
    out += pet
    out += radio
    out.append(fit(f"{PETAL}alt+a{RST} {MUTED}agentes{RST}  {PETAL}alt+g{RST} {MUTED}cambios{RST}  "
                   f"{PETAL}alt+k{RST} {MUTED}comandos{RST}  {PETAL}alt+s{RST} {MUTED}sesiones{RST}  "
                   f"{PETAL}alt+x{RST} {MUTED}terminal{RST}", W))
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
