#!/usr/bin/env python3
"""bk monitor: pantalla para dejar abierta en otro monitor y vigilar a todos los agentes.

  Agentes   los de bk y los chats: título, estado, herramienta, minutos, costo de la sesión y sus subagentes
  Desvíos   en vivo, apenas pasan: los que detecta `bk learn` (hooks, barandas, red.sh/verify.py, correcciones,
            interrupciones, permisos rechazados, bloqueantes del contrato) y otros propios del monitor:
            agente trabado, el mismo error repetido, force push, otra credencial, "CI en verde" con el CI rojo,
            y atribuirte una decisión. Los graves (⚠) suenan y notifican una vez.
  Hoy       costo del día por modelo y desvíos de hoy contra el promedio de la semana
  Te esperan / Tareas  lo que necesita tu respuesta (preguntas, permisos, reviewers propuestos) y las tareas de
            los agentes: estado, cómo cerraron, el último evento y los bk say en cola

Teclas: ↑↓ elegir agente · enter ver su pantalla · tab pasar a los desvíos · d detalle · s sonido on/off (al prenderlo suena) · q salir
Suenan solo los graves (⚠): force push, otra credencial, CI en verde falso, el mismo error repetido y agente trabado 20 min.
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
import tasks as TK   # noqa: E402  (tareas de los agentes, ADR-005 del kit)
import checkpoints as CK   # noqa: E402  (planes, reviewers propuestos, PRs y lecciones que te esperan)
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
GRAVE_SOUND, GRAVE_VOLUME = "Hero", "2.5"   # largo y llamativo; afplay acepta más de 1 (el sonido del sistema es bajo)
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
        f = f"/System/Library/Sounds/{GRAVE_SOUND}.aiff"
        if os.path.exists(f) and shutil.which("afplay"):
            subprocess.Popen(["afplay", "-v", GRAVE_VOLUME, f], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        self.task_list, self.tasks_by_win, self.checkpoints = [], {}, []
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
        self.task_list = []
        try:
            self.task_list = TK.tasks()
        except Exception:   # una carpeta de tareas rota no puede tirar el monitor
            pass
        self.tasks_by_win = AG.task_by_window()
        try:
            self.checkpoints = CK.items()
        except Exception:
            self.checkpoints = []
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


def ago(mins):
    """3m · 2h · 1d: cuánto hace del último evento."""
    mins = max(0, int(mins or 0))
    return f"{mins}m" if mins < 60 else (f"{mins // 60}h" if mins < 1440 else f"{mins // 1440}d")


def since(iso):
    """Minutos desde un ISO 8601 en UTC (los ts de las tareas)."""
    t = epoch(iso) if iso else 0
    return int((now() - t) // 60) if t else 0


def name_of(a):
    return a.get("short") if a["kind"] == "chat" else a["win"]


def state_of(a, mon=None):
    """(glifo, color, texto) del estado de un agente o chat, en palabras: qué está pasando, no qué herramienta usa."""
    if a.get("note"):    # su tarea ya cerró: eso manda sobre el estado viejo del pane
        g = a["note"][:1] if a["note"][:1] in "✓⚠✕" else "✓"
        return g, (AMBER if g == "⚠" else RED if g == "✕" else MUTED), "terminó"
    if a["asks"]:
        if a["why"] == "pide permiso":
            return "!", RED, "pide permiso"
        return "?", AMBER, a["why"] or "te pregunta"
    if a["state"] == "waiting":
        return "!", RED, a["why"] or "te espera"
    if a["state"] == "working":
        if a.get("mins", 0) * 60 >= STALE_WARN:
            return "⚠", AMBER, f"¿trabado? {ago(a['mins'])} quieto"
        return "●", GREEN, "trabajando"
    if a["state"] == "background":
        return "◐", IRIS, "en 2º plano"
    if a["kind"] == "chat":
        return "◇", MUTED, {"done": "listo", "idle": "libre"}.get(a["state"], a["state"] or "sin estado")
    return "○", MUTED, {"done": "listo", "idle": "libre"}.get(a["state"], a["state"] or "sin estado")


def task_cell(t, queued=0):
    """La tarea en pocas letras: en curso / esperando / ✓ PR #12 / ⚠ sin bk done, más ✉N si hay bk say en cola."""
    if not t:
        return f"{MUTED}-{RST}"
    st = t.get("status")
    note = AG.task_note(t)
    if note:
        g, txt = note
        txt = {"terminó sin bk done": "sin bk done", "cerró con PR": "PR"}.get(txt, txt).replace("cerró con ", "")
        col = AMBER if g == "⚠" else RED if g == "✕" else GREEN
        cell = f"{col}{g} {txt}{RST}"
    else:
        cell = {"in_progress": f"{GREEN}en curso{RST}", "waiting": f"{AMBER}esperando{RST}",
                "pending": f"{AMBER}propuesta{RST}"}.get(st, f"{MUTED}{st or '-'}{RST}")
    return cell + (f" {PETAL}✉{queued}{RST}" if queued else "")


