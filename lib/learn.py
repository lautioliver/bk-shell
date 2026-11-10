#!/usr/bin/env python3
"""Retroalimentación del harness: desvíos de los agentes → patrones → lecciones → INDEX.md.

Lee los transcripts de Claude Code (~/.claude/projects/**/*.jsonl) de forma incremental y registra
eventos de desvío en el vault (BARRILETE_VAULT: una carpeta de notas en markdown; sin ella, sale con
un error que dice qué poner). Nunca escribe reglas: propone lecciones (status: proposed)
y solo las que la persona marca `accepted` y completa pasan a agents/INDEX.md, que leen los agentes.

Eventos:
  guardrail_block   una baranda (guardrails.py/barandas.py) frenó un comando
  hook_block        otro hook PreToolUse frenó una herramienta
  user_denied       la persona rechazó un permiso
  interrupt         la persona interrumpió al agente
  verify_fail       red.sh / verify.py terminó con error
  contract_*        un subagente devolvió escalate / blockers / question / dropped
  plan_correction   después de `planner.py --draft`, la respuesta no fue un "sí"
  user_correction   la persona corrigió al agente ("no, ...", "te dije ...")

Uso: learn.py [--quiet] [--rebuild] [--redraw]
Estado: ~/.cache/bk/learn-state.json (BK_LEARN_STATE). Retros: repos en ${BK_CONFIG_DIR:-~/.config/bk}/retros.json.
"""
import glob
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from deviations import (FREEFORM, RE_APPROVE, RE_CONTRACT, RE_CORRECT, RE_FIELD, RISK_HINTS,  # noqa: E402,F401
                        VAULT_HELP, agent_risks, assistant_events, ev, finding_class, norm, review_findings,
                        text_of, user_events, vault)

PROJECTS = os.path.expanduser("~/.claude/projects")
VAULT = vault()                         # BARRILETE_VAULT, sin valor por defecto
AG = os.path.join(VAULT, "agents") if VAULT else ""
STATE = os.path.expanduser(os.environ.get("BK_LEARN_STATE", "~/.cache/bk/learn-state.json"))
AGENT_STATE_DIR = os.environ.get("BK_STATE_DIR") or os.path.expanduser("~/.cache/bk/agents")

FIRST_SCAN_DAYS = 30          # primera pasada: historia reciente
PATTERN_DAYS, PATTERN_MIN = 14, 3
INDEX_MAX_RULES = 40          # el índice entra en el contexto de cada sesión: corto

RE_MODEL = re.compile(r'"model":"(claude-[^"]+)"')
RE_TOOLUSE = re.compile(r'"type":"tool_use","id":"(toolu_[^"]+)","name":"([^"]+)"')


# ---------- utilidades ----------
def project_name(cwd):
    if not cwd:
        return "?"
    base, parent = os.path.basename(cwd), os.path.basename(os.path.dirname(cwd))
    return f"{parent[:-3]}#{base}" if parent.endswith("-wt") else base




def slug(s):
    s = re.sub(r"[^\w\s-]", "", s.lower())
    return re.sub(r"[\s_]+", "-", s).strip("-")[:60] or "leccion"


def role_for(session_id, cache={}):
    if session_id not in cache:
        try:
            with open(os.path.join(AGENT_STATE_DIR, f"{session_id}.json")) as f:
                cache[session_id] = json.load(f).get("role") or "-"
        except (OSError, ValueError):
            cache[session_id] = "-"
    return cache[session_id]


# ---------- escaneo ----------
def load_state(rebuild):
    if not rebuild:
        try:
            with open(STATE) as f:
                return json.load(f), False
        except (OSError, ValueError):
            pass
    return {"offsets": {}, "model": {}, "tools": {}, "plan": {}}, True


