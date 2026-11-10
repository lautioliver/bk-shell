#!/usr/bin/env python3
"""Métricas de los agentes por PR: costo, rondas y desvíos, y si las lecciones los bajan.

  costo     tokens de todas las sesiones del PR (implementer, fixes, reviews y sus subagentes),
            en dólares equivalentes de API (precios de usage.py)
  rondas    reviews publicadas en GitHub (gh) y sesiones de agente que tocó el PR
  desvíos   eventos de `bk learn` (vault agents/data/events-*.jsonl) de esas sesiones
  lecciones compara PRs empezados antes y después de que se aceptó cada lección (vault agents/lessons)

Una sesión se asigna a un número por: el primer /implement-issue N o /review-pr N, o la rama
(<algo>/N-…). Los PRs se traen con `gh pr list` del remoto de cada repo (cache 30 min).

Uso: metrics.py [--json] [--repo NOMBRE] [--refresh]
Cache: ~/.cache/bk/metrics.json. Vault: BARRILETE_VAULT (BK_VAULT también vale, el nombre viejo).
"""
import glob
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from usage import cost  # noqa: E402  precios: una sola fuente
from deviations import VAULT_HELP, vault  # noqa: E402

PROJECTS = os.path.expanduser("~/.claude/projects")
CACHE = os.path.expanduser("~/.cache/bk/metrics.json")
VAULT = os.path.join(vault(), "agents") if vault() else None   # BARRILETE_VAULT (o BK_VAULT), sin valor por defecto
CMD = re.compile(r"^\s*/(?:[\w-]+:)?(implement-issue|review-pr)\s+#?(\d+)")
BRANCH = re.compile(r"^[^/]+/(\d+)(?:[-_]|$)")
GH_TTL = 1800


def load_cache():
    try:
        with open(CACHE) as f:
            c = json.load(f)
            if c.get("v") == 1:
                return c
    except (OSError, ValueError):
        pass
    return {"v": 1, "files": {}, "gh": {}}


