#!/usr/bin/env python3
"""bk learn effect: ¿las reglas aceptadas bajan los hallazgos que dicen evitar?

Por cada lección accepted mide los hallazgos de review de su clase por ronda de review, en los PRs abiertos
antes y después de aceptarla. El PR abierto después de aceptar la regla es la aproximación a "el agente
trabajó con la regla". Las rondas salen de GitHub (todas las reviews con secciones Bloqueantes / Para corregir,
también las que no tienen hallazgos: los eventos de bk learn solo guardan las que tienen).

Frontmatter de la lección que usa:
  accepted: 2026-11-04 o 2026-11-04T19:24Z   cuándo se aceptó (sin esto no se mide)
  mide: metadatos del PR, docs desactualizados   clases de hallazgo que la regla debería bajar
        (sin `mide`: la de su signature si es review_finding; `mide: -` = no se mide)
  scope: mi-proyecto | global

  effect.py [--days N] [--json] [--no-write]   tabla en la terminal y agents/efecto.md en el vault

Como las reviews se pueden poner más duras, también muestra qué parte de todos los hallazgos es de la clase
(si esa parte baja, la regla hace algo aunque el total suba).
"""
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from deviations import CLASS_LABELS, FINDING_SECTION, VAULT_HELP, finding_class, review_findings, vault  # noqa: E402

# Las lecciones viejas nombran la clase en castellano ("metadatos del PR"); finding_class devuelve el id del
# kit (pr-metadata, ADR-006). Se traduce al leer la lección, así las dos formas miden lo mismo.
LABEL_TO_ID = {v: k for k, v in CLASS_LABELS.items()}

VAULT = vault()       # BARRILETE_VAULT, sin valor por defecto
AG = os.path.join(VAULT, "agents") if VAULT else ""
RETRO_REPOS_CONF = os.path.join(os.environ.get("BK_CONFIG_DIR") or os.path.expanduser("~/.config/bk"), "retros.json")
MIN_ROUNDS = 6       # menos rondas después de aceptarla: poca muestra para decir algo
MIN_CASES = 3        # menos hallazgos de la clase antes: no hay qué comparar


def frontmatter(txt):
    m = re.match(r"---\n(.*?)\n---", txt, re.S)
    out = {}
    for l in (m.group(1) if m else "").splitlines():
        k, _, v = l.partition(":")
        if _:
            out[k.strip()] = re.sub(r"\s+#.*$", "", v).strip().strip('"')
    return out


