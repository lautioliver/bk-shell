#!/usr/bin/env python3
"""Resumen para seguir en un chat nuevo (handoff): siempre el mismo formato, en un archivo.

Cuando un chat carga mucho contexto (el aviso rojo de context_hook.py) o se lo pedís a mano (bk handoff), el agente
escribe ${XDG_STATE_HOME:-~/.local/state}/bk/handoffs/<session_id>.md con la plantilla de abajo.
`bk chat --desde <ws|sesión>` (o "Seguir desde ws N" en alt+n) abre un workspace nuevo con la misma cuenta y
carpeta, le manda ese archivo como primer mensaje y lo borra.

Al lado del .md queda <session_id>.json con de dónde viene (workspace, número, carpeta, rama y cuenta). Lo escribe
bk al pedir el resumen, no el agente: así `--desde 5` encuentra el archivo y la cuenta sin depender de lo que el
agente haya copiado. BK_HANDOFF_DIR cambia la carpeta (los tests).

  handoff.py pedido [--sid S] [--pane P] [--cwd D]   el pedido al agente, con la plantilla (y deja el .json)
  handoff.py find <ws|sesión|session_id>            "sid<TAB>carpeta<TAB>cuenta<TAB>sesión" del resumen listo
  handoff.py take <session_id>                      imprime el resumen y lo borra (.md y .json)
  handoff.py list                                   los resúmenes listos: "sid<TAB>ws<TAB>sesión<TAB>nombre"

La tira (sidebar.py) y bk s (sessions.py) marcan con ↪ el chat cuya sesión dejó un resumen listo.
"""
import glob, json, os, subprocess, sys, time

MAX_LINES = 40
STALE = 14 * 86400   # un .json sin resumen, después de dos semanas, se borra


def folder():
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.environ.get("BK_HANDOFF_DIR") or os.path.join(state, "bk", "handoffs")


def md_path(sid):
    return os.path.join(folder(), os.path.basename(sid) + ".md")


def meta_path(sid):
    return os.path.join(folder(), os.path.basename(sid) + ".json")


def sh(*a):
    try:
        r = subprocess.run(a, capture_output=True, text=True, timeout=2)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def origin(sid, pane=None, cwd=None):
    """De dónde viene: lo que bk sabe de la sesión (tmux, git, cuenta), sin preguntarle al agente."""
    pane = pane or os.environ.get("TMUX_PANE", "")
    session = sh("tmux", "display-message", "-p", "-t", pane, "#{session_name}") if pane else ""
    # La carpeta del chat (donde arrancó claude), no la del Bash del agente, que puede haber hecho cd
    cwd = cwd or (sh("tmux", "display-message", "-p", "-t", pane, "#{pane_current_path}") if pane else "") or os.getcwd()
    ws = name = account = ""
    if session.startswith("bk"):
        ws = sh("tmux", "show-options", "-qv", "-t", f"={session}:", "@bk_id")
        name = sh("tmux", "show-options", "-qv", "-t", f"={session}:", "@bk_name")
        account = sh("tmux", "show-options", "-qv", "-t", f"={session}:", "@bk_account_claude")
    account = account or os.environ.get("BK_ACCOUNT_CLAUDE", "") or "default"
    top = sh("git", "-C", cwd, "rev-parse", "--show-toplevel") or cwd
    branch = sh("git", "-C", cwd, "branch", "--show-current")
    return {"sid": sid, "session": session, "ws": ws, "name": name, "cwd": top, "branch": branch,
            "account": account, "ts": int(time.time())}


def origin_line(o):
    where = f"ws {o['ws']} ({o['session']})" if o.get("ws") else (o.get("session") or "fuera de un workspace")
    if o.get("name"):
        where += f" «{o['name']}»"
    branch = f"rama {o['branch']} en {o['cwd']}" if o.get("branch") else o["cwd"]
    return f"De dónde: sesión {o['sid']} · {where} · {branch} · cuenta {o['account']}"


