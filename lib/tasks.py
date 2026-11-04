#!/usr/bin/env python3
"""Tareas de los agentes de bk (ADR-005 de Barrilete Kit): estado en task.json, hechos en events.jsonl.

Cada agente que lanza `bk run`, `bk review` o `bk fix` trabaja sobre una tarea con id estable
(<issue>-<rol> o pr<n>-<rol>) en  $BK_TASKS_DIR/<proyecto>/<id>/  (por defecto
${XDG_STATE_HOME:-~/.local/state}/bk/tasks). El agente recibe esa carpeta en BK_TASK_DIR.

  tasks.py create <proyecto> <id> --role R [--ref N] [--pr N] [--repo R] [--worktree W] [--window V] [--pending]
                                     crea la tarea; si ya hay una activa con ese id, la imprime y sale con 3
  tasks.py start <dir> [--window V]   pending -> in_progress (al lanzar el agente)
  tasks.py done <resultado> [arg]     el agente cierra su tarea (lee BK_TASK_DIR): pr <n> | blockers |
                                     dropped "<motivo>" | escalate "<motivo>"
  tasks.py ended <dir>               el proceso del agente terminó: incomplete si no declaró el cierre
  tasks.py cancel <dir>              la persona lo paró (bk stop): cancelled
  tasks.py event <dir> <tipo> [k=v]  agrega un evento (lo usan los hooks)
  tasks.py status <dir> <estado>     in_progress | waiting (estados no terminales; lo usan los hooks)
  tasks.py find --window V           carpeta de la tarea activa de esa ventana
  tasks.py state <proyecto> <id>     su estado (sale con 4 si no existe)
  tasks.py get <dir>                 el task.json
  tasks.py queue <dir> <mensaje>     encola un `bk say` para cuando el agente espere
  tasks.py drain <dir>               imprime los mensajes encolados (uno por línea, JSON) y vacía la cola
  tasks.py list [--json] [--all]     las tareas con su estado y su carpeta (sin --all, solo las de hace 7 días o activas)
  tasks.py prune [--days N] [--yes]  borra tareas terminadas (nunca activas); sin --yes solo muestra cuáles
  tasks.py root                      la carpeta raíz de las tareas

Reglas (ADR-005): un estado terminal no cambia nunca; seq crece sin huecos bajo el lock de la tarea
(flock sobre <dir>/.lock, el mismo que usan los hooks del kit); los eventos llevan metadatos, nunca contenido.
Errores de uso salen con 64.
"""
import fcntl
import json
import os
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone

TERMINAL = {"completed", "failed", "incomplete", "cancelled"}
ACTIVE = {"pending", "in_progress", "waiting"}
RESULTS = {"pr": "completed", "blockers": "completed", "dropped": "failed", "escalate": "failed"}
REVIEWED_BY = {"implementer", "implementer-light"}   # sus PRs proponen una tarea de reviewer (D6)


class Usage(Exception):
    pass


def root():
    if os.environ.get("BK_TASKS_DIR"):
        return os.environ["BK_TASKS_DIR"]
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(state, "bk", "tasks")


def now_iso():   # ISO 8601 en UTC con milisegundos: dos tareas del mismo segundo se ordenan igual
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def safe(name, what):
    if not name or "/" in name or name.startswith(".") or name != name.strip():
        raise Usage(f"{what} inválido: {name!r}")
    return name


