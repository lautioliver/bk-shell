"""Detectores de desvíos de los agentes, compartidos por `bk learn` (los guarda en el vault) y
`bk monitor` (los muestra en vivo). Cada uno mira un bloque de un mensaje del transcript de Claude Code.

  guardrail_block   una baranda frenó un comando        hook_block       otro hook PreToolUse frenó una herramienta
  user_denied       la persona rechazó un permiso       interrupt        la persona interrumpió al agente
  verify_fail       red.sh / verify.py falló             contract_*       un subagente devolvió escalate/blockers/question/dropped
  plan_correction   el plan no recibió un "sí"          user_correction  la persona corrigió al agente
  force_push        intentó un force push                credential       intentó usar otra credencial de GitHub
  attribution       te atribuye una decisión ("como decidiste") — solo en agentes, no en tus chats
  review_finding    un hallazgo de una review publicada (bloqueante o para corregir), por clase
"""
import re

RE_CONTRACT = re.compile(r"^\s*result:\s*(escalate|blockers|question|dropped)\b", re.M)
RE_FIELD = lambda k: re.compile(rf"^\s*{k}:\s*(\S+)", re.M)
RE_APPROVE = re.compile(r"^\s*(s[ií]|yes|dale|ok(ay)?|aprob|listo|adelante|go|perfecto|de una|confirm)", re.I)
# Solo frases que corrigen al agente. "no me deja / no me aparece" es un reporte, no una corrección.
RE_CORRECT = re.compile(
    r"^\s*(no\s*[,.!]|no\s+(es|era|eso|hagas|toques|uses|quiero|quise|cambies|borres|sigas)\b|nope\b|"
    r"eso no\b|te dije\b|est[aá] mal\b|wrong\b|that'?s (wrong|not)\b|don'?t\b|"
    r"no era (eso|as[ií])\b)", re.I)
# Señales sin firma común: se leen en el resumen del día, no generan lecciones automáticas
FREEFORM = {"interrupt", "user_correction", "plan_correction", "user_denied"}


def norm(s, n=90):
    s = re.sub(r"/[\w./-]+", "<ruta>", s)
    s = re.sub(r"\b\d+\b", "N", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n]


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return ""


def ev(ctx, typ, sig, detail):
    return {**ctx, "type": typ, "sig": sig, "detail": re.sub(r"\s+", " ", detail).strip()[:280]}


def assistant_events(b, ctx, tools, plan, path):
    out = []
    if b.get("type") == "tool_use":
        cmd = str((b.get("input") or {}).get("command", ""))
        name = b.get("name", "")
        tools[b.get("id", "")] = f"{name}::{cmd[:300]}" if cmd else name
        if "planner.py" in cmd:
            plan[path] = "--draft" in cmd
    elif b.get("type") == "text":
        t = b.get("text", "")
        m = RE_CONTRACT.search(t)
        if m:
            role = (RE_FIELD("role").search(t) or [None, ctx["role"]])[1]
            model = (RE_FIELD("model").search(t) or [None, ctx["model"]])[1]
            issue = (RE_FIELD("issue").search(t) or [None, "?"])[1]
            summ = (RE_FIELD("summary").search(t) or [None, ""])[1]
            c = {**ctx, "role": role, "model": model}
            out.append(ev(c, f"contract_{m.group(1)}", f"{m.group(1)} · {role}",
                          f"issue {issue}: {t[t.find('summary:'):][:240] if 'summary:' in t else summ}"))
    return out


