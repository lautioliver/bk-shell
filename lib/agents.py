#!/usr/bin/env python3
"""Quién es quién entre los agentes de bk: nombre de ventana, título del issue/PR y si te espera.

  agents.py list                 tabla para `bk ls` (los que te esperan primero)
  agents.py resolve <algo>       ventana de un agente por nombre, número (#240, 218) o palabra del título
  agents.py auto                 el único agente que te espera (para `bk say "mensaje"` sin nombre)
  agents.py pick                 selector en la terminal; imprime la ventana elegida
  agents.py waiting [--json]     los que te esperan, con su pregunta
  agents.py json                 {pane: ventana, título, pregunta, subagentes} para Sketchybar (sin consultar GitHub)
  agents.py subs <palabra>       el transcript del subagente vivo cuya descripción coincide (para bk sub)

Los chats de los workspaces (bk-kit-2…) también se nombran: por su nombre (bk name / bk chat --nombre),
por el de /rename o por el título que les puso Claude. resolve los devuelve como chat:<sesión>.
  agents.py warm                 busca los títulos que falten (bk lo corre al lanzar un agente)

resolve/auto/pick imprimen la ventana en stdout. Si no pueden decidir salen con 3 (varios) o 4 (ninguno)
y explican en stderr; con una terminal, resolve y auto abren el selector en vez de fallar.
"""
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

import tasks as TK   # tareas de los agentes (ADR-005 del kit): estado final, propuestas y cola de bk say

SESSION = os.environ.get("BK_SESSION", "agents")
STATE_DIR = os.environ.get("BK_STATE_DIR", os.path.expanduser("~/.cache/sketchybar/agents"))
PROJECTS = os.environ.get("BK_PROJECTS") or os.path.expanduser("~/.claude/projects")
TITLES = os.path.join(os.environ.get("BK_CACHE_DIR", os.path.expanduser("~/.cache/bk")), "titles.json")
WIN_RE = re.compile(r"^(?:[\w.-]+-)?(i|pr|f)(\d+)$")   # i218, pr241, f240, abra-i12

R, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
ROSE, AMBER, GREEN, IRIS = "\033[38;5;218m", "\033[38;5;222m", "\033[38;5;150m", "\033[38;5;147m"


def sh(*args, timeout=5):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def fold(s):
    """minúsculas y sin acentos, para buscar 'glosario' en 'Glosário' o 'ddd' en 'DDD'."""
    return "".join(c for c in unicodedata.normalize("NFD", s or "").casefold() if unicodedata.category(c) != "Mn")


# ---------- títulos (cache por repo#número; se buscan una sola vez) ----------
def load_titles():
    try:
        return json.load(open(TITLES))
    except (OSError, ValueError):
        return {}


def repo_of(cwd):
    url = sh("git", "-C", cwd, "remote", "get-url", "origin").strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else ""


def pr_of_branch(cwd, titles, fetch=True):
    """Número del PR abierto desde la rama de ese worktree (i218 trabaja en el PR #240)."""
    if not cwd or not os.path.isdir(cwd):
        return ""
    branch = sh("git", "-C", cwd, "branch", "--show-current").strip()
    repo = repo_of(cwd)
    if not branch or not repo or branch in ("main", "master"):
        return ""
    key = f"{repo}@{branch}"
    if titles.get(key):
        return titles[key]
    if not fetch:
        return ""
    env = {k: v for k, v in os.environ.items() if k not in ("GH_CONFIG_DIR", "GH_TOKEN")}
    try:
        n = subprocess.run(["gh", "pr", "list", "-R", repo, "--head", branch, "--state", "all", "--json", "number",
                            "-q", ".[0].number // empty"], capture_output=True, text=True, timeout=6, env=env).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        n = ""
    if n.isdigit():   # solo se guarda cuando hay PR: si todavía no lo abrió, se vuelve a buscar
        titles[key] = n
        save_titles(titles)
    return n


def save_titles(titles):
    try:
        os.makedirs(os.path.dirname(TITLES), exist_ok=True)
        json.dump(titles, open(TITLES, "w"), ensure_ascii=False)
    except OSError:
        pass