def parse_ts(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        if len(s) == 10:
            return datetime.fromisoformat(s).astimezone()   # fecha sola: medianoche local
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def lessons():
    out = []
    for f in sorted(glob.glob(os.path.join(AG, "lessons", "*.md"))):
        txt = open(f, encoding="utf-8").read()
        fm = frontmatter(txt)
        if fm.get("status") != "accepted":
            continue
        rule = re.search(r"## Regla para los agentes\n(?:<!--.*?-->\n)?(.+)", txt)
        sig = fm.get("signature", "")
        mide = fm.get("mide")
        if mide is None and fm.get("type") == "review_finding":
            mide = sig.split("::")[1] if sig.count("::") >= 2 else ""
        classes = [] if mide in (None, "", "-") else [LABEL_TO_ID.get(c.strip(), c.strip()) for c in mide.split(",") if c.strip()]
        out.append({"name": os.path.basename(f)[:-3], "rule": rule.group(1).strip() if rule else "",
                    "scope": fm.get("scope", "global"), "accepted": parse_ts(fm.get("accepted")),
                    "classes": classes, "unmeasured": mide == "-"})
    return out


def repos():
    try:
        rs = json.load(open(RETRO_REPOS_CONF)).get("repos", [])
    except (OSError, ValueError, AttributeError):
        rs = []          # sin retros.json: no hay repos que medir
    return [os.path.expanduser(r) for r in rs if isinstance(r, str)]


def fetch_prs():
    """{repo: [{number, createdAt, reviews}]}. BK_EFFECT_PRS=<archivo json> lo reemplaza (tests)."""
    fx = os.environ.get("BK_EFFECT_PRS")
    if fx:
        return json.load(open(fx))
    env = {k: v for k, v in os.environ.items() if k not in ("GH_CONFIG_DIR", "GH_TOKEN")}
    out = {}
    for r in repos():
        try:
            url = subprocess.run(["git", "-C", r, "remote", "get-url", "origin"], capture_output=True, text=True).stdout
        except OSError:
            continue
        m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?\s*$", url or "")
        if not m:
            continue
        p = subprocess.run(["gh", "pr", "list", "--repo", m.group(1), "--state", "all", "--limit", "200",
                            "--json", "number,createdAt,reviews"], capture_output=True, text=True, env=env, timeout=90)
        if p.returncode != 0:
            sys.exit(f"bk learn effect: no pude leer los PRs de {m.group(1)}: {p.stderr.strip()[:200]}")
        out[m.group(1).split("/")[-1]] = json.loads(p.stdout or "[]")
    return out


def rounds(prs_by_repo):
    """Cada review con secciones de hallazgos es una ronda: (repo, PR, creado, [clases de sus hallazgos])."""
    out = []
    for repo, prs in prs_by_repo.items():
        for p in prs:
            created = parse_ts(p.get("createdAt"))
            for r in p.get("reviews") or []:
                body = r.get("body") or ""
                if not any(FINDING_SECTION.match(l) for l in body.splitlines()):
                    continue
                out.append({"repo": repo, "pr": p["number"], "created": created,
                            "classes": [finding_class(i) for i in review_findings(body)]})
    return out


def stats(rs, classes):
    n = len(rs)
    total = sum(len(r["classes"]) for r in rs)
    cls = sum(1 for r in rs for c in r["classes"] if c in classes)
    return {"rounds": n, "prs": len({(r["repo"], r["pr"]) for r in rs}), "findings": total, "class": cls,
            "rate": cls / n if n else None, "share": cls / total if total else None}


def verdict(b, a):
    if a["rounds"] < MIN_ROUNDS:
        return "poca muestra", f"solo {a['rounds']} rondas después; esperar"
    if not b["rounds"]:
        return "sin antes", "no hay rondas de antes para comparar"
    if not b["class"]:
        return "no había", "la clase no aparecía antes: la regla no tiene qué bajar"
    if b["class"] < MIN_CASES:
        return "pocos casos", f"solo {b['class']} hallazgo(s) de la clase antes: no alcanza para comparar"
    if a["rate"] <= b["rate"] * 0.5:
        return "baja", "la clase bajó a la mitad o menos"
    if a["rate"] < b["rate"] or (a["share"] is not None and b["share"] and a["share"] < b["share"] * 0.75):
        return "baja algo", "baja, pero poco: mirar de nuevo con más rondas"
    return "no baja", "reescribirla (más concreta, en el momento justo) o retirarla"


def measure(days):
    rs = rounds(fetch_prs())
    res = []
    for les in lessons():
        row = {"lesson": les["name"], "rule": les["rule"], "classes": les["classes"]}
        if les["unmeasured"] or not les["classes"]:
            row.update(verdict="no se mide", why="sin clase de hallazgo (mide: -)")
        elif not les["accepted"]:
            row.update(verdict="sin fecha", why="falta accepted: en la lección")
        else:
            acc = les["accepted"]
            mine = [r for r in rs if les["scope"] in ("global", "") or r["repo"] == les["scope"]]
            before = [r for r in mine if r["created"] and acc - timedelta(days=days) <= r["created"] < acc]
            after = [r for r in mine if r["created"] and r["created"] >= acc]
            b, a = stats(before, les["classes"]), stats(after, les["classes"])
            v, why = verdict(b, a)
            row.update(accepted=acc.astimezone().strftime("%Y-%m-%d %H:%M"), before=b, after=a, verdict=v, why=why)
        res.append(row)
    return res


def fmt(x, pct=False):
    if x is None:
        return "—"
    return f"{x * 100:.0f}%" if pct else f"{x:.2f}"


def render(res, days):
    lines = [f"# Efecto de las reglas\n",
             f"Generado por `bk learn effect` el {datetime.now().strftime('%Y-%m-%d %H:%M')}. Hallazgos de review de la clase "
             f"que la regla quiere evitar, por ronda de review, en PRs abiertos hasta {days} días antes de aceptarla y "
             "después. *Parte* = qué parte de todos los hallazgos son de esa clase (no sube si las reviews se "
             "ponen más duras).\n",
             "| regla | mide | aceptada | antes: por ronda (parte) · rondas | después: por ronda (parte) · rondas | veredicto |",
             "|---|---|---|---|---|---|"]
    for r in res:
        b, a = r.get("before"), r.get("after")
        lines.append(
            f"| [[{r['lesson']}]] | {', '.join(CLASS_LABELS.get(c, c) for c in r['classes']) or '—'} | {r.get('accepted', '—')} | "
            + (f"{fmt(b['rate'])} ({fmt(b['share'], True)}) · {b['rounds']}" if b else "—") + " | "
            + (f"{fmt(a['rate'])} ({fmt(a['share'], True)}) · {a['rounds']}" if a else "—") + " | "
            + f"**{r['verdict']}**: {r['why']} |")
    lines += ["", "Veredictos:",
              "- **baja**: la clase bajó a la mitad o menos.",
              "- **baja algo**: baja poco.",
              f"- **no baja**: para reescribir o retirar.",
              f"- **poca muestra**: menos de {MIN_ROUNDS} rondas después; esperar.",
              f"- **pocos casos**: menos de {MIN_CASES} hallazgos de la clase antes.",
              "",
              "La fecha `accepted:` y la clase `mide:` salen del frontmatter de cada lección. Corregilas ahí si no son las que querés medir.", ""]
    return "\n".join(lines)


def main(argv):
    if not VAULT:
        sys.exit(f"bk learn effect: {VAULT_HELP}")
    days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 30
    res = measure(days)
    if "--json" in argv:
        print(json.dumps(res, ensure_ascii=False, default=str))
        return 0
    for r in res:
        b, a = r.get("before"), r.get("after")
        head = f"{r['verdict']:<13} {r['lesson']}"
        if b and a:
            head += (f"  antes {fmt(b['rate'])}/ronda ({fmt(b['share'], True)}, {b['rounds']} rondas)"
                     f" → después {fmt(a['rate'])}/ronda ({fmt(a['share'], True)}, {a['rounds']} rondas)")
        print(head)
        print(f"{'':<14}{r['why']}")
    if "--no-write" not in argv and os.path.isdir(AG):
        with open(os.path.join(AG, "efecto.md"), "w", encoding="utf-8") as f:
            f.write(render(res, days))
        print(f"\n→ {os.path.join(AG, 'efecto.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
