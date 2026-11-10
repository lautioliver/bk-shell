#!/usr/bin/env python3
"""bk stats (alt+u): estadísticas de uso de Claude Code, a lo Gentle Shell 4.

Overview: mapa de actividad del último año, modelo favorito, sesiones, días activos, tokens,
costo (equivalente en API), rachas. Models: tokens y costo por modelo. Session: hoy y este proyecto.

Escanea ~/.claude/projects/**/*.jsonl de forma incremental (offset por archivo) y guarda un
mensaje por message.id en ~/.cache/barrilete/stats.json. La primera vez tarda (todo el historial).
Teclas: tab / ←→ pestañas · r recargar · q/esc salir.   --json: imprime el resumen.
"""
import curses, glob, json, os, sys, time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from usage import cost, project_name  # precios y nombre de proyecto: una sola fuente
import gentle as G  # noqa: E402

PROJECTS = os.path.expanduser("~/.claude/projects")
CACHE = os.path.expanduser("~/.cache/barrilete/stats.json")
QUIJOTE = 500_000  # tokens aproximados de Don Quijote


def load():
    try:
        return json.load(open(CACHE))
    except (OSError, ValueError):
        return {"offsets": {}, "msgs": {}, "sessions": {}}


def save(st):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, CACHE)


def scan(st, progress=None):
    """msgs[id] = [día, modelo, proyecto, in, out, cw5, cw1h, cr]; sessions[sid] = [inicio, fin]."""
    offsets, msgs, sess = st["offsets"], st["msgs"], st["sessions"]
    files = glob.glob(os.path.join(PROJECTS, "**", "*.jsonl"), recursive=True)
    for i, path in enumerate(files):
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        off = offsets.get(path, 0)
        if size < off:
            off = 0
        if size == off:
            continue
        if progress:
            progress(i, len(files), path)
        sid = os.path.basename(path)[:-6]
        with open(path, "rb") as f:
            f.seek(off)
            data = f.read()
        end = data.rfind(b"\n") + 1
        for raw in data[:end].splitlines():
            if b'"timestamp"' not in raw:
                continue
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            ts = e.get("timestamp")
            if not ts:
                continue
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if e.get("type") in ("user", "assistant"):
                s = sess.setdefault(sid, [t, t])
                s[0], s[1] = min(s[0], t), max(s[1], t)
            m = e.get("message") or {}
            u = m.get("usage")
            mid = m.get("id")
            if e.get("type") != "assistant" or not u or not mid:
                continue
            model = m.get("model") or "?"
            if model == "<synthetic>":
                continue
            cc = u.get("cache_creation") or {}
            cw1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
            cw = u.get("cache_creation_input_tokens", 0) or 0
            day = datetime.fromtimestamp(t).date().isoformat()
            row = [day, model, project_name(e.get("cwd")), u.get("input_tokens", 0) or 0,
                   u.get("output_tokens", 0) or 0, max(cw - cw1h, 0), cw1h, u.get("cache_read_input_tokens", 0) or 0]
            prev = msgs.get(mid)
            if prev is None or row[4] >= prev[4]:
                msgs[mid] = row
        offsets[path] = off + end
    return st


def summary(st, project=None):
    keys = ("in", "out", "cw5", "cw1h", "cr")
    per_day = defaultdict(lambda: {"tokens": 0, "cost": 0.0})
    models = defaultdict(lambda: {"tokens": 0, "cost": 0.0, "msgs": 0})
    tot = dict.fromkeys(keys, 0)
    total_cost = 0.0
    today = date.today().isoformat()
    today_t = {"tokens": 0, "cost": 0.0}
    proj_t = {"tokens": 0, "cost": 0.0}
    for day, model, proj, *vals in st["msgs"].values():
        u = dict(zip(keys, vals))
        c = cost(model, u) or 0.0
        tk = u["in"] + u["out"] + u["cw5"] + u["cw1h"] + u["cr"]
        per_day[day]["tokens"] += tk
        per_day[day]["cost"] += c
        models[model]["tokens"] += tk
        models[model]["cost"] += c
        models[model]["msgs"] += 1
        for k in keys:
            tot[k] += u[k]
        total_cost += c
        if day == today:
            today_t["tokens"] += tk; today_t["cost"] += c
        if project and (proj == project or proj.startswith(project + "#")):
            proj_t["tokens"] += tk; proj_t["cost"] += c
    days = sorted(per_day)
    active = set(days)
    # Rachas de días seguidos con actividad
    longest = cur = 0
    prev = None
    for d in days:
        dd = date.fromisoformat(d)
        cur = cur + 1 if prev and dd - prev == timedelta(days=1) else 1
        longest = max(longest, cur)
        prev = dd
    streak, d = 0, date.today()
    if d.isoformat() not in active:
        d -= timedelta(days=1)
    while d.isoformat() in active:
        streak += 1; d -= timedelta(days=1)
    span = (date.today() - date.fromisoformat(days[0])).days + 1 if days else 0
    sess = st["sessions"]
    longest_s = max((e - s for s, e in sess.values()), default=0)
    fav = max(models.items(), key=lambda kv: kv[1]["msgs"])[0] if models else "—"
    busiest = max(per_day.items(), key=lambda kv: kv[1]["tokens"])[0] if per_day else "—"
    return {
        "per_day": dict(per_day), "models": dict(models), "tokens": tot, "total_tokens": sum(tot.values()),
        "cost": total_cost, "favorite": fav, "sessions": len(sess), "active_days": len(active),
        "span_days": span, "busiest": busiest, "longest_streak": longest, "current_streak": streak,
        "longest_session": longest_s, "today": today_t, "project": proj_t,
    }