def title_for(cwd, num, titles, fetch=True):
    if not cwd or not num:
        return ""
    repo = repo_of(cwd) if os.path.isdir(cwd) else ""
    key = f"{repo}#{num}"
    if not repo:
        return ""
    if key in titles:
        return titles[key]
    if not fetch:
        return ""
    env = {k: v for k, v in os.environ.items() if k not in ("GH_CONFIG_DIR", "GH_TOKEN")}
    try:   # el endpoint de issues sirve también para PRs
        t = subprocess.run(["gh", "api", f"repos/{repo}/issues/{num}", "--jq", ".title"],
                           capture_output=True, text=True, timeout=6, env=env).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        t = ""
    if t:
        titles[key] = t
        save_titles(titles)
    return t


# ---------- pantalla del agente: ¿te está preguntando algo? ----------
def last_question(pane):
    """La última línea con '?' de lo que escribió el agente, si su último mensaje termina preguntando."""
    if not pane:
        return ""
    text = sh("tmux", "capture-pane", "-p", "-J", "-t", pane, "-S", "-40")
    lines = [l.rstrip() for l in text.splitlines()]
    # cortar en el prompt de Claude Code (la caja con ❯) y la línea de estado de abajo
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("❯"):
            lines = lines[:i]
            break
    # y quedarse con lo posterior a tu último mensaje (otro ❯ más arriba): una pregunta ya respondida no cuenta
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("❯"):
            lines = lines[i + 1:]
            break
    noise = re.compile(r"/clear to save|Update installed|Tip:|ctrl\+|shift\+tab|esc to interrupt|"
                       r"^\s*[✻✢✶✳·*]\s+\w+.*(for \d|…)")   # "✻ Baked for 3m", avisos de Claude Code
    body = [l for l in lines if l.strip() and not re.fullmatch(r"[\s─━╭╮╰╯│]*", l) and not noise.search(l)]
    tail = body[-8:]
    for l in reversed(tail):
        if "?" in l:
            return re.sub(r"^[\s⏺•\-\d.]+", "", l).strip()
    return ""


# ---------- sesiones de Claude: título y subagentes ----------
_TITLE_CACHE = {}


def session_title(sid):
    """El nombre de la sesión de Claude: el de /rename si lo tiene; si no, el que le puso Claude."""
    if not sid:
        return ""
    import glob
    files = glob.glob(os.path.join(PROJECTS, "*", f"{sid}.jsonl"))
    if not files:
        return ""
    f = files[0]
    try:
        mt = os.path.getmtime(f)
    except OSError:
        return ""
    if _TITLE_CACHE.get(f, (None,))[0] == mt:
        return _TITLE_CACHE[f][1]
    custom = ai = ""
    try:
        with open(f, errors="replace") as fh:
            for l in fh:
                if '"custom-title"' in l or '"ai-title"' in l:
                    try:
                        e = json.loads(l)
                    except ValueError:
                        continue
                    custom = e.get("customTitle") or custom
                    ai = e.get("aiTitle") or ai
    except OSError:
        pass
    t = custom or ai
    _TITLE_CACHE[f] = (mt, t)
    return t


def subagents(sid):
    """Descripciones de los subagentes vivos de una sesión (herramienta Agent), lo más nuevo primero."""
    if not sid:
        return []
    import glob
    out, now = [], time.time()
    for m in glob.glob(os.path.join(PROJECTS, "*", sid, "subagents", "agent-*.meta.json")):
        j = m[: -len(".meta.json")] + ".jsonl"
        try:
            if now - os.path.getmtime(j) > 600:
                continue
            meta = json.load(open(m))
        except (OSError, ValueError):
            continue
        if not meta.get("finished"):
            out.append((os.path.getmtime(j), meta.get("description") or meta.get("agentType") or "subagente", j))
    return [(d, j) for _, d, j in sorted(out, reverse=True)]