def save_cache(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(c, f)
    os.replace(tmp, CACHE)


def repo_of(cwd):
    """Raíz del repo principal (los worktrees ../<repo>-wt/<n> y .worktrees/ cuentan como el repo)."""
    if not cwd:
        return None
    base, parent = os.path.basename(cwd), os.path.dirname(cwd)
    if os.path.basename(parent).endswith("-wt"):
        return os.path.join(os.path.dirname(parent), os.path.basename(parent)[:-3])
    m = re.match(r"(.*?)-wt-\d+$", cwd)
    if m:
        return m.group(1)
    if "/.worktrees/" in cwd:
        return cwd.split("/.worktrees/")[0]
    return cwd


def parse_file(path):
    """Resumen de un transcript: sesión, repo, número, rol, costo, inicio."""
    s = {"sid": None, "cwd": None, "n": None, "role": None, "branch_n": None,
         "start": None, "cost": 0.0, "tokens": 0}
    msgs = {}
    with open(path, errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            s["sid"] = s["sid"] or e.get("sessionId")
            if e.get("cwd") and not s["cwd"]:
                s["cwd"] = e["cwd"]
            if e.get("timestamp") and not s["start"]:
                s["start"] = e["timestamp"]
            b = e.get("gitBranch")
            if b and not s["branch_n"]:
                m = BRANCH.match(b)
                if m:
                    s["branch_n"] = int(m.group(1))
            if e.get("type") == "user" and not s["role"] and not e.get("isSidechain"):
                c = (e.get("message") or {}).get("content")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                m = CMD.match(c or "") or re.search(r"<command-name>/(implement-issue|review-pr)</command-name>.*?<command-args>#?(\d+)", c or "", re.S)
                if m:
                    s["role"] = "review" if m.group(1) == "review-pr" else "implement"
                    s["n"] = int(m.group(2))
            m = e.get("message") or {}
            u, mid = m.get("usage"), m.get("id")
            if e.get("type") != "assistant" or not u or not mid or m.get("model") == "<synthetic>":
                continue
            cc = u.get("cache_creation") or {}
            cw1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
            cw = u.get("cache_creation_input_tokens", 0) or 0
            row = {"in": u.get("input_tokens", 0) or 0, "out": u.get("output_tokens", 0) or 0,
                   "cw5": max(cw - cw1h, 0), "cw1h": cw1h, "cr": u.get("cache_read_input_tokens", 0) or 0}
            prev = msgs.get(mid)
            if prev is None or row["out"] >= prev[1]["out"]:
                msgs[mid] = (m.get("model") or "?", row)
    for model, u in msgs.values():
        s["cost"] += cost(model, u) or 0.0
        s["tokens"] += u["in"] + u["out"] + u["cw5"] + u["cw1h"]
    return s


def scan(cache):
    """Sesiones de agente con número (implementer, fix, review). Los subagentes suman a su sesión."""
    files = cache["files"]
    seen = set()
    for path in glob.glob(f"{PROJECTS}/*/*.jsonl") + glob.glob(f"{PROJECTS}/*/*/subagents/*.jsonl"):
        seen.add(path)
        try:
            mt = os.path.getmtime(path)
        except OSError:
            continue
        if path in files and files[path]["mt"] == mt:
            continue
        try:
            files[path] = {"mt": mt, "s": parse_file(path)}
        except OSError:
            pass
    for p in list(files):
        if p not in seen:
            del files[p]
    sessions = {}
    subs = defaultdict(lambda: [0.0, 0])
    for path, v in files.items():
        s = v["s"]
        if "/subagents/" in path:
            parent = os.path.basename(os.path.dirname(os.path.dirname(path)))
            subs[parent][0] += s["cost"]; subs[parent][1] += s["tokens"]
            continue
        if not s["sid"] or not s["cwd"]:
            continue
        n = s["n"] or s["branch_n"]
        if not n:
            continue
        role = s["role"] or ("fix" if s["branch_n"] else None)
        sessions[s["sid"]] = dict(s, n=n, role=role, repo=repo_of(s["cwd"]))
    for sid, (c, t) in subs.items():
        if sid in sessions:
            sessions[sid]["cost"] += c; sessions[sid]["tokens"] += t
    return sessions


def slug_of(path, cache):
    """owner/repo del remoto de GitHub (los workspaces de Orca tienen nombres de animales)."""
    sl = cache.setdefault("slug", {})
    if path not in sl:
        try:
            url = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            url = ""
        m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
        sl[path] = m.group(1) if m else None
    return sl[path]


# Bloqueantes de una review: el mismo parser que usa bk learn (lib/deviations.py), así bk metrics,
# bk learn y bk learn effect cuentan igual.
from deviations import review_blockers  # noqa: E402,F401


def gh_prs(slug, cache, refresh=False):
    hit = cache["gh"].get(slug)
    fresh = hit and all("blockers" in p for p in hit["prs"])  # cache de antes de contar bloqueantes: se rehace
    if fresh and not refresh and time.time() - hit["t"] < GH_TTL:
        return hit["prs"]
    try:
        out = subprocess.run(["gh", "pr", "list", "--repo", slug, "--state", "all", "--limit", "200",
                              "--json", "number,title,headRefName,state,createdAt,mergedAt,closingIssuesReferences,reviews"],
                             capture_output=True, text=True, timeout=60)
        raw = json.loads(out.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return hit["prs"] if hit else []
    prs = [{"n": p["number"], "title": p["title"], "branch": p["headRefName"], "state": p["state"],
            "created": p["createdAt"], "merged": p.get("mergedAt"),
            "issues": [i["number"] for i in p.get("closingIssuesReferences") or []],
            "reviews": len([r for r in p.get("reviews") or [] if r.get("state") != "PENDING"]),
            # uno por review (ronda), en orden; None = el cuerpo no habla de bloqueantes
            "blockers": [review_blockers(r.get("body")) for r in p.get("reviews") or [] if r.get("state") != "PENDING"]}
           for p in raw]
    cache["gh"][slug] = {"t": time.time(), "prs": prs}
    return prs


def load_events():
    ev = defaultdict(list)
    for path in glob.glob(f"{VAULT}/data/events-*.jsonl"):
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                ev[e.get("session", "")[:8]].append(e)
    return ev


def load_lessons():
    out = []
    for path in glob.glob(f"{VAULT}/lessons/*.md"):
        meta = {}
        with open(path, errors="replace") as f:
            txt = f.read()
        m = re.match(r"---\n(.*?)\n---", txt, re.S)
        for line in (m.group(1) if m else "").splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.split("#")[0].strip().strip('"')
        if meta.get("status") == "accepted" and meta.get("accepted"):
            out.append({"name": os.path.basename(path)[:-3], "accepted": meta["accepted"]})
    return out


def build(repo_filter=None, refresh=False):
    cache = load_cache()
    sessions = scan(cache)
    events = load_events()
    by_repo = defaultdict(list)
    proj_slug = {}
    for s in sessions.values():
        slug = slug_of(s["repo"], cache) or slug_of(s["cwd"], cache)
        if slug:
            by_repo[slug].append(s)
            proj_slug[os.path.basename(s["cwd"])] = slug
            proj_slug[os.path.basename(s["repo"])] = slug
    # Bloqueantes/escaladas del contrato: los registra el orquestador, no la sesión del PR → por "issue N"
    contract = defaultdict(list)
    for evs in events.values():
        for e in evs:
            m = re.match(r"issue (\d+):", e.get("detail", ""))
            if not (e.get("type", "").startswith("contract_") and m):
                continue
            pj = e.get("project", "")
            if pj and pj not in proj_slug:  # workspace de Orca sin sesión numerada: buscarlo en disco
                hit = glob.glob(os.path.expanduser(f"~/orca/workspaces/*/{pj}")) + [os.path.expanduser(f"~/{pj}")]
                proj_slug[pj] = next((x for x in (slug_of(h, cache) for h in hit if os.path.isdir(h)) if x), None)
            if proj_slug.get(pj):
                contract[(proj_slug[pj], int(m.group(1)))].append(e)
    rows = []
    for slug, ss in by_repo.items():
        name = slug.split("/")[1]
        if repo_filter and name != repo_filter:
            continue
        prs = gh_prs(slug, cache, refresh)
        if not prs:
            continue
        issue_pr = {}
        for p in prs:
            for i in p["issues"]:
                issue_pr.setdefault(i, p["n"])
            m = BRANCH.match(p["branch"])
            if m:
                issue_pr.setdefault(int(m.group(1)), p["n"])
        pr_nums = {p["n"] for p in prs}
        groups = defaultdict(list)
        for s in ss:
            if s["role"] == "review":
                pr = s["n"] if s["n"] in pr_nums else issue_pr.get(s["n"])
            else:
                pr = issue_pr.get(s["n"]) or (s["n"] if s["n"] in pr_nums else None)
            if pr:
                groups[pr].append(s)
        for p in prs:
            g = groups.get(p["n"])
            if not g:
                continue
            ev = [e for s in g for e in events.get(s["sid"][:8], []) if not e.get("type", "").startswith("contract_")]
            ev += [e for i in set(p["issues"]) | {s["n"] for s in g} for e in contract.get((slug, i), [])]
            start = min(s["start"] for s in g if s["start"])
            rows.append({
                "repo": name, "pr": p["n"], "title": p["title"], "state": p["state"],
                "start": start, "merged": p["merged"],
                "cost": round(sum(s["cost"] for s in g), 2),
                # costo por rol: las sesiones /review-pr son del reviewer; el resto, del implementer
                "cost_review": round(sum(s["cost"] for s in g if s["role"] == "review"), 2),
                "cost_impl": round(sum(s["cost"] for s in g if s["role"] != "review"), 2),
                "tokens": sum(s["tokens"] for s in g),
                "reviews": p["reviews"],
                "rev_sessions": sum(1 for s in g if s["role"] == "review"),
                "impl_sessions": sum(1 for s in g if s["role"] != "review"),
                "deviations": len(ev),
                # bloq = TOTAL de bloqueantes sumando todas las reviews publicadas en GitHub, la misma
                # fuente que "en GH" (rondas): un bloqueante que sigue abierto y se repite en la ronda
                # siguiente cuenta de nuevo, porque costó otra ronda. Antes salía de los eventos
                # contract_blockers del vault, que solo deja /orchestrate, cuentan rondas (no bloqueantes)
                # y van por issue: casi nunca caían en un PR de esta tabla → siempre 0.
                "blockers": sum(b or 0 for b in p.get("blockers") or []),
                "blockers_unparsed": sum(1 for b in p.get("blockers") or [] if b is None),
            })
    save_cache(cache)
    rows.sort(key=lambda r: r["start"])
    return rows, lessons_effect(rows, load_lessons())


def lessons_effect(rows, lessons):
    """Por fecha de aceptación: promedio por PR antes y después (PRs empezados en cada lado)."""
    out = []
    for day in sorted({l["accepted"] for l in lessons}):
        names = [l["name"] for l in lessons if l["accepted"] == day]
        side = {"antes": [r for r in rows if r["start"][:10] < day],
                "después": [r for r in rows if r["start"][:10] >= day]}
        stat = {}
        for k, rs in side.items():
            n = len(rs) or 1
            stat[k] = {"prs": len(rs),
                       "rondas": round(sum(max(r["reviews"], r["rev_sessions"]) for r in rs) / n, 2),
                       "bloqueantes": round(sum(r["blockers"] for r in rs) / n, 2),
                       "desvíos": round(sum(r["deviations"] for r in rs) / n, 2),
                       "costo": round(sum(r["cost"] for r in rs) / n, 2)}
        out.append({"day": day, "lessons": names, **stat})
    return out


def show(rows, effect):
    G, M, B, R = "\033[32m", "\033[2m", "\033[1m", "\033[0m"
    if not rows:
        print("sin PRs con sesiones de agente"); return
    print(f"{B}{'repo':<20}{'PR':>5}  {'estado':<7}{'costo':>9}{'impl $':>9}{'review $':>10}{'rondas':>8}{'en GH':>7}{'impl':>6}{'desv':>6}{'bloq':>6}  título{R}")
    for r in rows:
        st = {"MERGED": "merged", "OPEN": "abierto", "CLOSED": "cerrado"}.get(r["state"], r["state"])
        print(f"{r['repo'][:19]:<20}{r['pr']:>5}  {st:<7}{'$' + format(r['cost'], ',.2f'):>9}"
              f"{'$' + format(r.get('cost_impl', 0), ',.2f'):>9}{'$' + format(r.get('cost_review', 0), ',.2f'):>10}"
              f"{max(r['reviews'], r['rev_sessions']):>8}{r['reviews']:>7}{r['impl_sessions']:>6}{r['deviations']:>6}{r['blockers']:>6}"
              f"  {M}{r['title'][:40]}{R}")
    tot = sum(r["cost"] for r in rows)
    print(f"{M}{len(rows)} PRs · ${tot:,.2f} · promedio ${tot / len(rows):,.2f} por PR · "
          f"rondas = máx(reviews en GitHub, sesiones /review-pr) · bloq = bloqueantes sumados de todas las reviews en GitHub{R}")
    ti, tr = sum(r.get("cost_impl", 0) for r in rows), sum(r.get("cost_review", 0) for r in rows)
    if tot:
        print(f"{M}por rol: implementer ${ti:,.2f} ({ti / tot:.0%}) · reviewer ${tr:,.2f} ({tr / tot:.0%}) · "
              f"review por ronda ${tr / max(1, sum(max(r['reviews'], r['rev_sessions']) for r in rows)):,.2f}{R}")
    # Si una regla funciona lo mide bk learn effect (por regla y por clase de hallazgo, ADR-006); acá queda
    # costo y rondas por PR, así hay una sola respuesta para cada pregunta.
    print(f"{M}¿las reglas bajan sus hallazgos? → bk learn effect{R}")
    effect = []
    for e in effect:
        a, d = e["antes"], e["después"]
        print(f"\n{B}Lecciones aceptadas el {e['day']}{R} {M}({', '.join(e['lessons'])}){R}")
        print(f"  {'por PR':<14}{'antes':>10}{'después':>10}")
        for k in ("rondas", "bloqueantes", "desvíos", "costo"):
            better = d[k] < a[k]
            col = G if better and d["prs"] else ""
            print(f"  {k:<14}{a[k]:>10}{col}{d[k]:>10}{R}")
        print(f"  {'PRs':<14}{a['prs']:>10}{d['prs']:>10}")
        if d["prs"] < 5:
            print(f"  {M}pocos PRs después: todavía no alcanza para concluir{R}")


def main():
    if not VAULT:
        sys.exit(f"bk metrics: {VAULT_HELP}")
    args = sys.argv[1:]
    repo = args[args.index("--repo") + 1] if "--repo" in args else None
    rows, effect = build(repo, "--refresh" in args)
    if "--json" in args:
        json.dump({"prs": rows, "lessons": effect}, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        show(rows, effect)


if __name__ == "__main__":
    main()