def scan(state, since_ts):
    offsets, models, tools, plan = state["offsets"], state["model"], state["tools"], state["plan"]
    events = []
    for path in glob.glob(os.path.join(PROJECTS, "**", "*.jsonl"), recursive=True):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_mtime < since_ts:
            continue
        off = offsets.get(path, 0)
        if st.st_size < off:
            off = 0
        if st.st_size == off:
            continue
        with open(path, "rb") as f:
            f.seek(off)
            data = f.read()
        end = data.rfind(b"\n") + 1
        for raw in data[:end].splitlines():
            line = raw.decode("utf-8", "ignore")
            is_user = '"type":"user"' in line
            is_asst = '"type":"assistant"' in line
            if not (is_user or is_asst):
                continue
            if is_asst:
                m = RE_MODEL.search(line)
                if m and m.group(1) != "<synthetic>":
                    models[path] = m.group(1)
                for tid, name in RE_TOOLUSE.findall(line):
                    tools[tid] = name
                interesting = any(k in line for k in ("red.sh", "verify.py", "planner.py", "result:") + RISK_HINTS)
                if not interesting:
                    continue
            elif not ('"is_error":true' in line or "interrupted by user" in line
                      or '"tool_result"' not in line):
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") not in ("user", "assistant") or e.get("isMeta"):
                continue
            if (e.get("cwd") or "").startswith(FIXTURE_CWD):   # sesiones de los tests (fixtures): no cuentan
                continue
            ctx = {
                "ts": e.get("timestamp", ""), "project": project_name(e.get("cwd")),
                "session": (e.get("sessionId") or "")[:8], "model": models.get(path, "-"),
                "role": role_for(e.get("sessionId") or ""),
            }
            content = (e.get("message") or {}).get("content")
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                if e["type"] == "assistant":
                    events += assistant_events(b, ctx, tools, plan, path)
                    events += agent_risks(b, ctx, ctx["role"] not in ("-", "", "chat"), path)
                else:
                    events += user_events(b, ctx, tools, plan, path)
        offsets[path] = off + end
    # acotar el mapa de herramientas (solo hace falta para resultados cercanos)
    if len(tools) > 4000:
        for k in list(tools)[:-2000]:
            del tools[k]
    return events





# ---------- vault ----------
def month_file(day):
    return os.path.join(AG, "data", f"events-{day[:7]}.jsonl")