@contextmanager
def locked(d):
    fd = os.open(os.path.join(d, ".lock"), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read(d):
    with open(os.path.join(d, "task.json")) as f:
        return json.load(f)


def write(d, task):
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".task.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(task, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, os.path.join(d, "task.json"))


def append_event(d, task, etype, **data):
    """Agrega un evento con el seq siguiente. Llamar con el lock tomado; devuelve el task actualizado."""
    seq = task.get("seq", 0) + 1
    ev = {"seq": seq, "ts": now_iso(), "type": etype}
    ev.update({k: v for k, v in data.items() if v is not None})
    with open(os.path.join(d, "events.jsonl"), "a") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    task["seq"] = seq
    task["updated"] = ev["ts"]
    return task


def task_dir(d):
    d = os.path.abspath(d)
    if not os.path.isfile(os.path.join(d, "task.json")):
        raise Usage(f"no hay una tarea en {d}")
    return d


def create(project, tid, role, ref=None, pr=None, worktree=None, window=None, pending=False, origin=None, repo=None):
    """Crea la tarea. Si existe una activa con ese id devuelve (dir, False); si existe terminada, la guarda
    aparte (<id>~<fecha>, D9: no se borra) y crea una nueva."""
    base = os.path.join(root(), safe(project, "proyecto"))
    d = os.path.join(base, safe(tid, "id"))
    os.makedirs(base, exist_ok=True)
    if os.path.isfile(os.path.join(d, "task.json")):
        with locked(d):
            old = read(d)
        if old.get("status") in ACTIVE:
            return d, False
        stamp = (old.get("closed") or now_iso()).replace(":", "")
        dest, n = f"{d}~{stamp}", 1
        while os.path.exists(dest):
            n += 1
            dest = f"{d}~{stamp}-{n}"
        os.rename(d, dest)
    os.makedirs(d, exist_ok=True)
    with locked(d):
        task = {"id": tid, "dir": d, "project": project, "role": role, "status": "pending" if pending else "in_progress",
                "created": now_iso(), "seq": 0}
        for k, v in (("ref", ref), ("pr", pr), ("repo", repo), ("worktree", worktree), ("window", window), ("from", origin)):
            if v:
                task[k] = int(v) if k == "pr" else v
        append_event(d, task, "task.created", role=role, status=task["status"])
        write(d, task)
    return d, True


def close(d, status, **result):
    """Cierra la tarea en un estado terminal. Si ya estaba terminada no la toca y devuelve None."""
    with locked(d):
        task = read(d)
        if task.get("status") in TERMINAL:
            return None
        task["status"] = status
        task["closed"] = now_iso()
        res = {k: v for k, v in result.items() if v is not None}
        if res:
            task["result"] = res
        if res.get("pr"):
            task["pr"] = res["pr"]
        append_event(d, task, f"task.{status}")
        # El evento terminal copia el task.json final (D5): se reescribe con el seq ya incluido
        with open(os.path.join(d, "events.jsonl")) as f:
            lines = f.read().splitlines()
        last = json.loads(lines[-1])
        last["task"] = task
        lines[-1] = json.dumps(last, ensure_ascii=False)
        with open(os.path.join(d, "events.jsonl"), "w") as f:
            f.write("\n".join(lines) + "\n")
        write(d, task)
    return task


def cmd_done(args):
    d = os.environ.get("BK_TASK_DIR")
    if not d:
        raise Usage("bk done: no hay BK_TASK_DIR (solo un agente lanzado por bk cierra su tarea)")
    d = task_dir(d)
    if not args or args[0] not in RESULTS:
        raise Usage("bk done pr <n> | blockers | dropped \"<motivo>\" | escalate \"<motivo>\"")
    kind, rest = args[0], args[1:]
    pr = reason = None
    if kind == "pr":
        if len(rest) != 1 or not rest[0].lstrip("#").isdigit():
            raise Usage("bk done pr <n>")
        pr = int(rest[0].lstrip("#"))
    elif kind in ("dropped", "escalate"):
        reason = " ".join(rest).strip()
        if not reason:
            raise Usage(f'bk done {kind} "<motivo>"')
    elif rest:
        raise Usage("bk done blockers no lleva argumentos")
    task = close(d, RESULTS[kind], kind=kind, pr=pr, reason=reason)
    if task is None:
        raise Usage(f"la tarea {read(d)['id']} ya estaba cerrada ({read(d)['status']})")
    print(f"{task['id']}: {task['status']} ({kind}{f' #{pr}' if pr else ''})")
    if kind == "pr" and task.get("role") in REVIEWED_BY:
        rd, new = create(task["project"], f"pr{pr}-reviewer", "reviewer", ref=f"#{pr}", pr=pr, pending=True,
                         origin=task["id"], repo=task.get("repo"))
        if new:
            print(f"propuesta: pr{pr}-reviewer espera tu aprobación → bk approve pr{pr}-reviewer")


def find(window):
    """La tarea activa de una ventana de tmux. La más nueva si hubiera varias."""
    best = None
    for d in all_dirs():
        try:
            t = read(d)
        except (OSError, ValueError):
            continue
        if t.get("status") not in ACTIVE or t.get("window") != window:
            continue
        k = ("~" not in os.path.basename(d), t.get("created", ""))
        if best is None or k > best[1]:
            best = (d, k)
    return best[0] if best else None


def all_dirs():
    r = root()
    if not os.path.isdir(r):
        return []
    out = []
    for p in sorted(os.listdir(r)):
        pd = os.path.join(r, p)
        if not os.path.isdir(pd):
            continue
        for t in sorted(os.listdir(pd)):
            d = os.path.join(pd, t)
            if os.path.isfile(os.path.join(d, "task.json")):
                out.append(d)
    return out


def tasks(all_=False):
    out = []
    week = time.time() - 7 * 86400
    for d in all_dirs():
        try:
            t = read(d)
        except (OSError, ValueError):
            continue
        t["dir"] = d   # la carpeta real (una tarea guardada aparte, <id>~<fecha>, se movió)
        if not all_ and t.get("status") in TERMINAL and os.path.getmtime(os.path.join(d, "task.json")) < week:
            continue
        out.append(t)
    order = {"waiting": 0, "pending": 1, "in_progress": 2}
    out.sort(key=lambda t: (order.get(t.get("status"), 3), t.get("updated", "")), reverse=False)
    return out


def describe(t):
    r = t.get("result") or {}
    res = r.get("kind", "")
    if r.get("pr"):
        res += f" #{r['pr']}"
    if r.get("reason"):
        res += f": {r['reason']}"
    return res


def cmd_list(args):
    ts = tasks(all_="--all" in args)
    if "--json" in args:
        print(json.dumps(ts, ensure_ascii=False))
        return
    if not ts:
        print(f"sin tareas (en {root()})")
        return
    rows = [("TAREA", "PROYECTO", "ESTADO", "RESULTADO", "CARPETA")]
    home = os.path.expanduser("~")
    for t in ts:
        d = t["dir"].replace(home, "~", 1)
        rows.append((os.path.basename(t["dir"]), t.get("project", ""), t.get("status", ""), describe(t) or "-", d))
    w = [max(len(r[i]) for r in rows) for i in range(4)]
    for r in rows:
        print("  ".join(c.ljust(w[i]) for i, c in enumerate(r[:4])) + "  " + r[4])


def cmd_prune(args):
    days = 0
    if "--days" in args:
        i = args.index("--days")
        try:
            days = int(args[i + 1])
        except (IndexError, ValueError):
            raise Usage("bk tasks prune [--days N] [--yes]")
    cutoff = time.time() - days * 86400
    victims = []
    for d in all_dirs():
        try:
            t = read(d)
        except (OSError, ValueError):
            continue
        if t.get("status") in TERMINAL and os.path.getmtime(os.path.join(d, "task.json")) <= cutoff:
            victims.append(d)
    if not victims:
        print("nada para borrar (las tareas activas nunca se borran)")
        return
    for d in victims:
        print(("borrada  " if "--yes" in args else "borraría ") + d)
        if "--yes" in args:
            shutil.rmtree(d)
    if "--yes" not in args:
        print(f"{len(victims)} tarea(s). Para borrarlas: bk tasks prune{' --days ' + str(days) if days else ''} --yes")


def main(argv):
    if not argv:
        raise Usage(__doc__.strip().splitlines()[0])
    cmd, args = argv[0], argv[1:]
    if cmd == "root":
        print(root())
    elif cmd == "create":
        if len(args) < 2:
            raise Usage("tasks.py create <proyecto> <id> --role R [...]")
        project, tid, opts = args[0], args[1], {}
        it = iter(args[2:])
        for a in it:
            if a == "--pending":
                opts["pending"] = True
            elif a in ("--role", "--ref", "--pr", "--repo", "--worktree", "--window"):
                opts[a[2:]] = next(it, None)
            else:
                raise Usage(f"opción desconocida: {a}")
        if not opts.get("role"):
            raise Usage("falta --role")
        d, new = create(project, tid, **opts)
        print(d)
        return 0 if new else 3
    elif cmd == "start":
        d = task_dir(args[0]) if args else None
        if not d:
            raise Usage("tasks.py start <dir> [--window V]")
        win = args[args.index("--window") + 1] if "--window" in args else None
        with locked(d):
            t = read(d)
            if t.get("status") != "pending":
                raise Usage(f"la tarea {t['id']} no está pendiente ({t['status']})")
            t["status"] = "in_progress"
            if win:
                t["window"] = win
            append_event(d, t, "task.approved")
            write(d, t)
    elif cmd == "done":
        cmd_done(args)
    elif cmd in ("ended", "cancel"):
        d = task_dir(args[0]) if args else None
        if not d:
            raise Usage(f"tasks.py {cmd} <dir>")
        t = close(d, "incomplete" if cmd == "ended" else "cancelled")
        if t:
            print(f"{t['id']}: {t['status']}")
    elif cmd == "event":
        if len(args) < 2:
            raise Usage("tasks.py event <dir> <tipo> [k=v ...]")
        d, etype = task_dir(args[0]), args[1]
        data = dict(kv.split("=", 1) for kv in args[2:] if "=" in kv)
        with locked(d):
            t = read(d)
            if t.get("status") in TERMINAL:
                return 0   # nada después del evento terminal
            append_event(d, t, etype, **data)
            write(d, t)
    elif cmd == "status":
        if len(args) != 2 or args[1] not in ("in_progress", "waiting"):
            raise Usage("tasks.py status <dir> in_progress|waiting")
        d = task_dir(args[0])
        etype = "turn.waiting" if args[1] == "waiting" else None
        with locked(d):
            t = read(d)
            if t.get("status") in TERMINAL or t.get("status") == args[1] or t.get("status") == "pending":
                return 0
            t["status"] = args[1]
            if etype:
                append_event(d, t, etype)
            write(d, t)
    elif cmd == "find":
        if len(args) != 2 or args[0] != "--window":
            raise Usage("tasks.py find --window V")
        d = find(args[1])
        if not d:
            return 4
        print(d)
    elif cmd == "state":   # estado de <proyecto>/<id>, o nada si no existe (sale con 4)
        if len(args) != 2:
            raise Usage("tasks.py state <proyecto> <id>")
        f = os.path.join(root(), safe(args[0], "proyecto"), safe(args[1], "id"), "task.json")
        if not os.path.isfile(f):
            return 4
        with open(f) as fh:
            print(json.load(fh).get("status", ""))
    elif cmd == "get":
        print(json.dumps(read(task_dir(args[0])), indent=2, ensure_ascii=False))
    elif cmd == "queue":
        if len(args) < 2:
            raise Usage("tasks.py queue <dir> <mensaje>")
        d = task_dir(args[0])
        with locked(d):
            with open(os.path.join(d, "queue.jsonl"), "a") as f:
                f.write(json.dumps({"ts": now_iso(), "msg": " ".join(args[1:])}, ensure_ascii=False) + "\n")
    elif cmd == "drain":
        d = task_dir(args[0])
        q = os.path.join(d, "queue.jsonl")
        with locked(d):
            if not os.path.exists(q):
                return 0
            with open(q) as f:
                lines = [ln for ln in f.read().splitlines() if ln.strip()]
            os.remove(q)
        for ln in lines:
            print(ln)
    elif cmd == "list":
        cmd_list(args)
    elif cmd == "prune":
        cmd_prune(args)
    else:
        raise Usage(f"comando desconocido: {cmd}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Usage as e:
        print(f"bk: {e}", file=sys.stderr)
        sys.exit(64)
