#!/usr/bin/env python3
"""bk sub: ver en vivo la sesión de un subagente (los de la herramienta Agent, que no son ventanas).

  bk sub                lista los subagentes vivos de todas las sesiones; elegís uno con su número
  bk sub <agent.jsonl>  sigue ese transcript: lo que dice, las herramientas que usa y sus resultados

Solo lectura: a un subagente no se le escribe (lo maneja el chat que lo lanzó).
Teclas: q / esc cierran · g arriba de todo · espacio sigue el final.
"""
import glob
import json
import os
import re
import select
import shutil
import sys
import termios
import time
import tty

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from sidebar import PROJECTS, clean, live_meta  # noqa: E402

R, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
ROSE, IRIS, AMBER, GREEN, RED = ("\033[38;5;218m", "\033[38;5;147m", "\033[38;5;222m",
                                 "\033[38;5;150m", "\033[38;5;210m")


def project_label(dirname):
    """La carpeta de ~/.claude/projects es la ruta del proyecto con / y . cambiados por -: sin el home."""
    home = re.sub(r"[^A-Za-z0-9]", "-", os.path.expanduser("~"))
    return (dirname[len(home):] if dirname.startswith(home) else dirname).strip("-") or dirname


def live_subagents():
    """Subagentes vivos de todas las sesiones: (descripción, tipo, segundo plano, ruta, proyecto, edad).
    Misma regla que la tira (sidebar.live_meta): sin finished y con el transcript escrito hace poco."""
    out, now = [], time.time()
    for m in glob.glob(os.path.join(glob.escape(PROJECTS), "*", "*", "subagents", "agent-*.meta.json")):
        meta = live_meta(m, now)
        if meta is None:
            continue
        j = m[:-len(".meta.json")] + ".jsonl"
        try:
            age = now - os.path.getmtime(j)
        except OSError:
            continue
        proj = project_label(os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(m)))))
        out.append((clean(meta.get("description") or meta.get("agentType") or "subagente"),
                    clean(meta.get("agentType")), meta.get("requestShape") == "background", j, proj, age))
    return sorted(out, key=lambda x: x[5])


def short(v, n):
    s = clean(" ".join(str(v).split()))
    return s if len(s) <= n else s[: n - 1] + "…"


def tool_arg(name, inp):
    if not isinstance(inp, dict):
        return short(inp, 200)
    for k in ("command", "file_path", "pattern", "description", "url", "query", "prompt"):
        if inp.get(k):
            return short(inp[k], 200)
    return short(json.dumps(inp, ensure_ascii=False), 200)


def render(path, W):
    """Líneas legibles del transcript: texto del agente, herramientas y resultados (cortos)."""
    lines = []
    try:
        with open(path, errors="replace") as f:
            raws = f.readlines()
    except OSError:
        return [f"{RED}no puedo leer {clean(path)}{R}"]
    first = True
    for raw in raws:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        t, m = e.get("type"), e.get("message") or {}
        c = m.get("content")
        if t == "user" and first:          # el pedido con el que lo lanzaron
            first = False
            txt = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
            lines.append(f"{ROSE}{BOLD}▸ pedido{R} {DIM}{short(txt, W * 3)}{R}")
            continue
        if not isinstance(c, list):
            continue
        for x in c:
            if not isinstance(x, dict):
                continue
            k = x.get("type")
            if t == "assistant" and k == "text" and x.get("text", "").strip():
                # Texto del modelo: varias líneas, pero sin secuencias de escape que muevan la pantalla
                txt = "\n".join(clean(l) for l in x["text"].strip().split("\n"))
                lines.append(f"{IRIS}⏺{R} {txt}")
            elif t == "assistant" and k == "tool_use":
                lines.append(f"{AMBER}⏺ {clean(x.get('name'))}{R} {DIM}{tool_arg(x.get('name'), x.get('input'))}{R}")
            elif t == "user" and k == "tool_result":
                body = x.get("content")
                if isinstance(body, list):
                    body = " ".join(y.get("text", "") for y in body if isinstance(y, dict))
                mark = f"{RED}✗{R}" if x.get("is_error") else f"{GREEN}⎿{R}"
                lines.append(f"  {mark} {DIM}{short(body or '', W - 6)}{R}")
    # cortar a lo ancho, respetando saltos
    out = []
    for l in lines:
        for part in l.split("\n"):
            out.append(part)
    return out


def visible_len(s):
    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def follow(path, title):
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd) if os.isatty(fd) else None
    if old:
        tty.setcbreak(fd)
    sys.stdout.write("\033[?25l")
    pinned, top = True, 0
    try:
        last = None
        while True:
            W, H = shutil.get_terminal_size((100, 30))
            mt = os.path.getmtime(path) if os.path.exists(path) else 0
            lines = render(path, W) if mt != last else lines  # noqa: F821
            last = mt
            # envolver a W columnas
            wrapped = []
            for l in lines:
                while visible_len(l) > W:
                    cut = W
                    wrapped.append(l[:cut])
                    l = "  " + l[cut:]
                wrapped.append(l)
            body_h = H - 2
            if pinned:
                top = max(0, len(wrapped) - body_h)
            view = wrapped[top: top + body_h]
            alive = time.time() - mt < 30
            state = f"{GREEN}● trabajando{R}" if alive else f"{DIM}○ quieto {int((time.time() - mt) // 60)}m{R}"
            head = f"{ROSE}{BOLD}↳ {title}{R}  {state}  {DIM}q cerrar · ↑↓ mover · espacio seguir el final{R}"
            sys.stdout.write("\033[H\033[2J" + head[: W * 3] + "\n" + "─" * W + "\n" + "\n".join(view))
            sys.stdout.flush()
            r, _, _ = select.select([sys.stdin], [], [], 1.0)
            if r:
                k = os.read(fd, 8).decode(errors="ignore")
                if k in ("q", "\x1b") :
                    return
                if k in ("\x1b[A", "k"):
                    pinned, top = False, max(0, top - 3)
                elif k in ("\x1b[B", "j"):
                    top = min(max(0, len(wrapped) - body_h), top + 3)
                    pinned = top >= len(wrapped) - body_h
                elif k == "g":
                    pinned, top = False, 0
                elif k in (" ", "G"):
                    pinned = True
    finally:
        sys.stdout.write("\033[?25h\033[H\033[2J")
        if old:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main():
    args = sys.argv[1:]
    if args:
        p = args[0]
        meta = p[:-len(".jsonl")] + ".meta.json"
        try:
            m = json.load(open(meta))
            title = clean(m.get("description") or m.get("agentType")) or os.path.basename(p)
        except Exception:
            title = os.path.basename(p)
        follow(p, title)
        return
    subs = live_subagents()
    if not subs:
        print(f"{DIM}no hay subagentes vivos{R}")
        return
    for i, (desc, typ, bg, _, proj, age) in enumerate(subs, 1):
        tag = f"{AMBER}2º plano{R}" if bg else f"{IRIS}trabajando{R}"
        print(f" {BOLD}{i:>2}{R}  {desc:<40.40} {DIM}{typ:<16.16} {proj:<28.28}{R} {tag}")
    try:
        n = input(f"\n{ROSE}número (enter cierra) › {R}").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if n.isdigit() and 1 <= int(n) <= len(subs):
        d = subs[int(n) - 1]
        follow(d[3], d[0])


if __name__ == "__main__":
    main()