def local_day(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return datetime.now().strftime("%Y-%m-%d")


def load_events(days):
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for f in sorted(glob.glob(os.path.join(AG, "data", "events-*.jsonl"))):
        for raw in open(f, encoding="utf-8"):
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if e.get("day", "") >= since:
                out.append(e)
    return out


LABELS = {
    "guardrail_block": "baranda", "hook_block": "hook", "user_denied": "permiso rechazado",
    "interrupt": "interrupción", "verify_fail": "verificación fallida",
    "contract_escalate": "escaló", "contract_blockers": "review con bloqueantes",
    "contract_question": "pregunta", "contract_dropped": "abandonó",
    "plan_correction": "plan corregido", "user_correction": "corrección",
    "force_push": "force push", "credential": "otra credencial", "attribution": "decisión atribuida",
    "review_finding": "hallazgo de review", "loop": "loop", "stale_grave": "trabado", "ci_claim": "CI en verde falso",
}
FIXTURE_CWD = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")   # tests: no son agentes reales


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def short_model(m):
    return re.sub(r"-\d{8}$", "", (m or "-").replace("claude-", ""))


def write_day(day, evs):
    def table(es):
        return ("| hora (UTC) | tipo | proyecto | rol | modelo | detalle |\n|---|---|---|---|---|---|\n"
                + "\n".join(
                    f"| {e['ts'][11:16]} | {LABELS.get(e['type'], e['type'])} | {e['project']} | "
                    f"{e['role']} | {short_model(e['model'])} | {e['detail'][:140].replace('|', '/')} |"
                    for e in sorted(es, key=lambda e: e["ts"])))
    c = Counter(LABELS.get(e["type"], e["type"]) for e in evs)
    resumen = " · ".join(f"{k} {v}" for k, v in c.most_common())
    auto = [e for e in evs if e["type"] not in FREEFORM]
    leer = [e for e in evs if e["type"] in FREEFORM and e["type"] != "interrupt"]
    n_int = sum(1 for e in evs if e["type"] == "interrupt")
    body = f"# Desvíos {day}\n\n{resumen}\n"
    if leer:
        body += ("\n## Para leer: tus correcciones y rechazos\n"
                 "No generan lecciones solas. Si ves un patrón, creá una lección en `lessons/`.\n\n"
                 + table(leer) + "\n")
    if auto:
        body += "\n## Con firma (alimentan [[patterns]])\n\n" + table(auto) + "\n"
    if n_int:
        body += f"\nInterrupciones: {n_int}.\n"
    write(os.path.join(AG, "deviations", f"{day}.md"),
          f"---\ntipo: desvios\nfecha: {day}\ntotal: {len(evs)}\n---\n" + body)


def existing_lessons():
    out = {}
    for f in glob.glob(os.path.join(AG, "lessons", "*.md")):
        txt = open(f, encoding="utf-8").read()
        m = re.search(r'^signature:\s*"?(.*?)"?\s*$', txt, re.M)
        if m:
            out[m.group(1)] = f
    return out


REVIEW_RULES = {   # regla sugerida por clase de hallazgo (la persona la edita o la descarta)
    "pr-metadata": "Antes de pedir review, releé el cuerpo del PR contra el diff y el bloque de red.sh de este PR.",
    "ci-claim": "No digas que el CI está en verde o que una falla es flaky sin haberlo visto en gh pr checks.",
    "undecided-decision": "Si agregás una regla o cambiás una decisión que el issue no trae, dejala como pregunta.",
    "test-cannot-fail": "Comprobá que cada test nuevo falla si quitás lo que chequea.",
    "stale-docs": "Después de cambiar un comportamiento, buscá en README, AGENTS.md y SKILL.md lo que lo describe.",
    "wrong-command": "Corré contra la herramienta real cada comando que escribís en un skill o doc.",
    "text-code-mismatch": "Antes de cerrar, compará lo que dicen ADR, SKILL y docs con lo que hace el código.",
    "uncovered-case": "Para cada camino nuevo (modo, rol, flag), revisá quién lo dispara y qué pasa si falta un dato.",
    "unhandled-input": "Probá entradas mal formadas (null, lista, vacío) y que el error sea un mensaje, no un traceback.",
}


def suggested_rule(typ, sig):
    if typ == "review_finding":
        return REVIEW_RULES.get(sig, "")
    if typ == "guardrail_block":
        return (f"No intentes {sig}: es una acción humana. No busques otro camino; "
                "pedile a la persona que la corra ella.")
    return ""


def write_patterns_and_lessons(evs):
    groups = defaultdict(list)
    for e in evs:
        groups[(e["type"], e["sig"], e["project"])].append(e)
    rows, nuevas = [], []
    lessons = existing_lessons()
    for (typ, sig, proj), g in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        g = sorted(g, key=lambda e: e["ts"])
        rows.append(f"| {len(g)} | {LABELS.get(typ, typ)} | {sig.replace('|', '/')} | {proj} | "
                    f"{', '.join(sorted({short_model(e['model']) for e in g}))} |")
        key = f"{typ}::{sig}::{proj}"
        if len(g) < PATTERN_MIN or key in lessons or typ in FREEFORM:
            continue
        if typ == "review_finding" and not suggested_rule(typ, sig):   # "otro hallazgo": sin una regla que proponer
            continue
        name = slug(f"{LABELS.get(typ, typ)} {sig}")
        path = os.path.join(AG, "lessons", f"{name}.md")
        ejemplos = "\n".join(f"- {e['day']} · {e['project']} · {short_model(e['model'])}: {e['detail'][:200]}"
                             for e in g[-3:])
        write(path, f"""---
status: proposed   # proposed → accepted (entra a INDEX.md) | retired
type: {typ}
signature: "{key}"
scope: {proj}      # global, o el proyecto donde aplica
count: {len(g)}
first_seen: {g[0]['day']}
last_seen: {g[-1]['day']}
---
# {LABELS.get(typ, typ).capitalize()}: {sig}

Patrón detectado por `bk learn`: {len(g)} veces en {PATTERN_DAYS} días.

## Ejemplos
{ejemplos}

## Regla para los agentes
<!-- Una línea. Es lo único que pasa a INDEX.md cuando status: accepted. Vacía = no entra. -->
{suggested_rule(typ, sig)}

## Notas
<!-- Si la regla es de un solo proyecto, considerá llevarla a su AGENTS.md o a /improve-skills: el repo manda en lo del proyecto. -->
""")
        nuevas.append(name)
    write(os.path.join(AG, "patterns.md"),
          f"# Patrones (últimos {PATTERN_DAYS} días)\n\nGenerado por `bk learn`. Con ≥{PATTERN_MIN} repeticiones se propone una lección.\n\n"
          "| veces | tipo | firma | proyecto | modelos |\n|---|---|---|---|---|\n" + "\n".join(rows) + "\n")
    return nuevas


def write_models(evs30):
    by = defaultdict(Counter)
    sessions = defaultdict(set)
    for e in evs30:
        k = (short_model(e["model"]), e["role"])
        by[k][LABELS.get(e["type"], e["type"])] += 1
        sessions[k].add(e["session"])
    rows = "\n".join(
        f"| {m} | {r} | {sum(c.values())} | {len(sessions[(m, r)])} | "
        f"{' · '.join(f'{k} {v}' for k, v in c.most_common(4))} |"
        for (m, r), c in sorted(by.items(), key=lambda kv: -sum(kv[1].values())))
    write(os.path.join(AG, "models.md"),
          "# Desvíos por modelo y rol (30 días)\n\nGenerado por `bk learn`. Sirve para decidir qué modelo corre cada rol (roles.yml).\n\n"
          "| modelo | rol | desvíos | sesiones con desvíos | principales |\n|---|---|---|---|---|\n" + rows + "\n")


def write_index():
    rules = []
    for f in sorted(glob.glob(os.path.join(AG, "lessons", "*.md"))):
        txt = open(f, encoding="utf-8").read()
        if not re.search(r"^status:\s*accepted\b", txt, re.M):
            continue
        scope = (re.search(r"^scope:\s*(\S+)", txt, re.M) or [None, "global"])[1]
        sec = re.search(r"## Regla para los agentes\n(.*?)(\n## |\Z)", txt, re.S)
        rule = re.sub(r"<!--.*?-->", "", sec.group(1), flags=re.S).strip() if sec else ""
        if rule:
            rules.append(f"- {'' if scope == 'global' else f'[solo en {scope}] '}{rule.splitlines()[0]}")
    rules = rules[:INDEX_MAX_RULES]
    write(os.path.join(AG, "INDEX.md"),
          "<!-- Generado por `bk learn` desde agents/lessons/ (status: accepted). No editar acá: editá la lección. -->\n"
          "# Lecciones aprendidas de agentes\n\n"
          "Reglas que salieron de desvíos reales y la persona aprobó. Aplican además de AGENTS.md; "
          "si chocan con el AGENTS.md de un proyecto, manda el proyecto.\n\n"
          + ("\n".join(rules) if rules else "_Todavía no hay lecciones aceptadas._") + "\n\n"
          f"Para buscar contexto (retros por issue o por skill, desvíos, patrones): `{AG}/mapa.md`.\n")
    return len(rules)


# ---------- Retros de Barrilete Kit (rama agents/retros) -> vault ----------
RETRO_REPOS_CONF = os.path.join(os.environ.get("BK_CONFIG_DIR") or os.path.expanduser("~/.config/bk"),
                                "retros.json")                      # {"repos": ["~/mi-proyecto", ...]}
RETRO_FETCH_EVERY = 1800                                            # git fetch como mucho cada 30 min


def retro_repos():
    try:
        repos = json.load(open(RETRO_REPOS_CONF)).get("repos", [])
    except (OSError, ValueError, AttributeError):
        repos = []                                                  # sin retros.json: no trae retros ni reviews
    return [os.path.expanduser(r) for r in repos if isinstance(r, str) and os.path.isdir(os.path.expanduser(r))]


def git(repo, *args):
    import subprocess
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=60)
    return r.stdout if r.returncode == 0 else ""