def header_row(cols, W):
    """Fila de encabezados: [(texto, ancho), …] en gris."""
    return f"{MUTED}" + "".join(G.fit(t, w) for t, w in cols)[:W] + RST


def agent_row(a, W, selected, task, queued):
    g, col, st = state_of(a)
    tool = a.get("_tool") if a["state"] == "working" and not a.get("note") and a.get("_tool") else ""
    who = f"{(a['role'] or '-')[:17]}" + (f" · {a['model'][:8]}" if a.get("model") else "")
    what = max(12, W - (2 + 15 + 22 + 26 + 6 + 16 + 9))
    line = (f"{col}{g}{RST} {TEXT}{BOLD}{G.fit(name_of(a), 14)}{RST} {MUTED}{G.fit(who, 21)}{RST} "
            f"{G.fit(AG.label(a, what - 1), what)}"
            f"{col}{G.fit(G.cut(st + (f' · {tool}' if tool else ''), 25), 26)}{RST}"
            f"{MUTED}{ago(a['mins']):>4}{RST}  {G.fit(task_cell(task, queued), 16)}{PETAL}{money(a.get('_cost', 0)):>9}{RST}")
    line = G.cut(line, W)
    if selected:
        line = "\033[7m" + G.ANSI.sub("", line) + RST
    rows = [line]
    for d, _ in a.get("subs", []):
        rows.append(f"    {MUTED}└ subagente: {G.cut(d, W - 18)}{RST}")
    return rows


def chat_row(a, W, selected):
    g, col, st = state_of(a)
    tool = a.get("_tool") if a["state"] == "working" and a.get("_tool") else ""
    what = max(12, W - (2 + 23 + 26 + 6 + 10))
    line = (f"{col}{g}{RST} {TEXT}{BOLD}{G.fit(name_of(a), 22)}{RST} {G.fit(G.cut(a.get('title') or '(sin nombre)', what - 1), what)}"
            f"{col}{G.fit(G.cut(st + (f' · {tool}' if tool else ''), 25), 26)}{RST}{MUTED}{ago(a['mins']):>4}{RST}  "
            f"{PETAL}{money(a.get('_cost', 0)):>9}{RST}")
    line = G.cut(line, W)
    if selected:
        line = "\033[7m" + G.ANSI.sub("", line) + RST
    rows = [line]
    for d, _ in a.get("subs", []):
        rows.append(f"    {MUTED}└ subagente: {G.cut(d, W - 18)}{RST}")
    return rows


def agent_rows(mon, W, sel):
    """Las dos tablas de abajo del encabezado: agentes de bk y chats, con encabezados de columna.
    sel es el índice en mon.agents (la misma lista que recorren ↑↓)."""
    tks = mon.tasks_by_win
    ags = [(i, a) for i, a in enumerate(mon.agents) if a["kind"] != "chat"]
    chats = [(i, a) for i, a in enumerate(mon.agents) if a["kind"] == "chat"]
    what = max(12, W - (2 + 15 + 22 + 26 + 6 + 16 + 9))
    arows = [header_row([("  AGENTE", 17), ("ROL · MODELO", 22), ("QUÉ", what), ("ESTADO", 26), ("HACE", 6),
                         ("TAREA", 16), ("COSTO HOY", 9)], W)] if ags else []
    for i, a in ags:
        t = tks.get(a["win"])
        arows += agent_row(a, W, i == sel, t, AG.queued(t) if t else 0)
    cwhat = max(12, W - (2 + 23 + 26 + 6 + 10))
    crows = [header_row([("  CHAT", 25), ("DE QUÉ", cwhat), ("ESTADO", 26), ("HACE", 6), ("COSTO HOY", 9)], W)] if chats else []
    for i, a in chats:
        crows += chat_row(a, W, i == sel)
    return arows or [f"{MUTED}ningún agente de bk ahora (bk run, bk review, bk fix){RST}"], crows


