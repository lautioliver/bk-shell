"""Detectores de desvíos de los agentes, compartidos por `bk learn` (los guarda en el vault) y
`bk monitor` (los muestra en vivo). Cada uno mira un bloque de un mensaje del transcript de Claude Code.

  guardrail_block   una baranda frenó un comando        hook_block       otro hook PreToolUse frenó una herramienta
  user_denied       la persona rechazó un permiso       interrupt        la persona interrumpió al agente
  verify_fail       red.sh / verify.py falló             contract_*       un subagente devolvió escalate/blockers/question/dropped
  plan_correction   el plan no recibió un "sí"          user_correction  la persona corrigió al agente
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