REVIEWS_EVERY = 900   # s: learn corre cada ~2 min; GitHub, cada 15


def import_reviews(state):
    """Hallazgos (bloqueantes y "para corregir") de las reviews publicadas en los repos con retros.
    Cada ítem es un evento review_finding con su clase; el proyecto es el repo, para que se agrupen."""
    if time.time() - state.get("reviews_t", 0) < REVIEWS_EVERY:
        return []
    state["reviews_t"] = time.time()
    seen = set(state.setdefault("reviews_seen", []))
    since = (datetime.now() - timedelta(days=FIRST_SCAN_DAYS)).strftime("%Y-%m-%d")
    env = {k: v for k, v in os.environ.items() if k not in ("GH_CONFIG_DIR", "GH_TOKEN")}
    out = []
    for repo in retro_repos():
        url = git(repo, "remote", "get-url", "origin")
        m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?\s*$", url or "")
        if not m:
            continue
        slug_ = m.group(1)
        try:
            raw = subprocess.run(["gh", "pr", "list", "--repo", slug_, "--state", "all", "--limit", "60",
                                  "--json", "number,reviews"], capture_output=True, text=True, timeout=60, env=env).stdout
            prs = json.loads(raw or "[]")
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        for p in prs:
            for r in p.get("reviews") or []:
                rid, ts = r.get("id"), r.get("submittedAt") or ""
                if not rid or rid in seen or ts[:10] < since:
                    continue
                seen.add(rid)
                ctx = {"ts": ts, "project": slug_.split("/")[-1], "session": rid[-8:], "model": "-",
                       "role": "implementer"}
                for item in review_findings(r.get("body")):
                    out.append(ev(ctx, "review_finding", finding_class(item), f"#{p['number']}: {item}"))
    state["reviews_seen"] = sorted(seen)[-5000:]
    return out