def waiting_rows(mon, W):
    """Lo que necesita tu respuesta, primero: preguntas y permisos en vivo, y después los checkpoints (planes,
    reviewers propuestos, PRs para revisar o mergear, lecciones), con qué hacer en cada uno."""
    rows = []
    for a in mon.agents:
        if a.get("note") or not (a["asks"] or a["state"] == "waiting"):
            continue
        g, col, st = state_of(a)
        q = f" {AMBER}«{G.cut(a['question'], W - 40)}»{RST}" if a.get("question") else ""
        rows.append(G.cut(f"{col}{g}{RST} {TEXT}{BOLD}{G.fit(name_of(a), 16)}{RST} {col}{st}{RST}{q}"
                          f" {MUTED}· hace {ago(a['mins'])} · bk say {name_of(a)} \"…\"{RST}", W))
    # lo que solo vos podés hacer (bk checkpoints): uno por línea, salvo las lecciones y los PRs sin movimiento,
    # que van en una línea cada grupo (el detalle está en bk checkpoints)
    lessons = [c for c in mon.checkpoints if c["kind"] == "lesson"]
    stale = [c for c in mon.checkpoints if c.get("stale")]
    for c in mon.checkpoints:
        if c in lessons or c in stale:
            continue
        num = f"#{c['number']} " if c.get("number") else (f"{c['id']} " if c.get("id") else "")
        rows.append(G.cut(f"{AMBER}◆{RST} {TEXT}{BOLD}{c['what']}{RST} {num}{c['title']} {MUTED}({c['where']}) · {c['todo']}{RST}", W))
    if len(lessons) == 1:
        c = lessons[0]
        rows.append(G.cut(f"{AMBER}◆{RST} {TEXT}{BOLD}{c['what']}{RST} {c['title']} {MUTED}({c['where']}) · {c['todo']}{RST}", W))
    elif lessons:
        rows.append(G.cut(f"{AMBER}◆{RST} {TEXT}{BOLD}{len(lessons)} lecciones propuestas{RST} "
                          f"{MUTED}· {lessons[0]['todo']}{RST}", W))
    if stale:
        nums = ", ".join(f"{c['where']}#{c['number']}" for c in stale[:4]) + (" …" if len(stale) > 4 else "")
        rows.append(G.cut(f"{MUTED}◇ {len(stale)} PR{'s' if len(stale) != 1 else ''} sin movimiento hace más de "
                          f"{CK.STALE_DAYS} días ({nums}; el más viejo, {max(c['days'] for c in stale)} días) · bk checkpoints{RST}", W))
    return rows