def chats(sts):
    """Los chats de los workspaces (bk-<proyecto>, bk-<proyecto>-2…): {sesión: (pane, nombre)}."""
    out = {}
    raw = sh("tmux", "list-sessions", "-F", "#{session_name}")
    for ws in raw.split():
        if not ws.startswith("bk"):
            continue
        name = sh("tmux", "show-options", "-qv", "-t", ws, "@bk_name").strip()
        pane = ""
        for l in sh("tmux", "list-panes", "-t", f"{ws}:main", "-F", "#{pane_id}\t#{pane_start_command}\t#{pane_title}").splitlines():
            pid, start, title = (l.split("\t") + ["", ""])[:3]
            if "bk rail" in start or "bk sidebar" in start:
                continue
            pane = pid
            break
        # si un agente está de visita en el lugar del chat, el chat está en la sesión de agentes (@bk_ws)
        if sh("tmux", "show-options", "-wqv", "-t", f"{ws}:main", "@bk_guest").strip():
            for l in sh("tmux", "list-panes", "-s", "-t", SESSION, "-F", "#{pane_id}\t#{@bk_ws}").splitlines():
                pid, w = (l.split("\t") + [""])[:2]
                if w == ws:
                    pane = pid
        if pane:
            out[ws] = (pane, name)
    return out


# ---------- agentes ----------
def windows():
    """{ventana: pane del agente}. Si el agente está de visita en un workspace (alt+shift+N), su pane real."""
    out = {}
    raw = sh("tmux", "list-panes", "-s", "-t", SESSION, "-F", "#{window_name}\t#{pane_id}\t#{@bk_ws}")
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or parts[0] in out:
            continue
        win, pane, ws = parts[0], parts[1], (parts[2] if len(parts) > 2 else "")
        if ws:
            guest = sh("tmux", "show-options", "-wqv", "-t", f"{ws}:main", "@bk_guest").strip()
            pane = guest or pane
        out[win] = pane
    return out


def states():
    """{pane: estado} de los archivos del hook."""
    out = {}
    try:
        names = os.listdir(STATE_DIR)
    except OSError:
        return out
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            st = json.load(open(os.path.join(STATE_DIR, n)))
        except (OSError, ValueError):
            continue
        st["_file"] = n
        st["_sid"] = n[:-5]
        p = st.get("pane") or ""
        if p and (p not in out or st.get("ts", 0) > out[p].get("ts", 0)):
            out[p] = st
    return out


def task_by_window():
    """{ventana: tarea}: la tarea activa de cada ventana o, si no hay, la última que terminó ahí."""
    out = {}
    try:
        ts = TK.tasks()
    except Exception:   # una carpeta de tareas rota no puede romper bk ls ni el riel
        return out
    for t in ts:
        w = t.get("window")
        if not w:
            continue
        # activa primero; después la vigente (no una guardada aparte como <id>~<fecha>); después la más nueva
        def key(x):
            return (x.get("status") in TK.ACTIVE, "~" not in os.path.basename(x.get("dir", "")), x.get("created", ""))
        if w not in out or key(t) > key(out[w]):
            out[w] = t
    return out


def queued(t):
    """Cuántos bk say esperan a que el agente termine el turno."""
    try:
        with open(os.path.join(t["dir"], "queue.jsonl")) as f:
            return sum(1 for ln in f if ln.strip())
    except (OSError, KeyError):
        return 0


def task_note(t):
    """(glifo, texto) de una tarea terminada, o None si sigue activa."""
    st, r = (t or {}).get("status"), (t or {}).get("result") or {}
    if st == "completed":
        return ("✓", f"cerró con PR #{r['pr']}") if r.get("pr") else ("✓", "review con bloqueantes")
    if st == "failed":
        return ("✕", f"{r.get('kind', 'falló')}: {short(r.get('reason', ''), 40)}".rstrip(": "))
    if st == "incomplete":
        return ("⚠", "terminó sin bk done")
    if st == "cancelled":
        return ("✕", "cancelada")
    return None