def import_retros(state):
    """Copia cada retro nueva de origin/agents/retros a <vault>/agents/retros/<repo>/ y arma retros.md."""
    import time as _t
    out_dir = os.path.join(AG, "retros")
    new = []
    for repo in retro_repos():
        name = os.path.basename(repo.rstrip("/"))
        key = f"retros_fetch:{repo}"
        if _t.time() - state.get(key, 0) > RETRO_FETCH_EVERY:
            git(repo, "fetch", "-q", "origin", "agents/retros")
            state[key] = _t.time()
        files = [f for f in git(repo, "ls-tree", "-r", "--name-only", "origin/agents/retros").split()
                 if f.startswith("retros/") and f.endswith(".md")]
        for f in files:
            dest = os.path.join(out_dir, name, os.path.basename(f))
            if os.path.exists(dest):
                continue
            body = git(repo, "show", f"origin/agents/retros:{f}")
            if not body:
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            # Fuente al pie, para saber de dónde salió (el frontmatter de la retro queda igual)
            body = body.rstrip() + f"\n\n---\n_Retro de `{name}`, rama `agents/retros`, `{f}`._\n"
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(body)
            new.append(dest)
    # Índice: las 40 más recientes, con skill, issue/PR y la primera línea de desvíos
    notes = sorted(glob.glob(os.path.join(out_dir, "*", "*.md")), key=os.path.basename, reverse=True)[:40]
    rows = []
    for n in notes:
        txt = open(n, encoding="utf-8").read()
        meta = dict(re.findall(r"^(skill|issue|pr|date|model):\s*(.+)$", txt, re.M))
        dev = re.search(r"\*\*Deviations:\*\*\s*(.+)", txt) or re.search(r"\*\*Desv[ií]os:\*\*\s*(.+)", txt)
        rel = os.path.relpath(n, AG)[:-3]
        rows.append(f"| [[{rel}\\|{meta.get('date', '?')}]] | {os.path.basename(os.path.dirname(n))} | "
                    f"{meta.get('skill', '?')} | #{meta.get('issue', '?')} | {meta.get('model', '?')} | "
                    f"{norm(dev.group(1), 110) if dev else ''} |")
    if rows:
        write(os.path.join(AG, "retros.md"),
              "# Retros de los agentes\n\nLo que cada agente anotó al terminar (`retro.sh`, rama `agents/retros` "
              "de cada repo). Se copian solas desde `bk learn`; las lee `/improve-skills`.\n\n"
              "| Fecha | Repo | Skill | Issue | Modelo | Desvíos que anotó |\n|---|---|---|---|---|---|\n"
              + "\n".join(rows) + "\n")
    return new