def last_event(t):
    """(tipo, minutos) del último evento de una tarea, sin leer el archivo entero."""
    try:
        with open(os.path.join(t["dir"], "events.jsonl"), "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 4096))
            lines = [l for l in f.read().splitlines() if l.strip()]
        e = json.loads(lines[-1]) if lines else {}
    except (OSError, ValueError, KeyError):
        return "", 0
    extra = f" {e['sha'][:7]}" if e.get("sha") else (f" #{e['pr']}" if e.get("pr") else "")
    return (e.get("type", "") + extra), since(e.get("ts"))


def task_rows(mon, W, n):
    """Las tareas: activas primero, después las que cerraron en las últimas 24 h."""
    # las vigentes (una relanzada deja la vieja como <id>~<fecha>: esa queda para bk tasks), activas primero y
    # después las que cerraron en las últimas 24 h, de la más nueva a la más vieja
    ts = [t for t in mon.task_list if "~" not in os.path.basename(t.get("dir", ""))
          and (t.get("status") in TK.ACTIVE or since(t.get("closed") or t.get("updated")) < 1440)]
    ts.sort(key=lambda t: (t.get("status") not in TK.ACTIVE, -epoch(t.get("closed") or t.get("updated") or t.get("created") or "")))
    if not ts:
        return [f"{MUTED}sin tareas en las últimas 24 h (las crea bk run, bk review y bk fix){RST}"]
    rows = [header_row([("  TAREA", 22), ("PROYECTO", 20), ("ESTADO", 18), ("ÚLTIMO EVENTO", 26), ("HACE", 6)], W)]
    for t in ts[:n]:
        ev, mins = last_event(t)
        rows.append(G.cut(f"  {TEXT}{BOLD}{G.fit(t['id'], 20)}{RST}{MUTED}{G.fit(t.get('project', ''), 20)}{RST}"
                          f"{G.fit(task_cell(t, AG.queued(t)), 18)}{MUTED}{G.fit(ev, 26)}{ago(mins):>4}{RST}", W))
    if len(ts) > n:
        rows.append(f"  {MUTED}… y {len(ts) - n} más (bk tasks){RST}")
    return rows


def grouped(events):
    """Los desvíos más nuevos primero, juntando seguidos los del mismo tipo y del mismo agente (×N)."""
    out = []
    for e in reversed(events):
        if out and out[-1]["type"] == e["type"] and out[-1]["who"] == e["who"] and out[-1]["ts"] - e["ts"] < 1800:
            out[-1]["n"] += 1
            continue
        out.append(dict(e, n=1))
    return out


def event_rows(mon, W, n, sel=None):
    rows = [header_row([("HORA ", 6), ("  QUIÉN", 25), ("QUÉ", 30), ("DETALLE", 40)], W)]
    evs = grouped(mon.events)[:n]
    for i, e in enumerate(evs):
        txt, col = TYPE_UI.get(e["type"], (e["type"], MUTED))
        grave = e["type"] in GRAVE_TYPES
        mark = f"{RED}⚠{RST}" if grave else f"{col}•{RST}"
        times = f" {BOLD}×{e['n']}{RST}" if e["n"] > 1 else ""
        line = (f"{MUTED}{hhmm(e['ts'])}{RST} {mark} {TEXT}{BOLD}{G.fit(G.cut(e['who'], 22), 23)}{RST}"
                f"{col}{G.fit(txt + times, 30)}{RST}{MUTED}{e['detail']}{RST}")
        line = G.cut(line, W)
        if sel is not None and i == sel:
            line = "\033[7m" + G.ANSI.sub("", line) + RST
        rows.append(line)
    return rows if evs else [f"{GREEN}sin desvíos todavía hoy{RST}"]


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
        rows.append(G.lr(f"{TEXT}más caro{RST} {MUTED}{G.cut(name_of(top), 14)}{RST}", f"{PETAL}{money(top['_cost'])}{RST}", W))
    return rows


LEGEND = (f" {GREEN}●{RST} trabajando  {AMBER}?{RST} te pregunta  {RED}!{RST} pide permiso  {IRIS}◐{RST} 2º plano  "
          f"{MUTED}○{RST} listo  {MUTED}◇{RST} chat  {GREEN}✓{RST}/{AMBER}⚠{RST}/{RED}✕{RST} cómo cerró  {PETAL}✉{RST} bk say en cola")


def headline(mon):
    ags = [a for a in mon.agents if a["kind"] != "chat"]
    chats = [a for a in mon.agents if a["kind"] == "chat"]
    busy = sum(1 for a in ags if a["state"] in ("working", "background") and not a.get("note"))
    # los PRs sin movimiento no cuentan como "te esperan": siguen en la lista, apagados
    waits = sum(1 for a in mon.agents if not a.get("note") and (a["asks"] or a["state"] == "waiting")) \
        + sum(1 for c in mon.checkpoints if not c.get("stale"))
    parts = [f"{len(ags)} agente{'s' if len(ags) != 1 else ''}" + (f" ({busy} trabajando)" if busy else ""),
             f"{len(chats)} chat{'s' if len(chats) != 1 else ''}"]
    parts.append(f"{AMBER}{BOLD}{waits} te espera{'n' if waits != 1 else ''}{RST}{MUTED}" if waits else f"{GREEN}nadie te espera{RST}{MUTED}")
    parts += [f"{money((mon.today or {}).get('cost_usd', 0))} hoy", f"sonido {'on' if sound_on() else 'off'}"]
    return f" {ROSE}✿{RST} {BOLD}bk monitor{RST} {MUTED}· {time.strftime('%H:%M:%S')} · " + " · ".join(parts) + RST


def frame(mon, cols, lines, sel, focus, esel, overlay):
    W = cols
    out = [G.cut(headline(mon), W), ""]
    if overlay:
        out += G.card(overlay[0], [G.cut(l, W - 6) for l in overlay[1]][-(lines - 6):], W, glyph="▸")
        return out[:lines - 1] + [f" {MUTED}esc vuelve{RST}"]
    wait = waiting_rows(mon, W - 6)
    if wait:
        out += G.card("Te esperan", wait, W, glyph="?", color=AMBER)
    arows, crows = agent_rows(mon, W - 6, sel if focus == "agents" else -1)
    out += G.card("Agentes", arows, W, glyph="∗")
    if crows:
        out += G.card("Chats", crows, W, glyph="◇")
    # las tareas y los desvíos se reparten lo que queda (al menos 3 filas cada uno)
    room = max(6, lines - len(out) - 3)
    n_tasks = max(2, min(len(mon.task_list) or 1, room // 3))
    out += G.card("Tareas", task_rows(mon, W - 6, n_tasks), W, glyph="◆")
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
    keys = f" {MUTED}↑↓ elegir · enter su pantalla · tab desvíos · d detalle · s sonido · q salir{RST}"
    return out[:lines - 2] + [G.cut(LEGEND, W), keys]


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
                if new == "on":   # al prenderlo, suena una vez: así sabés que anda
                    alert("bk monitor", "sonido prendido: los desvíos graves suenan así")
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
