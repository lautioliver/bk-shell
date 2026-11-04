#!/usr/bin/env python3
"""bk monitor: pantalla para dejar abierta en otro monitor y vigilar a todos los agentes.

  Agentes   los de bk y los chats: título, estado, herramienta, minutos, costo de la sesión y sus subagentes
  Desvíos   en vivo, apenas pasan: los que detecta `bk learn` (hooks, barandas, red.sh/verify.py, correcciones,
            interrupciones, permisos rechazados, bloqueantes del contrato) y otros propios del monitor:
            agente trabado, el mismo error repetido, force push, otra credencial, "CI en verde" con el CI rojo,
            y atribuirte una decisión. Los graves (⚠) suenan y notifican una vez.
  Hoy       costo del día por modelo y desvíos de hoy contra el promedio de la semana

Teclas: ↑↓ elegir agente · enter ver su pantalla · tab pasar a los desvíos · d detalle · s sonido on/off · q salir
Solo lee: transcripts de Claude Code, el estado del hook y tmux. Los desvíos propios quedan en
~/.cache/bk/monitor-events.jsonl (para reabrir el monitor sin perderlos).
"""
import glob
import json
import os
import re
import select
import shutil
import subprocess
import sys
import termios
import time
import tty

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import agents as AG  # noqa: E402
import gentle as G   # noqa: E402
from gentle import AMBER, BOLD, GREEN, IRIS, MUTED, PETAL, RED, ROSE, RST, TEXT  # noqa: E402

try:
    import deviations as L   # los mismos detectores que usa bk learn para el vault
except Exception:       # sin vault configurado, el monitor sigue con sus detectores propios
    L = None
try:
    from usage import cost as usage_cost
except Exception:
    usage_cost = None

CACHE = os.environ.get("BK_CACHE_DIR", os.path.expanduser("~/.cache/bk"))
LOG = os.path.join(CACHE, "monitor-events.jsonl")
SOUND_OFF = os.path.join(os.environ.get("BK_CONFIG_DIR", os.path.expanduser("~/.config/bk")), "sound")
VAULT_DATA = os.path.join(os.path.expanduser(os.environ.get("BARRILETE_VAULT", "~/Documents/Obsidian Vault")), "agents", "data")
TO_VAULT = {"loop", "stale_grave", "ci_claim"}   # los que bk learn no puede ver en los transcripts: el monitor los anota
STALE_WARN, STALE_GRAVE = 600, 1200      # segundos "trabajando" sin eventos del hook
LOOP_N, LOOP_WINDOW = 4, 900             # el mismo comando con el mismo error 4 veces en 15 min

GRAVE_TYPES = {"force_push", "credential", "ci_claim", "loop", "stale_grave"}
TYPE_UI = {
    "hook_block": ("bloqueado por un hook", AMBER), "guardrail_block": ("bloqueado por una baranda", AMBER),
    "verify_fail": ("verificación falló", AMBER), "user_denied": ("rechazaste un permiso", PETAL),
    "user_correction": ("lo corregiste", PETAL), "plan_correction": ("corregiste el plan", PETAL),
    "interrupt": ("lo interrumpiste", PETAL), "contract_blockers": ("review con bloqueantes", AMBER),
    "contract_escalate": ("escaló", IRIS), "contract_question": ("dejó una pregunta", IRIS),
    "contract_dropped": ("abandonó el issue", RED), "tool_error": ("error de herramienta", MUTED),
    "stale": ("quieto sin eventos", AMBER), "stale_grave": ("¿trabado?", RED),
    "loop": ("el mismo error se repite", RED), "force_push": ("intentó un force push", RED),
    "credential": ("intentó usar otra credencial", RED), "ci_claim": ("dijo «CI en verde» y está rojo", RED),
    "attribution": ("te atribuye una decisión: verificá", AMBER),
}

# Los patrones de riesgo son los de lib/deviations.py (los mismos que guarda bk learn)
RE_FORCE, RE_CRED, RE_ATTR = (getattr(L, "RE_FORCE", None), getattr(L, "RE_CRED", None), getattr(L, "RE_ATTR", None))
RE_CI = re.compile(r"\b(CI|checks?)\b[^.\n]{0,40}\b(en verde|verde|green|pass(?:es|ed)?|pasa(?:n|ron)?)\b", re.I)


