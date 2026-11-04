#!/usr/bin/env python3
"""Checkpoints: lo que un agente no puede hacer por vos y te está esperando. Una sola fuente para el monitor
(Te esperan), la barra y `bk checkpoints`.

  plan       un planner mostró su borrador (planner.py --draft) y espera tu "sí"
  merge      un PR tuyo listo: CI en verde, con review sin bloqueantes y sin commits después de la review
  review     un PR que te pidieron revisar
  lesson     bk learn propone una lección (status: proposed en el vault)
  reviewer   un implementer cerró con `bk done pr <n>` y bk propone su reviewer (ADR-005 D6)

  checkpoints.py [--json] [--project <ruta>] [--no-refresh]

Los PRs salen de la caché que escribe `bk _prs` (GitHub, cada ~2 min, en segundo plano); el resto es local.
Sin --project, de todos los proyectos. Nunca falla por una fuente rota: la saltea.
"""
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import agents as AG   # noqa: E402
import tasks as TK    # noqa: E402

PRS = os.environ.get("BK_PRS_CACHE") or os.path.expanduser("~/.cache/bk/checkpoints_prs.json")
PRS_TTL = 120
LESSONS = os.path.join(os.path.expanduser(os.environ.get("BARRILETE_VAULT", "~/Documents/Obsidian Vault")),
                       "agents", "lessons")

# qué es y qué hacés, para cada tipo
KINDS = {
    "plan": ("plan por aprobar", "contestá en su chat"),
    "merge": ("PR listo para mergear", "gh pr merge {number} --squash --repo {repo}"),
    "review": ("te pidieron una review", "bk review {number}"),
    "lesson": ("lección propuesta", "aceptala o descartala en el vault: agents/lessons/"),
    "reviewer": ("reviewer propuesto", "bk approve {id}"),
}


_asked = [0.0]   # cuándo se pidió la última actualización de PRs (en este proceso)


def refresh_prs():
    """Si la caché de PRs es vieja, la pide de nuevo en segundo plano (no espera a GitHub)."""
    try:
        age = time.time() - os.path.getmtime(PRS)
    except OSError:
        age = 1e9
    # una vez cada PRS_TTL como mucho: sin red la caché no se renueva y el monitor llama cada 2 s
    if age > PRS_TTL and time.time() - _asked[0] > PRS_TTL and not os.environ.get("BK_CHECKPOINTS_OFFLINE"):
        _asked[0] = time.time()
        try:
            subprocess.Popen([os.path.join(os.path.dirname(HERE), "bin", "bk"), "_prs"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass


def repo_name(path):
    """owner/repo del origin de una carpeta, o su nombre si no tiene."""
    url = AG.sh("git", "-C", path, "remote", "get-url", "origin").strip() if path else ""
    if not url:
        return os.path.basename((path or "").rstrip("/"))
    return url.rstrip("/").removesuffix(".git").split(":")[-1].split("github.com/")[-1]


def plans(project=None):
    out = []
    for st in AG.states().values():
        if st.get("phase") == "plan_pending" and st.get("state") in ("done", "idle"):
            cwd = st.get("cwd") or ""
            if project and not os.path.realpath(cwd).startswith(os.path.realpath(project)):
                continue
            out.append({"kind": "plan", "title": AG.session_title(st.get("_sid")) or "plan", "where": AG.project_label(cwd),
                        "cwd": cwd, "pane": st.get("pane", ""), "ts": st.get("ts", 0)})
    return out


def prs(project=None):
    try:
        with open(PRS) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    mine = repo_name(project).split("/")[-1] if project else None
    out = []
    for p in data if isinstance(data, list) else []:
        repo = p.get("repo", "")
        if mine and repo.split("/")[-1] != mine:
            continue
        out.append({"kind": p.get("kind", "merge"), "title": p.get("title", ""), "where": repo.split("/")[-1],
                    "repo": repo, "number": p.get("number"), "url": p.get("url", "")})
    return out


def lessons():
    out = []
    for f in sorted(glob.glob(os.path.join(LESSONS, "*.md"))):
        try:
            with open(f, encoding="utf-8") as fh:
                head = fh.read(600)
        except OSError:
            continue
        if "status: proposed" in head:
            title = next((l[2:] for l in head.splitlines() if l.startswith("# ")), os.path.basename(f)[:-3])
            scope = next((l.split(":", 1)[1].split("#")[0].strip() for l in head.splitlines() if l.startswith("scope:")), "")
            out.append({"kind": "lesson", "title": title, "where": scope or "global", "path": f})
    return out


def reviewers(project=None):
    try:
        ts = TK.tasks()
    except Exception:
        return []
    out = []
    for t in ts:
        if t.get("status") != "pending":
            continue
        if project and t.get("repo") and os.path.realpath(t["repo"]) != os.path.realpath(project):
            continue
        out.append({"kind": "reviewer", "title": f"de {t.get('from', '-')}", "where": t.get("project", ""),
                    "id": t["id"], "pr": t.get("pr")})
    return out


def items(project=None, refresh=True):
    """Todos los checkpoints, en el orden en que conviene atenderlos."""
    if refresh:
        refresh_prs()
    out = []
    for source in (lambda: plans(project), lambda: reviewers(project), lambda: prs(project), lessons):
        try:
            out += source()
        except Exception:   # una fuente rota no tapa a las demás
            continue
    order = {"plan": 0, "reviewer": 1, "review": 2, "merge": 3, "lesson": 4}
    out.sort(key=lambda c: order.get(c["kind"], 9))
    for c in out:
        what, todo = KINDS.get(c["kind"], (c["kind"], ""))
        c["what"] = what
        try:
            c["todo"] = todo.format(**c)
        except (KeyError, IndexError):
            c["todo"] = todo
    return out


def main(argv):
    project, refresh = None, True
    if "--project" in argv:
        i = argv.index("--project")
        if i + 1 >= len(argv):
            print("bk: checkpoints --project <ruta>", file=sys.stderr)
            return 64
        project = argv[i + 1]
    if "--no-refresh" in argv:
        refresh = False
    cs = items(project, refresh)
    if "--json" in argv:
        print(json.dumps(cs, ensure_ascii=False))
        return 0
    if not cs:
        print("nada te espera")
        return 0
    for c in cs:
        num = f"#{c['number']} " if c.get("number") else ""
        print(f"◆ {c['what']}: {num}{c['title']}  ({c['where']})\n    → {c['todo']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