# ---------- Mapa del vault: todo enlazado para que un agente (o vos) navegue ----------
# Notas hub generadas: agents/mapa.md, skills/<skill>.md, issues/<repo>-<n>.md, repos/<repo>.md.
# A cada retro, día de desvíos y lección se le agrega un bloque "Relacionado" entre marcadores
# (<!-- bk:links --> … <!-- /bk:links -->) que se rehace en cada corrida sin tocar el resto de la nota.
ROLE_SKILL = {"implementer": "implement-issue", "implementer-light": "implement-issue", "reviewer": "review-pr",
              "planner": "plan-feature", "orchestrator": "orchestrate"}
LINKS_RE = re.compile(r"\n*<!-- bk:links -->.*?<!-- /bk:links -->\n?", re.S)


def write_if_changed(path, text):
    try:
        if open(path, encoding="utf-8").read() == text:
            return
    except OSError:
        pass
    write(path, text)


def with_links(path, links):
    """Rehace el bloque Relacionado de una nota existente."""
    try:
        txt = open(path, encoding="utf-8").read()
    except OSError:
        return
    base = LINKS_RE.sub("", txt).rstrip()
    block = "\n\n<!-- bk:links -->\n## Relacionado\n" + "\n".join(f"- {l}" for l in links) + "\n<!-- /bk:links -->\n"
    write_if_changed(path, base + block)


def retro_meta():
    out = []
    for n in glob.glob(os.path.join(AG, "retros", "*", "*.md")):
        txt = open(n, encoding="utf-8").read()
        m = dict(re.findall(r"^(skill|issue|pr|date|model|area):\s*(.+?)\s*$", txt, re.M))
        dev = re.search(r"\*\*(?:Deviations|Desv[ií]os):\*\*\s*(.+)", txt)
        out.append({"path": n, "rel": os.path.relpath(n, AG)[:-3], "repo": os.path.basename(os.path.dirname(n)),
                    "skill": m.get("skill", "?"), "issue": m.get("issue", "").lstrip("#"), "pr": m.get("pr", "").lstrip("#"),
                    "date": m.get("date", os.path.basename(n)[:10]), "model": m.get("model", "?"),
                    "dev": norm(dev.group(1), 100) if dev else ""})
    return sorted(out, key=lambda r: (r["date"], r["rel"]))