def now():
    return time.time()


def epoch(ts):
    """'2026-11-04T03:09:08.838Z' → segundos (UTC); 0 si no se entiende."""
    try:
        import calendar
        return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0


def day_start():
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def hhmm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def sound_on():
    try:
        return open(SOUND_OFF).read().strip() != "off"
    except OSError:
        return True


def alert(title, body):
    """Sonido + notificación de macOS (una vez por desvío grave)."""
    if os.environ.get("BK_MONITOR_DRY"):
        return
    if sound_on():
        f = "/System/Library/Sounds/Basso.aiff"
        if os.path.exists(f) and shutil.which("afplay"):
            subprocess.Popen(["afplay", "-v", "0.7", f], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if shutil.which("osascript"):
        esc = lambda s: s.replace("\\", "").replace('"', "'")
        subprocess.Popen(["osascript", "-e", f'display notification "{esc(body)}" with title "{esc(title)}"'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------- seguimiento de transcripts ----------
class Tail:
    """Lee lo nuevo de un transcript: costo acumulado, última herramienta y desvíos."""

    def __init__(self, path, who, kind, pr=""):
        self.path, self.who, self.kind, self.pr = path, who, kind, pr
        self.off, self.cost, self.model, self.tool = 0, 0.0, "", ""   # cost: solo lo de hoy
        self.tools, self.plan, self.errors = {}, {}, []
        self.first = True    # la primera lectura solo suma costo: no reporta desvíos viejos

    def read(self, mon):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self.off:
            self.off = 0
        if size == self.off:
            return
        with open(self.path, "rb") as f:
            f.seek(self.off)
            data = f.read()
        end = data.rfind(b"\n") + 1
        self.off += end
        for raw in data[:end].splitlines():
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            self.line(e, mon)
        self.first = False

    def line(self, e, mon):
        t = e.get("type")
        if t not in ("user", "assistant") or e.get("isMeta"):
            return
        msg = e.get("message") or {}
        ts = e.get("timestamp", "")
        if t == "assistant":
            self.model = msg.get("model") or self.model
            u = msg.get("usage") or {}
            if usage_cost and u and self.model and self.model != "<synthetic>":
                c = usage_cost(self.model, {"in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0),
                                            "cw5": (u.get("cache_creation") or {}).get("ephemeral_5m_input_tokens",
                                                                                       u.get("cache_creation_input_tokens", 0)),
                                            "cw1h": (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0),
                                            "cr": u.get("cache_read_input_tokens", 0)})
                if epoch(e.get("timestamp", "")) >= day_start():
                    self.cost += c or 0
        content = msg.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        ets = epoch(ts)
        if self.first and ets < day_start():
            return          # lo de antes de hoy ya no es "en vivo"
        mon.when, mon.quiet = (ets or now()), self.first
        ctx = {"ts": ts, "project": self.who, "session": os.path.basename(self.path)[:8],
               "model": self.model or "-", "role": self.kind}
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if t == "assistant" and b.get("type") == "tool_use":
                inp = b.get("input") or {}
                self.tool = b.get("name", "")
                cmd = str(inp.get("command", ""))
                self.tools[b.get("id", "")] = f"{self.tool}::{cmd[:200]}"
                if self.kind == "chat":   # los chats son tuyos: ahí env -u GH_CONFIG_DIR es lo correcto
                    continue
                if RE_FORCE.search(cmd):
                    mon.add("force_push", self.who, cmd[:200])
                elif RE_CRED.search(cmd):
                    mon.add("credential", self.who, cmd[:200])
            if t == "assistant" and b.get("type") == "text" and self.kind != "chat":
                txt = b.get("text") or ""
                if RE_ATTR.search(txt):
                    m = RE_ATTR.search(txt)
                    mon.add("attribution", self.who, txt[max(0, m.start() - 80): m.end() + 80])
                if RE_CI.search(txt) and self.pr and not self.first:
                    mon.check_ci(self.who, self.pr, txt)
            if t == "user" and b.get("type") == "tool_result" and b.get("is_error"):
                tool, _, cmd = self.tools.get(b.get("tool_use_id", ""), "?").partition("::")
                body = L.text_of(b.get("content")) if L else str(b.get("content"))[:300]
                # mismo comando y misma última línea de error ("Exit code 1" solo no alcanza: en TDD es normal)
                lines = [l.strip() for l in body.strip().splitlines() if l.strip() and not l.startswith("Exit code")]
                last = re.sub(r"[0-9a-f]{7,}|/[^ ]+|\d+(\.\d+)?s\b", "…", lines[-1] if lines else "")[:80]
                sig = f"{tool} {cmd[:60]} → {last}"
                stamp = mon.when
                self.errors = [x for x in self.errors if stamp - x[0] < LOOP_WINDOW] + [(stamp, sig)]
                if sum(1 for _, s in self.errors if s == sig) == LOOP_N:
                    mon.add("loop", self.who, f"{LOOP_N} veces: {sig}")
            if L:
                try:
                    evs = (L.assistant_events if t == "assistant" else L.user_events)(b, ctx, self.tools, self.plan, self.path)
                except Exception:
                    evs = []
                for ev in evs:
                    mon.add(ev["type"], self.who, ev.get("detail", ""))


class Monitor:
    def __init__(self):
        self.tails = {}          # ruta → Tail
        self.events = []         # [{ts, type, who, detail}]
        self.seen = set()
        self.ci_cache = {}       # pr → (ts, rojo?)
        self.stale_seen = set()
        self.agents = []
        self.today = {}
        self.week_avg = 0.0
        self.last_today = 0
        self.when, self.quiet = 0, False   # hora del evento que se está leyendo; quiet: backfill sin sonar
        os.makedirs(CACHE, exist_ok=True)
        try:   # los desvíos de hoy que ya vio un monitor anterior
            day = time.strftime("%Y-%m-%d")
            for l in open(LOG):
                e = json.loads(l)
                if time.strftime("%Y-%m-%d", time.localtime(e["ts"])) == day:
                    self.events.append(e)
                    self.seen.add((e["type"], e["who"], e["detail"][:80]))
        except (OSError, ValueError, KeyError):
            pass

    def add(self, typ, who, detail):
        key = (typ, who, (detail or "")[:80])
        if key in self.seen:
            return
        self.seen.add(key)
        e = {"ts": self.when or now(), "type": typ, "who": who, "detail": re.sub(r"\s+", " ", detail or "").strip()[:300]}
        self.events.append(e)
        try:
            with open(LOG, "a") as f:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except OSError:
            pass
        self.events.sort(key=lambda x: x["ts"])
        if typ in TO_VAULT and not self.quiet and os.path.isdir(VAULT_DATA) and not os.environ.get("BK_MONITOR_DRY"):
            day = time.strftime("%Y-%m-%d", time.localtime(e["ts"]))
            row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(e["ts"])), "project": who,
                   "session": "monitor", "model": "-", "role": "-", "type": typ,
                   "sig": TYPE_UI.get(typ, (typ,))[0], "detail": e["detail"][:280], "day": day}
            try:
                with open(os.path.join(VAULT_DATA, f"events-{day[:7]}.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            except OSError:
                pass
        if typ in GRAVE_TYPES and not self.quiet:
            alert(f"⚠ {who}: {TYPE_UI.get(typ, (typ,))[0]}", e["detail"][:120])

    def check_ci(self, who, pr, txt):
        """El agente dijo que el CI está en verde: ¿lo está? (una consulta por PR cada 2 min)."""
        ts, red = self.ci_cache.get(pr, (0, None))
        if now() - ts > 120:
            env = {k: v for k, v in os.environ.items() if k not in ("GH_CONFIG_DIR", "GH_TOKEN")}
            try:
                out = subprocess.run(["gh", "pr", "checks", pr], capture_output=True, text=True, timeout=10,
                                     env=env, cwd=os.path.expanduser("~")).stdout
            except (OSError, subprocess.SubprocessError):
                out = ""
            red = bool(re.search(r"\tfail\t", out))
            self.ci_cache[pr] = (now(), red)
        if red:
            self.add("ci_claim", who, f"PR #{pr}: {txt.strip()[:160]}")

    def refresh(self):
        self.agents = AG.gather(fetch_titles=False, look=True)
        live = set()
        for a in self.agents:
            who = a.get("short") if a["kind"] == "chat" else a["win"]
            st = AG.states().get(a["pane"], {})
            sid = st.get("_sid")
            files = glob.glob(os.path.join(AG.PROJECTS, "*", f"{sid}.jsonl")) if sid else []
            subs = glob.glob(os.path.join(AG.PROJECTS, "*", sid, "subagents", "agent-*.jsonl")) if sid else []
            for f in files + subs:
                live.add(f)
                if f not in self.tails:
                    self.tails[f] = Tail(f, who if f in files else f"{who}↳", a["kind"], a.get("pr") or "")
                self.tails[f].read(self)
            main = self.tails.get(files[0]) if files else None
            a["_cost"] = sum(self.tails[f].cost for f in files + subs if f in self.tails)
            a["_tool"] = main.tool if main else ""
            self.when, self.quiet = 0, False
            # trabado: "trabajando" sin eventos del hook hace rato
            idle = now() - st.get("ts", now())
            if a["state"] == "working" and st.get("tool") != "confianza":
                if idle > STALE_GRAVE and (who, st.get("ts"), "g") not in self.stale_seen:
                    self.stale_seen.update({(who, st.get("ts"), "g"), (who, st.get("ts"))})
                    self.add("stale_grave", who, f"{int(idle // 60)} min trabajando sin eventos (herramienta: {st.get('tool') or '-'})")
                elif idle > STALE_WARN and (who, st.get("ts")) not in self.stale_seen:
                    self.stale_seen.add((who, st.get("ts")))
                    self.add("stale", who, f"{int(idle // 60)} min sin eventos (herramienta: {st.get('tool') or '-'})")
        for f in list(self.tails):
            if f not in live:
                del self.tails[f]
        if now() - self.last_today > 30:
            self.last_today = now()
            try:
                self.today = json.loads(subprocess.run([sys.executable, os.path.join(HERE, "usage.py"), "--json"],
                                                       capture_output=True, text=True, timeout=25).stdout or "{}")
            except (subprocess.SubprocessError, ValueError):
                pass
            self.week_avg = week_average()


def week_average():
    """Desvíos por día de los 7 días anteriores, según los eventos de bk learn en el vault."""
    counts = {}
    for f in glob.glob(os.path.join(VAULT_DATA, "events-*.jsonl")):
        try:
            for l in open(f):
                d = json.loads(l).get("day", "")
                counts[d] = counts.get(d, 0) + 1
        except (OSError, ValueError):
            continue
    days = [time.strftime("%Y-%m-%d", time.localtime(now() - 86400 * i)) for i in range(1, 8)]
    return sum(counts.get(d, 0) for d in days) / 7


# ---------- dibujo ----------
def money(x):
    return f"${x:,.2f}" if x >= 0.005 else "-"


def agent_rows(mon, W, sel):
    rows = []
    for i, a in enumerate(mon.agents):
        name = a.get("short") if a["kind"] == "chat" else a["win"]
        if len(name) > 12:
            name = "…" + name[-11:]
        if a["asks"]:
            mark, col, st = "?", AMBER, a["why"]
        elif a["state"] == "working":
            mark, col, st = "●", GREEN, (a.get("_tool") or "trabajando")
        elif a["state"] == "background":
            mark, col, st = "◐", IRIS, "2º plano"
        elif a["state"] == "waiting":
            mark, col, st = "!", RED, a["why"] or "te espera"
        else:
            mark, col, st = ("◇" if a["kind"] == "chat" else "○"), MUTED, {"done": "listo", "idle": "libre"}.get(a["state"], a["state"] or "-")
        role = (a["role"] or "")[:11]
        model = (a["model"] or "").replace("claude-", "")[:8]
        left = f"{col}{mark}{RST} {TEXT}{BOLD}{name:<12}{RST} {MUTED}{role:<11} {model:<8}{RST} {AG.label(a, 40)}"
        right = f"{col}{G.cut(st, 18)}{RST}  {MUTED}{a['mins']:>3}m{RST}  {PETAL}{money(a.get('_cost', 0)):>7}{RST}"
        line = G.lr(left, right, W)
        if i == sel:
            line = "\033[7m" + G.ANSI.sub("", line) + RST
        rows.append(line)
        for d, _ in a.get("subs", []):
            rows.append(f"    {MUTED}└ {G.cut(d, W - 8)}{RST}")
        if a["asks"] and a.get("question"):
            rows.append(f"    {AMBER}«{G.cut(a['question'], W - 8)}»{RST}")
    return rows or [f"{MUTED}sin agentes ni chats{RST}"]


def event_rows(mon, W, n, sel=None):
    rows = []
    evs = list(reversed(mon.events))[:n]
    for i, e in enumerate(evs):
        txt, col = TYPE_UI.get(e["type"], (e["type"], MUTED))
        grave = e["type"] in GRAVE_TYPES
        mark = f"{RED}⚠{RST}" if grave else f"{col}•{RST}"
        line = f"{MUTED}{hhmm(e['ts'])}{RST} {mark} {TEXT}{BOLD}{G.cut(e['who'], 10):<10}{RST} {col}{txt}{RST} {MUTED}{e['detail']}{RST}"
        line = G.cut(line, W)
        if sel is not None and i == sel:
            line = "\033[7m" + G.ANSI.sub("", line) + RST
        rows.append(line)
    return rows or [f"{MUTED}sin desvíos todavía hoy{RST}"]


def today_rows(mon, W):
    t = mon.today or {}
    rows = [G.lr(f"{TEXT}costo de hoy{RST}", f"{PETAL}{BOLD}{money(t.get('cost_usd', 0))}{RST}", W)]
    for m, v in sorted((t.get("by_model") or {}).items(), key=lambda kv: -kv[1].get("cost", 0)):
        rows.append(G.lr(f"  {MUTED}{m.replace('claude-', '')}{RST}", f"{money(v.get('cost', 0))}", W))
    day = time.strftime("%Y-%m-%d")
    n_today = sum(1 for e in mon.events if time.strftime("%Y-%m-%d", time.localtime(e["ts"])) == day)
    graves = sum(1 for e in mon.events if e["type"] in GRAVE_TYPES and time.strftime("%Y-%m-%d", time.localtime(e["ts"])) == day)
    rows.append("")
    rows.append(G.lr(f"{TEXT}desvíos hoy{RST}", f"{AMBER if n_today else GREEN}{n_today}{RST}", W))
    rows.append(G.lr(f"  {MUTED}graves{RST}", f"{RED if graves else MUTED}{graves}{RST}", W))
    rows.append(G.lr(f"  {MUTED}promedio de la semana{RST}", f"{MUTED}{mon.week_avg:.1f}/día{RST}", W))
    top = max(mon.agents, key=lambda a: a.get("_cost", 0), default=None)
    if top and top.get("_cost", 0) > 0:
        rows.append("")
        nm = top.get("short") if top["kind"] == "chat" else top["win"]
        rows.append(G.lr(f"{TEXT}más caro{RST} {MUTED}{G.cut(nm, 14)}{RST}", f"{PETAL}{money(top['_cost'])}{RST}", W))
    return rows


def frame(mon, cols, lines, sel, focus, esel, overlay):
    W = cols
    head = (f" {ROSE}✿{RST} {BOLD}bk monitor{RST} {MUTED}· {time.strftime('%H:%M:%S')} · "
            f"{sum(1 for a in mon.agents if a['kind'] != 'chat')} agentes · {money((mon.today or {}).get('cost_usd', 0))} hoy · "
            f"sonido {'on' if sound_on() else 'off'}{RST}")
    out = [G.cut(head, W), ""]
    if overlay:
        out += G.card(overlay[0], [G.cut(l, W - 6) for l in overlay[1]][-(lines - 6):], W, glyph="▸")
        return out[:lines - 1] + [f" {MUTED}esc vuelve{RST}"]
    out += G.card("Agentes", agent_rows(mon, W - 6, sel if focus == "agents" else -1), W, glyph="∗")
    left_w = max(40, int(W * 0.66)) if W >= 100 else W
    room = max(4, lines - len(out) - 6)
    devs = G.card("Desvíos en vivo", event_rows(mon, left_w - 6, room, esel if focus == "events" else None),
                  left_w, glyph="⚠")
    if W >= 100:
        today = G.card("Hoy", today_rows(mon, W - left_w - 7), W - left_w - 1, glyph="◆")
        for i in range(max(len(devs), len(today))):
            l = devs[i] if i < len(devs) else " " * left_w
            r = today[i] if i < len(today) else ""
            out.append(G.fit(l, left_w) + " " + r)
    else:
        out += devs + G.card("Hoy", today_rows(mon, W - 6), W, glyph="◆")
    keys = f" {MUTED}↑↓ agente · enter su pantalla · tab desvíos · d detalle · s sonido · q salir{RST}"
    return out[:lines - 1] + [keys]


def main():
    if "--once" in sys.argv:   # un cuadro y salir (para probar)
        mon = Monitor()
        mon.refresh()
        cols, lines = shutil.get_terminal_size((140, 45))
        print("\n".join(frame(mon, cols, lines, 0, "agents", 0, None)))
        return
    if not sys.stdin.isatty():
        sys.exit("bk monitor necesita una terminal")
    mon = Monitor()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    sys.stdout.write("\033[?1049h\033[?25l")
    sel, focus, esel, overlay, last = 0, "agents", 0, None, 0
    try:
        while True:
            if now() - last > 2:
                mon.refresh()
                last = now()
                if overlay and overlay[2]:   # la pantalla del agente, en vivo
                    overlay = (overlay[0], peek(overlay[2]), overlay[2])
            cols, lines = shutil.get_terminal_size((140, 45))
            sel = min(sel, max(0, len(mon.agents) - 1))
            sys.stdout.write("\033[H\033[2J" + "\n".join(frame(mon, cols, lines, sel, focus, esel, overlay)))
            sys.stdout.flush()
            if not select.select([sys.stdin], [], [], 1.0)[0]:
                continue
            k = os.read(fd, 16).decode(errors="ignore")
            if k in ("q", "Q"):
                break
            if k == "\x1b" and overlay:
                overlay = None
            elif k in ("\x1b[A", "k"):
                if focus == "agents":
                    sel = max(0, sel - 1)
                else:
                    esel = max(0, esel - 1)
            elif k in ("\x1b[B", "j"):
                if focus == "agents":
                    sel = min(len(mon.agents) - 1, sel + 1)
                else:
                    esel = min(len(mon.events) - 1, esel + 1)
            elif k == "\t":
                focus = "events" if focus == "agents" else "agents"
            elif k in ("\r", "\n") and focus == "agents" and mon.agents:
                a = mon.agents[sel]
                name = a.get("short") if a["kind"] == "chat" else a["win"]
                overlay = (f"{name} · {AG.label(a, 60)}", peek(a["pane"]), a["pane"])
            elif k in ("d", "D", "\r", "\n") and focus == "events" and mon.events:
                e = list(reversed(mon.events))[min(esel, len(mon.events) - 1)]
                txt = TYPE_UI.get(e["type"], (e["type"],))[0]
                body = [f"{hhmm(e['ts'])}  {e['who']}  {txt}", ""] + wrap(e["detail"], cols - 10)
                overlay = (f"desvío · {e['who']}", body, None)
            elif k in ("s", "S"):
                # leer antes de abrir para escribir: open("w") vacía el archivo y sound_on() leería "" (on)
                new = "off" if sound_on() else "on"
                os.makedirs(os.path.dirname(SOUND_OFF), exist_ok=True)
                with open(SOUND_OFF, "w") as f:
                    f.write(new + "\n")
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()


def peek(pane):
    out = AG.sh("tmux", "capture-pane", "-p", "-J", "-t", pane, "-S", "-60")
    return [l for l in out.splitlines() if l.strip()][-60:]


def wrap(s, w):
    words, out, cur = (s or "").split(), [], ""
    for wd in words:
        if len(cur) + len(wd) + 1 > w:
            out.append(cur)
            cur = wd
        else:
            cur = f"{cur} {wd}".strip()
    return out + ([cur] if cur else [])


if __name__ == "__main__":
    main()