def user_events(b, ctx, tools, plan, path):
    out = []
    if b.get("type") == "tool_result":
        if not b.get("is_error"):
            return out
        t = text_of(b.get("content"))
        tool = tools.get(b.get("tool_use_id", ""), "?")
        if "hook error" in t and "PreToolUse" in t:
            reason = t[t.find("]:") + 2:] if "]:" in t else t
            first = reason.strip().split("\n")[0]
            if "):" in first:
                first = first.split("):", 1)[1]
            first = first.strip().rstrip(".")
            # Un hook roto (error de bash/python del propio hook) no es un desvío del agente: es infraestructura
            infra = re.search(r"syntax error|command not found|No such file or directory|Traceback|unexpected EOF", t)
            typ = "guardrail_block" if re.search(r"baranda|guardrail", t, re.I) else "hook_block"
            if not infra:
                out.append(ev(ctx, typ, norm(first), first))
        elif "doesn't want to proceed" in t and tool.split("::")[0] == "AskUserQuestion":
            pass   # cerraste una pregunta: no es un desvío del agente
        elif "doesn't want to proceed" in t:
            out.append(ev(ctx, "user_denied", f"rechazado · {tool.split('::')[0]}",
                          tool.split("::", 1)[1] if "::" in tool else tool))
        elif re.search(r"red\.sh|verify\.py", tool):
            which = "red.sh" if "red.sh" in tool else "verify.py"
            out.append(ev(ctx, "verify_fail", f"{which} falló", t[:240]))
        return out
    if b.get("type") != "text":
        return out
    t = (b.get("text") or "").strip()
    if not t:
        return out
    if t.startswith("[Request interrupted by user"):
        out.append(ev(ctx, "interrupt", "interrupción", t))
        return out
    # Mensajes de sistema / comandos no son texto de la persona
    if t.startswith(("<", "Caveat:", "[Image")) or "system-reminder" in t[:200]:
        return out
    if plan.get(path):
        if RE_APPROVE.match(t):
            plan[path] = False
        else:
            out.append(ev(ctx, "plan_correction", "plan corregido", t))
        return out
    if RE_CORRECT.match(t):
        out.append(ev(ctx, "user_correction", "corrección", t))
    return out


# ---------- riesgos en comandos y texto de los agentes (también los muestra bk monitor) ----------
RE_FORCE = re.compile(r"\bgit\s+push\b[^\n|;&]*(--force\b|--force-with-lease|\s-f\b|\s\+\S)")
RE_CRED = re.compile(r"\bgh\s+auth\s+(login|token|refresh|switch)|\benv\s+-u\s+GH_CONFIG_DIR|\bunset\s+GH_CONFIG_DIR"
                     r"|\bGH_TOKEN=\S|\bGITHUB_TOKEN=\S")
RE_ATTR = re.compile(r"\bcomo (lo )?(decidiste|pediste|aprobaste|indicaste)\b|\b(as )?you (decided|approved|asked)\b"
                     r"|\bper your (decision|approval)\b", re.I)
RISK_HINTS = ("git push", "gh auth", "GH_CONFIG_DIR", "GH_TOKEN", "GITHUB_TOKEN", "decidiste", "pediste", "aprobaste",
              "indicaste", "you decided", "you approved", "you asked", "per your")


def agent_risks(b, ctx, is_agent):
    """force_push / credential en un comando, attribution en un texto. Solo agentes: en tus chats
    `env -u GH_CONFIG_DIR` es lo correcto (corrés gh como vos)."""
    if not is_agent:
        return []
    if b.get("type") == "tool_use":
        cmd = str((b.get("input") or {}).get("command", ""))
        if RE_FORCE.search(cmd):
            return [ev(ctx, "force_push", "force push", cmd[:200])]
        if RE_CRED.search(cmd):
            return [ev(ctx, "credential", "otra credencial de GitHub", cmd[:200])]
    elif b.get("type") == "text":
        t = b.get("text") or ""
        m = RE_ATTR.search(t)
        if m:
            return [ev(ctx, "attribution", "decisión atribuida a la persona", t[max(0, m.start() - 100): m.end() + 80])]
    return []