def human(n):
    for u, d in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= d:
            return f"{n / d:.1f}{u}" if n < 10 * d else f"{n / d:.0f}{u}"
    return str(int(n))


def model_name(m):
    m = m.replace("claude-", "")
    for fam in ("opus", "sonnet", "haiku", "fable"):
        if m.startswith(fam):
            return (fam + " " + m[len(fam):].strip("-").replace("-", ".")).strip()
    return m


MONTHS = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def ui(scr, st, project):
    curses.curs_set(0)
    curses.use_default_colors()
    A = G.curses_attrs(shades=5)  # shade0..4: de "nada" a "mucho", de tint a rose
    ROSE, PETAL, MUTED, TEXT, AMBER = (A[k] for k in ("rose", "petal", "muted", "text", "amber"))
    tabs, tab = ["Overview", "Models", "Session"], 0
    s = summary(st, project)
    while True:
        H, W = scr.getmaxyx()
        scr.erase()
        scr.addnstr(0, 1, "✿ Stats", W - 2, ROSE | curses.A_BOLD)
        x = 2
        for i, t in enumerate(tabs):
            label = f"[{t}]" if i == tab else f" {t} "
            scr.addnstr(1, x, label, W - x - 1, (PETAL | curses.A_BOLD) if i == tab else MUTED)
            x += len(label) + 2
        y = 3
        if tab == 0:
            # Mapa de actividad: 53 semanas x 7 días, terminando hoy
            weeks = max(10, min(53, (W - 10) // 2))
            end = date.today()
            start = end - timedelta(days=end.weekday() + 7 * (weeks - 1))
            vals = [v["tokens"] for v in s["per_day"].values()] or [1]
            hi = sorted(vals)[int(len(vals) * 0.95)] if len(vals) > 1 else vals[0]
            last_m = None
            for w in range(weeks):
                d0 = start + timedelta(days=7 * w)
                if d0.month != last_m:
                    scr.addnstr(y, 8 + 2 * w, MONTHS[d0.month - 1], max(0, W - 9 - 2 * w), MUTED)
                    last_m = d0.month
            for dow, lab in ((0, "Lun"), (2, "Mié"), (4, "Vie")):
                scr.addnstr(y + 1 + dow, 2, lab, 4, MUTED)
            for w in range(weeks):
                for dow in range(7):
                    d = start + timedelta(days=7 * w + dow)
                    if d > end:
                        continue
                    tk = s["per_day"].get(d.isoformat(), {}).get("tokens", 0)
                    lvl = 0 if tk == 0 else min(4, 1 + int(3 * tk / max(hi, 1)))
                    ch = "·" if lvl == 0 else "█"
                    if y + 1 + dow < H - 1 and 8 + 2 * w < W - 1:
                        scr.addnstr(y + 1 + dow, 8 + 2 * w, ch, 1, A[f"shade{lvl}"])
            y += 9
            scr.addnstr(y, 8, "Menos ", W - 9, MUTED)
            for i in range(1, 5):
                scr.addnstr(y, 14 + 2 * (i - 1), "█", 1, A[f"shade{i}"])
            scr.addnstr(y, 23, "Más", W - 24, MUTED)
            y += 2
            t = s["tokens"]
            left = [("Modelo favorito", model_name(s["favorite"])), ("Sesiones", str(s["sessions"])),
                    ("Días activos", f"{s['active_days']}/{s['span_days']}"), ("Día más activo", s["busiest"]),
                    ("Tokens", f"in {human(t['in'])} · out {human(t['out'])} · cache read {human(t['cr'])}"
                               f" · cache write {human(t['cw5'] + t['cw1h'])}"),
                    ("Costo (≈API)", f"${s['cost']:,.2f}")]
            ls = s["longest_session"]
            right = [("Tokens totales", human(s["total_tokens"])), ("Sesión más larga", f"{int(ls // 3600)}h{int(ls % 3600 // 60):02d}m"),
                     ("Racha más larga", f"{s['longest_streak']} días"), ("Racha actual", f"{s['current_streak']} días")]
            for i, (k, v) in enumerate(left):
                if y + i < H - 2:
                    scr.addnstr(y + i, 2, f"{k:<16}", 16, MUTED)
                    scr.addnstr(y + i, 19, v, max(0, W - 21), TEXT)
            rx = max(60, W - 40)
            if rx + 30 < W:
                for i, (k, v) in enumerate(right):
                    scr.addnstr(y + i, rx, f"{k:<17}", 17, MUTED)
                    scr.addnstr(y + i, rx + 18, v, W - rx - 19, TEXT)
            y += len(left) + 1
            if y < H - 2:
                x_ = s["total_tokens"] / QUIJOTE
                scr.addnstr(y, 2, f"✿ Son ~{x_:,.0f} veces los tokens del Quijote.", W - 3, ROSE)
        elif tab == 1:
            rows = sorted(s["models"].items(), key=lambda kv: -kv[1]["cost"])
            total = sum(v["tokens"] for _, v in rows) or 1
            scr.addnstr(y, 2, f"{'modelo':<16}{'tokens':>10}{'costo':>12}{'resp':>8}   uso", W - 3, MUTED)
            for i, (m, v) in enumerate(rows, 1):
                if y + i >= H - 2:
                    break
                bar = "█" * max(1, int(20 * v["tokens"] / total))
                scr.addnstr(y + i, 2, f"{model_name(m):<16}{human(v['tokens']):>10}{'$' + format(v['cost'], ',.2f'):>12}"
                                      f"{v['msgs']:>8}   ", W - 3, TEXT)
                scr.addnstr(y + i, 50, bar, max(0, W - 51), PETAL)
        else:
            rows = [("Hoy", s["today"]), (f"Proyecto {project}" if project else "Proyecto", s["project"])]
            for i, (k, v) in enumerate(rows):
                scr.addnstr(y + i * 2, 2, f"{k:<28}", 28, MUTED)
                scr.addnstr(y + i * 2, 30, f"{human(v['tokens'])} tokens · ${v['cost']:,.2f}", W - 31, TEXT)
            scr.addnstr(y + 5, 2, "Precios: tabla de usage.py (equivalente en API; con suscripción no se cobra así).",
                        W - 3, MUTED)
        scr.addnstr(H - 1, 1, "tab/←→ pestañas · r recargar · esc cerrar", W - 2, MUTED)
        scr.refresh()
        k = scr.getch()
        if k in (ord("q"), 27):
            return
        if k in (9, curses.KEY_RIGHT, ord("l")):
            tab = (tab + 1) % len(tabs)
        elif k in (curses.KEY_LEFT, ord("h"), curses.KEY_BTAB):
            tab = (tab - 1) % len(tabs)
        elif k == ord("r"):
            scan(st); save(st); s = summary(st, project)


def main():
    st = load()
    first = not st["offsets"]
    if first and sys.stdout.isatty():
        print("\033[38;2;240;149;200m✿\033[0m leyendo todo tu historial de Claude Code por primera vez…")

    def prog(i, n, path):
        if first and sys.stdout.isatty():
            sys.stdout.write(f"\r  {i + 1}/{n} transcripts\033[K"); sys.stdout.flush()
    scan(st, prog)
    save(st)
    proj = None
    top = os.environ.get("BK_PROJECT")
    if top:
        proj = os.path.basename(top.rstrip("/"))
    if "--json" in sys.argv:
        s = summary(st, proj)
        s.pop("per_day")
        print(json.dumps(s, indent=1, default=str))
        return
    curses.wrapper(ui, st, proj)


if __name__ == "__main__":
    main()