def proposals(repo=None):
    """Tareas propuestas que esperan tu aprobación (bk approve), de un repo o de todos."""
    try:
        ts = TK.tasks()
    except Exception:
        return []
    return [t for t in ts if t.get("status") == "pending" and (repo is None or t.get("repo") == repo)]


def gather(fetch_titles=True, look=True, chats_too=True):
    """Lista de agentes de la sesión `agents`, con lo necesario para nombrarlos."""
    titles, sts, now, tks = load_titles(), states(), time.time(), task_by_window()
    out = []
    for win, pane in windows().items():
        st = sts.get(pane, {})
        m = WIN_RE.match(win)
        kind, num = (m.group(1), m.group(2)) if m else ("", "")
        ref = (st.get("ref") or "").lstrip("#")
        cwd = st.get("cwd") or sh("tmux", "display-message", "-p", "-t", pane, "#{pane_current_path}").strip()
        # el título del número de la ventana (pr241 → PR #241; f240 → PR #240; i218 → issue #218)
        title = title_for(cwd, num or ref, titles, fetch_titles)
        pr = num if kind in ("pr", "f") else (pr_of_branch(cwd, titles, fetch_titles) if kind == "i" else "")
        state = st.get("state", "")
        why = ""
        if state == "waiting":
            why = {"question": "te pregunta", "permission": "pide permiso"}.get(st.get("wait"), "") \
                or ("confiar en la carpeta" if st.get("tool") == "confianza" else "te espera")
        elif st.get("phase") == "plan_pending" and state in ("done", "idle"):
            why = "plan esperando tu sí"
        q = last_question(pane) if look and (why or state in ("done", "idle")) else ""
        if state in ("done", "idle") and not why and q:
            why = "te pregunta"
        out.append({
            "win": win, "pane": pane, "kind": kind, "num": num, "ref": ref, "pr": pr, "title": title,
            "role": st.get("role", ""), "model": re.sub(r"^claude-|-\d{8}$", "", st.get("model") or ""),
            "state": state, "asks": bool(why), "why": why, "question": q if why else "",
            "mins": int((now - st.get("ts", now)) // 60) if st else 0,
            "project": project_label(cwd),
            "task": (tks.get(win) or {}).get("status", ""), "note": (task_note(tks.get(win)) or ("", ""))[1],
            "queued": queued(tks[win]) if win in tks else 0,
        })
    for a in out:
        a["subs"] = subagents(sts.get(a["pane"], {}).get("_sid"))
    if chats_too:
        for ws, (pane, name) in chats(sts).items():
            st = sts.get(pane, {})
            state = st.get("state", "")
            why = ""
            if state == "waiting":
                why = {"question": "te pregunta", "permission": "pide permiso"}.get(st.get("wait"), "te espera")
            title = name or session_title(st.get("_sid"))
            out.append({
                "win": "chat:" + ws, "pane": pane, "kind": "chat", "num": "", "ref": "", "pr": "",
                "title": title, "ws": ws, "short": ws[3:] if ws.startswith("bk-") else ws,
                "role": "chat", "model": "", "state": state, "asks": bool(why), "why": why, "question": "",
                "mins": int((now - st.get("ts", now)) // 60) if st else 0,
                "project": project_label(st.get("cwd") or ""), "subs": subagents(st.get("_sid")),
            })
    out.sort(key=lambda a: (a["kind"] == "chat", not a["asks"], a["mins"]))
    return out


def project_label(cwd):
    p = (cwd or "").rstrip("/").split("/")
    if len(p) >= 2 and p[-2].endswith("-wt"):
        return f"{p[-2][:-3]}#{p[-1]}"
    return p[-1] if p else ""


def short(s, n):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def label(a, width=46):
    """`#240 Agregar las reglas de DDD…` (o el proyecto si no hay número; el nombre si es un chat)."""
    if a["kind"] == "chat":
        return short(a["title"] or "(sin nombre: /rename o bk name)", width)
    n = a["num"] or a["ref"]
    t = a["title"] or a["project"]
    pr = f" (PR #{a['pr']})" if a.get("pr") and a["pr"] != n else ""
    return short((f"#{n}{pr} " if n else "") + t, width)


def line(a, i=None, color=True):
    mark = "?" if a["asks"] else ("●" if a["state"] == "working" else "○")
    if a["asks"]:
        status = a["why"] + (f": «{short(a['question'], 60)}»" if a["question"] else "")
    else:
        status = {"working": "trabajando", "background": "en segundo plano", "done": "listo",
                  "idle": "libre"}.get(a["state"], a["state"]) + f" {a['mins']}m" if a["state"] else "sin estado"
        if a.get("note"):   # la tarea ya cerró: eso es lo que importa, no el estado del pane
            status = a["note"]
    if a.get("queued"):
        status += f" · {a['queued']} en cola"
    num = f"{i:>2}  " if i is not None else ""
    name = a["short"] if a["kind"] == "chat" else a["win"]
    if len(name) > 14:   # el final distingue: barrilete-workspace-2 → …workspace-2
        name = "…" + name[-13:]
    if a["kind"] == "chat" and not a["asks"]:
        mark = "◇"
    if not color:
        return f"{num}{mark} {name:<14} {label(a):<46}  {status}"
    col = AMBER if a["asks"] else (GREEN if a["state"] == "working" else DIM)
    return f"{BOLD}{num}{R}{col}{mark}{R} {BOLD}{name:<14}{R} {label(a):<46}  {col}{status}{R}"


def sub_lines(a, color=True):
    """Los subagentes vivos de un agente o chat, debajo de él (no se les escribe: bk sub <palabra> los sigue)."""
    return [(f"  {DIM}└ {short(d, 60)}{R}" if color else f"  └ {short(d, 60)}") for d, _ in a.get("subs", [])]


# ---------- elegir ----------
def match(agents, q):
    """Agentes que corresponden a <q>: nombre exacto, número (de la ventana o del issue) o palabra."""
    exact = [a for a in agents if q in (a["win"], a.get("ws"), a.get("short"))]
    if exact:
        return exact
    n = q.lstrip("#")
    if n.isdigit():
        return [a for a in agents if n in (a["num"], a["ref"], a["pr"])]
    f = fold(q)
    return [a for a in agents if any(f in fold(a.get(k) or "") for k in ("title", "win", "project", "question"))]


def prefer_asking(cands):
    asking = [a for a in cands if a["asks"]]
    return asking if len(asking) == 1 else cands


def tty_ok():
    """Hay alguien en una terminal para elegir (no desde `!` en un chat, ni en un script)."""
    try:
        return os.isatty(sys.stdin.fileno()) and os.isatty(sys.stderr.fileno())
    except (OSError, ValueError):
        return False


def pick(agents, prompt="¿a quién?"):
    """Selector simple en /dev/tty: número o texto para filtrar. Devuelve el agente o None."""
    try:   # lectura y escritura por separado: "r+" en texto falla en una tty (no es seekable)
        tin, tty = open("/dev/tty"), open("/dev/tty", "w")
    except OSError:
        return None
    shown = agents
    while True:
        tty.write("\n")
        for i, a in enumerate(shown, 1):
            tty.write(line(a, i, color=True) + "\n")
        tty.write(f"\n{prompt} número o palabra (enter cancela) › ")
        tty.flush()
        ans = tin.readline().strip()
        if not ans:
            return None
        # 1–2 cifras: el número de la lista; más largo (218, #240): el issue o PR
        if ans.isdigit() and len(ans) <= 2 and 1 <= int(ans) <= len(shown):
            return shown[int(ans) - 1]
        got = match(agents, ans)
        if len(got) == 1:
            return got[0]
        shown = got or agents
        if not got:
            tty.write(f"nada con «{ans}»\n")


def explain(cands, why, hint):
    sys.stderr.write(f"bk: {why}\n")
    for a in cands:
        sys.stderr.write("  " + line(a, color=False) + "\n")
    sys.stderr.write(hint + "\n")


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "list"

    if cmd == "list":
        agents = gather()
        if not agents:
            print("sin agentes")
            return
        tty = sys.stdout.isatty()
        shown_chat = False
        for a in agents:
            if a["kind"] == "chat" and not shown_chat:
                print(f"{DIM}chats{R}" if tty else "chats")
                shown_chat = True
            print(line(a, color=tty))
            for l in sub_lines(a, tty):
                print(l)
        return

    if cmd == "subs":   # bk sub <palabra>: el transcript del subagente que coincide
        q = fold(" ".join(args[1:]))
        found = [(d, j) for a in gather(fetch_titles=False, look=False) for d, j in a["subs"] if q in fold(d)]
        if len(found) == 1:
            print(found[0][1])
            return
        sys.stderr.write(("bk: «%s» corresponde a %d subagentes:\n" % (q, len(found)) if found else
                          "bk: no hay un subagente vivo con «%s». Los vivos:\n" % q))
        for a in gather(fetch_titles=False, look=False):
            for d, _ in (found and [(d, j) for d, j in a["subs"] if (d, j) in found]) or a["subs"]:
                sys.stderr.write(f"  └ {short(d, 70)}   (de {a.get('short') or a['win']})\n")
        sys.exit(3 if found else 4)

    if cmd == "json":   # para Sketchybar: {pane: {...}} sin consultar GitHub (solo la cache)
        print(json.dumps({a["pane"]: {"win": a.get("short") if a["kind"] == "chat" else a["win"], "kind": a["kind"],
                                      "label": label(a, 60), "asks": a["asks"], "why": a["why"],
                                      "question": short(a["question"], 70),
                                      "subs": [short(d, 60) for d, _ in a["subs"]],
                                      "task": a.get("task", ""), "note": a.get("note", ""), "queued": a.get("queued", 0)}
                          for a in gather(fetch_titles=False)},
                         ensure_ascii=False))
        return

    if cmd == "warm":   # llenar la cache de títulos (lo corre bk después de lanzar un agente)
        gather(fetch_titles=True, look=False)
        return

    if cmd == "waiting":
        agents = [a for a in gather() if a["asks"]]
        if "--json" in args:
            print(json.dumps([{k: a[k] for k in ("win", "why", "question", "role", "num", "ref", "title", "project")}
                              for a in agents], ensure_ascii=False))
        else:
            for a in agents:
                print(line(a, color=sys.stdout.isatty()))
        return

    agents = gather()
    if not agents:
        sys.stderr.write("bk: no hay agentes (bk ls)\n")
        sys.exit(4)

    if cmd == "pick":
        a = pick(agents)
        if not a:
            sys.exit(4)
        print(a["win"])
        return

    if cmd == "auto":   # solo agentes: un chat tuyo no se elige solo
        agents = [a for a in agents if a["kind"] != "chat"]
        asking = [a for a in agents if a["asks"]]
        if len(asking) == 1:
            print(asking[0]["win"])
            return
        if tty_ok():
            a = pick(asking or agents, "¿a quién le escribís?")
            if a:
                print(a["win"])
                return
            sys.exit(4)
        if not asking:
            explain(agents, "ningún agente te está esperando; decí a cuál:", 'bk say <nombre, número o palabra> "mensaje"')
        else:
            explain(asking, f"te esperan {len(asking)}; decí a cuál:", 'bk say <nombre, número o palabra> "mensaje"')
        sys.exit(3)

    if cmd == "resolve":
        q = " ".join(args[1:]).strip()
        cands = prefer_asking(match(agents, q)) if q else []
        if len(cands) == 1:
            print(cands[0]["win"])
            return
        if tty_ok():
            a = pick(cands or agents, f"«{q}» no alcanza:" if cands else f"no encontré «{q}»:")
            if a:
                print(a["win"])
                return
            sys.exit(4)
        if cands:
            explain(cands, f"«{q}» corresponde a {len(cands)} agentes:", "usá el nombre de la ventana (la primera columna)")
            sys.exit(3)
        explain(agents, f"no encontré un agente para «{q}». Los que hay:", "")
        sys.exit(4)

    sys.stderr.write(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    main()