# ---------- hallazgos de reviews publicadas ----------
FINDING_SECTION = re.compile(r"^\s*(#+\s*|\*\*)?(blocking|blockers?|bloqueantes?|to fix|para corregir|a corregir)\b", re.I)
OTHER_SECTION = re.compile(r"^\s*(#+\s|\*\*(suggestions?|sugerencias?|questions?|preguntas?|verified|verificado)\b)", re.I)
FINDING_CLASSES = [   # (clase, patrón) — la primera que coincide; "otro hallazgo" si ninguna
    ("metadatos del PR", r"PR body|cuerpo del PR|PR's body|body of the PR|el cuerpo (dice|cita|todavía)|bloque (del rojo )?de otro|"
                         r"red block (is|from)|from another PR|de otro PR|n[uú]mero de PR"),
    ("afirmación sobre el CI", r"\bflaky\b|por azar|\bCI\b[^.]{0,60}\b(verde|green)\b|claims? (the )?CI|afirma[^.]{0,30}CI"),
    ("decisión no aprobada", r"no (lo |la )?aprob|not approved|nobody approved|nadie (la |lo )?aprob|outside (the |D\d)|fuera del ADR|"
                             r"regla nueva|new rule|sin tu (sí|aprobación)|without (your )?approval"),
    ("test que no puede fallar", r"pass(es)? (even|anyway|when .{0,30}missing)|would still pass|still green|pasa(n)? (igual|aunque)|"
                                 r"never fail|nunca falla|vacuous|without checking|sin chequear|forbidden (in|form)|formas? prohibid"),
    ("docs desactualizados", r"outdated|out of date|stale|desactualizad|contradic|still says|todavía dice|sigue diciendo|"
                             r"no longer matches|ya no coincide"),
    ("comando o flag mal usado", r"--[a-z][\w-]+[^.]{0,60}\b(only|solo|exact|exacto|no acepta|doesn'?t (match|accept)|takes only)"),
    ("el texto no coincide con el código", r"does not match the code|doesn'?t match the code|no coincide con el código|"
                                           r"the argument does not match|dice .{0,40} pero|says .{0,40} but"),
    ("caso no cubierto", r"\bno (escalation )?rule\b|nothing triggers|nada (lanza|dispara)|\bskips?\b|\bignores?\b|se saltea|"
                         r"ignora|not covered|no cubre|without the inputs|sin los datos|falls through|queda asignado sin"),
    ("entrada inesperada sin manejar", r"traceback|crash|TypeError|KeyError|malformed|mal formad|null\b|hostil|hostile|edge case|"
                                       r"caso borde|silently|en silencio|falls back silently"),
]


def review_findings(body):
    """Los ítems de las secciones Blocking / To fix (o Bloqueantes / Para corregir) de una review."""
    items, inside, cur, fence = [], False, None, False
    for l in (body or "").splitlines():
        if l.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if FINDING_SECTION.match(l):
            inside = not re.search(r"\b(none|ninguno|nada)\b", l, re.I)
            rest = re.sub(r"^.*?:\*?\*?\s*", "", l) if ":" in l else ""
            if inside and rest and not re.match(r"(none|ninguno|nada)\b", rest, re.I):
                items.append(rest)
            continue
        if OTHER_SECTION.match(l):
            inside = False
            continue
        if not inside:
            continue
        m = re.match(r"^\s*(\d+[.)]|[-*])\s+(.*)", l)
        if m and len(l) - len(l.lstrip()) < 2:   # solo los ítems de primer nivel; los anidados son detalle
            cur = m.group(2)
            items.append(cur)
        elif items and l.strip():
            items[-1] += " " + l.strip()
        elif l.strip() and re.match(r"(none|ninguno|nada)\b", l.strip().strip("*"), re.I):
            inside = False
    return [re.sub(r"\s+", " ", i).strip() for i in items if i.strip()]


def finding_class(text):
    for name, pat in FINDING_CLASSES:
        if re.search(pat, text, re.I):
            return name
    return "otro hallazgo"