def template(o):
    return f"""# Seguir desde {f"ws {o['ws']}" if o.get('ws') else o['sid'][:8]}: <en qué andabas, en pocas palabras>
{origin_line(o)}

## Hecho
- <qué quedó listo, con su commit, PR o issue>

## Falta (en orden)
1. Primer paso: <lo próximo, concreto: el archivo, el comando o la prueba>
2. <…>

## Te espera a vos
- <aprobaciones, merges, `bk approve …`; "nada" si no hay>

## No tocar
- <lo que lleva otra sesión o un agente, y quién; "nada" si no hay>

## Archivos y comandos
- <rutas y comandos clave para retomar>"""


def pedido(o):
    path = md_path(o["sid"]).replace(os.path.expanduser("~"), "~", 1)
    return (f"Dejá el resumen para seguir en un chat nuevo: escribilo en {path} (con Write), con esta plantilla. "
            f"Las seis secciones siempre, en este orden; reemplazá lo que está entre <> y no cambies la línea "
            f"«De dónde». Viñetas cortas, como mucho {MAX_LINES} líneas: es el primer mensaje del chat nuevo, que no "
            f"sabe nada de esta conversación.\n\n{template(o)}\n\n"
            f"Después decile a la persona que siga con `bk chat --desde {o.get('ws') or o['sid']}` "
            f"(o «Seguir desde» en alt+n), que abre el chat nuevo con este resumen.")


def save_meta(o):
    os.makedirs(folder(), exist_ok=True)
    tmp = meta_path(o["sid"]) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(o, f, ensure_ascii=False)
    os.replace(tmp, meta_path(o["sid"]))


def request(sid, pane=None, cwd=None):
    """El pedido al agente; deja el .json con de dónde viene (si no puede, el pedido igual sale)."""
    o = origin(sid, pane, cwd)
    try:
        save_meta(o)
    except OSError:
        pass
    return pedido(o)


def load_meta(sid):
    try:
        with open(meta_path(sid)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def ready():
    """Los resúmenes escritos, el más nuevo primero: [(mtime, meta)]. Borra los .json viejos sin resumen."""
    out = []
    for m in glob.glob(os.path.join(folder(), "*.json")):
        sid = os.path.basename(m)[:-5]
        md = md_path(sid)
        if not os.path.exists(md):
            try:
                if time.time() - os.path.getmtime(m) > STALE:
                    os.remove(m)
            except OSError:
                pass
            continue
        if os.path.getsize(md) == 0:
            continue
        meta = load_meta(sid) or {"sid": sid}
        meta["sid"] = sid
        out.append((os.path.getmtime(md), meta))
    return sorted(out, key=lambda x: -x[0])


def ready_sids():
    """Las sesiones de Claude que dejaron un resumen listo (la tira y bk s las marcan con ↪)."""
    try:
        return {m["sid"] for _, m in ready()}
    except OSError:
        return set()


def find(q):
    q = q.strip().lstrip("#")
    if q.startswith("ws"):
        q = q[2:].strip()
    for _, m in ready():
        if q and (q in (m.get("sid"), m.get("session"), str(m.get("ws") or "")) or (len(q) >= 8 and m["sid"].startswith(q))):
            return m
    return None


def take(sid):
    try:
        with open(md_path(sid)) as f:
            text = f.read()
    except OSError:
        return None
    for p in (md_path(sid), meta_path(sid)):
        try:
            os.remove(p)
        except OSError:
            pass
    return text


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "pedido":
        opts = dict(zip(argv[1::2], argv[2::2]))
        sid = opts.get("--sid")
        if not sid:
            sys.exit("bk: no sé qué sesión es esta (falta --sid)")
        print(request(sid, opts.get("--pane"), opts.get("--cwd")))
    elif cmd == "find" and len(argv) > 1:
        m = find(argv[1])
        if not m:
            sys.exit(1)
        print("\t".join([m["sid"], m.get("cwd") or "", m.get("account") or "default", m.get("session") or ""]))
    elif cmd == "take" and len(argv) > 1:
        text = take(argv[1])
        if text is None:
            sys.exit(1)
        sys.stdout.write(text)
    elif cmd == "list":
        for _, m in ready():
            print("\t".join([m["sid"], str(m.get("ws") or ""), m.get("session") or "", m.get("name") or ""]))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