def link_vault():
    retros = retro_meta()
    skills, issues, repos = {}, {}, {}
    for r in retros:
        skills.setdefault(r["skill"], []).append(r)
        if r["issue"]:
            issues.setdefault((r["repo"], r["issue"]), []).append(r)
        repos.setdefault(r["repo"], []).append(r)
    days = {os.path.basename(f)[:-3] for f in glob.glob(os.path.join(AG, "deviations", "*.md"))}
    lessons = {os.path.basename(f)[:-3]: f for f in glob.glob(os.path.join(AG, "lessons", "*.md"))}
    issue_link = lambda repo, n: f"[[issues/{repo}-{n}|{repo}#{n}]]"
    row = lambda r: (f"| [[{r['rel']}|{r['date']}]] | {r['skill']} | "
                     f"{issue_link(r['repo'], r['issue']) if r['issue'] else ''} | {r['model']} | {r['dev']} |")
    head = "| Fecha | Skill | Issue | Modelo | Desvíos que anotó |\n|---|---|---|---|---|\n"

    # 1) Cada retro -> su skill, issue, repo, día, y la anterior/siguiente del mismo issue
    for r in retros:
        links = [f"Skill: [[skills/{r['skill']}|{r['skill']}]]", f"Repo: [[repos/{r['repo']}|{r['repo']}]]"]
        if r["issue"]:
            links.append(f"Issue: {issue_link(r['repo'], r['issue'])}")
            same = issues[(r["repo"], r["issue"])]
            i = same.index(r)
            if i > 0:
                links.append(f"Anterior en este issue: [[{same[i - 1]['rel']}|{same[i - 1]['date']} · {same[i - 1]['skill']}]]")
            if i < len(same) - 1:
                links.append(f"Siguiente en este issue: [[{same[i + 1]['rel']}|{same[i + 1]['date']} · {same[i + 1]['skill']}]]")
        if r["date"] in days:
            links.append(f"Desvíos de ese día: [[deviations/{r['date']}|{r['date']}]]")
        links.append("Todas: [[retros]] · [[mapa]]")
        with_links(r["path"], links)

    # 2) Hubs por skill: retros + lecciones del rol que usa esa skill
    for sk, rs in skills.items():
        les = [n for n in lessons if any(n.startswith(role) and ROLE_SKILL.get(role) == sk for role in ROLE_SKILL)]
        body = (f"---\ntipo: skill\nskill: {sk}\nretros: {len(rs)}\n---\n# Skill `{sk}`\n\n"
                f"{len(rs)} retros. Lecciones relacionadas: " + (", ".join(f"[[lessons/{n}|{n}]]" for n in les) or "ninguna")
                + ".\n\n## Retros (más recientes primero)\n\n" + head + "\n".join(row(r) for r in reversed(rs))
                + "\n\nVer también: [[patterns]] · [[retros]] · [[mapa]]\n")
        write_if_changed(os.path.join(AG, "skills", f"{sk}.md"), body)

    # 3) Hubs por issue: la historia del issue (implement, reviews…) en orden
    for (repo, n), rs in issues.items():
        prs = sorted({r["pr"] for r in rs if r["pr"]})
        evday = sorted({e["day"] for e in load_events(3650) if e.get("project") == f"{repo}#{n}"})
        body = (f"---\ntipo: issue\nrepo: {repo}\nissue: {n}\n---\n# {repo}#{n}\n\n"
                f"PRs: {', '.join('#' + x for x in prs) or '—'} · Repo: [[repos/{repo}|{repo}]]\n\n"
                "## Retros en orden\n\n" + "\n".join(
                    f"{i + 1}. [[{r['rel']}|{r['date']} · {r['skill']}]] ({r['model']}) — {r['dev'] or 'sin desvíos'}"
                    for i, r in enumerate(rs))
                + ("\n\n## Desvíos registrados en su worktree\n\n" + ", ".join(f"[[deviations/{d}|{d}]]" for d in evday)
                   if evday else "") + "\n\nVer también: [[mapa]]\n")
        write_if_changed(os.path.join(AG, "issues", f"{repo}-{n}.md"), body)

    # 4) Hubs por repo
    for repo, rs in repos.items():
        iss = sorted({r["issue"] for r in rs if r["issue"]}, key=lambda x: int(x) if x.isdigit() else 0, reverse=True)
        body = (f"---\ntipo: repo\nrepo: {repo}\nretros: {len(rs)}\n---\n# {repo}\n\n{len(rs)} retros.\n\n"
                "## Issues\n\n" + " · ".join(issue_link(repo, i) for i in iss)
                + "\n\n## Skills\n\n" + " · ".join(f"[[skills/{k}|{k}]]" for k in sorted({r['skill'] for r in rs}))
                + "\n\nVer también: [[retros]] · [[mapa]]\n")
        write_if_changed(os.path.join(AG, "repos", f"{repo}.md"), body)

    # 5) Cada día de desvíos -> retros de ese día
    by_day = {}
    for r in retros:
        by_day.setdefault(r["date"], []).append(r)
    for d in days:
        rs = by_day.get(d, [])
        links = [f"Retro: [[{r['rel']}|{r['skill']} · {r['repo']}#{r['issue']}]]" for r in rs] or ["Sin retros ese día"]
        with_links(os.path.join(AG, "deviations", f"{d}.md"), links + ["[[patterns]] · [[mapa]]"])

    # 6) Cada lección -> la skill de su rol
    for n, f in lessons.items():
        sk = next((ROLE_SKILL[r] for r in sorted(ROLE_SKILL, key=len, reverse=True) if n.startswith(r)), None)
        with_links(f, ([f"Skill: [[skills/{sk}|{sk}]]"] if sk else []) + ["[[INDEX]] · [[patterns]] · [[mapa]]"])

    # 7) Mapa (MOC): la entrada para navegar todo
    body = ("---\ntipo: mapa\n---\n# Mapa del vault de agentes\n\n"
            "Por dónde empezar: [[INDEX]] (reglas vigentes) · [[patterns]] (lo que se repite) · [[models]] · [[retros]].\n\n"
            "## Repos\n\n" + "\n".join(f"- [[repos/{r}|{r}]] — {len(rs)} retros" for r, rs in sorted(repos.items()))
            + "\n\n## Skills\n\n" + "\n".join(f"- [[skills/{k}|{k}]] — {len(v)} retros" for k, v in sorted(skills.items()))
            + "\n\n## Issues (más recientes primero)\n\n" + "\n".join(
                f"- {issue_link(r, n)} — {len(v)} retros" for (r, n), v in
                sorted(issues.items(), key=lambda kv: kv[1][-1]["date"], reverse=True)[:60])
            + "\n\n## Lecciones\n\n" + "\n".join(f"- [[lessons/{n}|{n}]]" for n in sorted(lessons))
            + "\n\n## Días con desvíos\n\n" + " · ".join(f"[[deviations/{d}|{d}]]" for d in sorted(days, reverse=True)[:45])
            + "\n")
    write_if_changed(os.path.join(AG, "mapa.md"), body)


def main():
    quiet = "--quiet" in sys.argv
    if not VAULT:
        sys.exit(f"bk learn: {VAULT_HELP}")
    if not os.path.isdir(VAULT):
        sys.exit(f"bk learn: no existe el vault {VAULT} (BARRILETE_VAULT)")
    state, fresh = load_state("--rebuild" in sys.argv)
    since = 0 if not fresh else (datetime.now() - timedelta(days=FIRST_SCAN_DAYS)).timestamp()
    if fresh:  # reconstrucción: evitar eventos duplicados
        for f in glob.glob(os.path.join(AG, "data", "events-*.jsonl")):
            os.remove(f)
    new = scan(state, since)
    new += import_reviews(state)

    touched = set()
    for e in new:
        e["day"] = local_day(e["ts"])
        touched.add(e["day"])
        os.makedirs(os.path.join(AG, "data"), exist_ok=True)
        with open(month_file(e["day"]), "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE + ".tmp", "w") as f:
        json.dump(state, f)
    os.replace(STATE + ".tmp", STATE)

    retros = import_retros(state)
    with open(STATE + ".tmp", "w") as f:
        json.dump(state, f)
    os.replace(STATE + ".tmp", STATE)

    evs30 = load_events(30)
    if "--redraw" in sys.argv:          # rehacer las notas de todos los días (después de limpiar eventos)
        touched |= {e["day"] for e in evs30}
    for day in touched:
        write_day(day, [e for e in evs30 if e["day"] == day])
    since14 = (datetime.now() - timedelta(days=PATTERN_DAYS)).strftime("%Y-%m-%d")
    nuevas = write_patterns_and_lessons([e for e in evs30 if e["day"] >= since14])
    write_models(evs30)
    n_rules = write_index()
    link_vault()
    proposed = sum(1 for f in glob.glob(os.path.join(AG, "lessons", "*.md"))
                   if re.search(r"^status:\s*proposed\b", open(f, encoding="utf-8").read(), re.M))

    if not quiet:
        print(f"{len(new)} desvíos nuevos · {len(evs30)} en 30 días · {proposed} lecciones por revisar · "
              f"{n_rules} reglas en INDEX.md")
        for n in nuevas:
            print(f"  nueva lección propuesta: agents/lessons/{n}.md")
        if retros:
            print(f"  {len(retros)} retros nuevas en agents/retros/")


if __name__ == "__main__":
    main()
