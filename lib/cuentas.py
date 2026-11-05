#!/usr/bin/env python3
"""bk cuenta: cuentas de Claude Code o Codex por sesión, sin perder las conversaciones.

Cada cuenta es una carpeta de config propia (CLAUDE_CONFIG_DIR o CODEX_HOME) en
${BK_CUENTAS_DIR:-${XDG_STATE_HOME:-~/.local/state}/bk/cuentas}/<nombre>. Lo compartido (las
conversaciones, settings, skills, hooks…) es un symlink a la carpeta principal; lo propio de cada cuenta
son las credenciales (Claude Code las guarda en el llavero con un sufijo por carpeta) y .claude.json.
La cuenta "principal" es la de siempre (~/.claude, ~/.codex) y no tiene carpeta.

  cuentas.py list [--json]              las cuentas, con su proveedor y si tienen sesión iniciada
  cuentas.py new <claude|codex> <nombre>  crear la carpeta (el login lo hace bk)
  cuentas.py provider <nombre>          claude | codex (principal: vacío)
  cuentas.py env <nombre>               VAR=valor para lanzar con esa cuenta (repara los symlinks)
"""
import json
import os
import re
import subprocess
import sys

HOME = os.path.expanduser("~")
STATE = os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local", "state")
ROOT = os.environ.get("BK_CUENTAS_DIR") or os.path.join(STATE, "bk", "cuentas")
MAIN = {"claude": os.environ.get("BK_CLAUDE_MAIN") or os.path.join(HOME, ".claude"),
        "codex": os.environ.get("BK_CODEX_MAIN") or os.path.join(HOME, ".codex")}
VAR = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
# Lo que comparten todas las cuentas de un proveedor. Las carpetas marcadas con / se crean en la
# principal si faltan (projects/: sin ella, una cuenta nueva escribiría sus conversaciones aparte).
SHARED = {
    "claude": ["projects/", "settings.json", "CLAUDE.md", "barrilete-lessons.md", "skills", "plugins",
               "hooks", "agents", "commands", "output-styles", "history.jsonl", "file-history", "todos",
               "plans", "shell-snapshots", "session-env", "keybindings.json"],
    "codex": ["sessions/", "config.toml", "AGENTS.md", "hooks.json", "rules", "prompts", "history.jsonl"],
}
# De ~/.claude.json, lo que no es de la cuenta: proyectos confiados, MCP del usuario, tema.
SEED_KEYS = ("projects", "mcpServers", "theme")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


def die(msg):
    print(f"bk cuenta: {msg}", file=sys.stderr)
    sys.exit(1)


def path(name):
    return os.path.join(ROOT, name)


def provider(name):
    try:
        with open(os.path.join(path(name), "proveedor")) as f:
            return f.read().strip()
    except OSError:
        return ""


def names():
    try:
        return sorted(n for n in os.listdir(ROOT) if provider(n))
    except OSError:
        return []


def link_shared(name):
    """Crea los symlinks que falten (si la principal ganó algo nuevo, la cuenta lo ve). Nunca pisa
    un archivo real de la cuenta: si existe y no es symlink, lo deja."""
    prov, d = provider(name), path(name)
    for item in SHARED[prov]:
        is_dir = item.endswith("/")
        item = item.rstrip("/")
        src, dst = os.path.join(MAIN[prov], item), os.path.join(d, item)
        if is_dir:
            os.makedirs(src, exist_ok=True)
        if not os.path.lexists(src) or os.path.lexists(dst):
            continue
        os.symlink(src, dst)


def seed_claude_json(d):
    main = os.environ.get("BK_CLAUDE_JSON") or os.path.join(HOME, ".claude.json")
    try:
        with open(main) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    seed = {k: data[k] for k in SEED_KEYS if k in data}
    fd = os.open(os.path.join(d, ".claude.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(seed, f, indent=2)


def new(prov, name):
    if prov not in VAR:
        die(f"proveedor desconocido: {prov} (claude | codex)")
    if name == "principal" or not NAME.match(name):
        die(f"nombre inválido: {name!r} (minúsculas, números y guiones; 'principal' es la de siempre)")
    d = path(name)
    if os.path.exists(d):
        die(f"ya existe la cuenta {name} ({provider(name) or '?'}): {d}")
    os.makedirs(d, mode=0o700)
    with open(os.path.join(d, "proveedor"), "w") as f:
        f.write(prov + "\n")
    if prov == "claude":
        seed_claude_json(d)
    link_shared(name)
    print(d)


def env(name):
    if name in ("", "principal"):
        return
    prov = provider(name)
    if not prov:
        die(f"no existe la cuenta {name} (bk cuenta)")
    link_shared(name)
    print(f"{VAR[prov]}={path(name)}")


def status(prov, d):
    """(sesión iniciada, quién). Lo pregunta al CLI con la carpeta de la cuenta."""
    e = dict(os.environ)
    e.pop("CLAUDE_CONFIG_DIR", None)
    e.pop("CODEX_HOME", None)
    if d:
        e[VAR[prov]] = d
    try:
        if prov == "claude":
            r = subprocess.run(["claude", "auth", "status"], env=e, capture_output=True, text=True, timeout=15)
            j = json.loads(r.stdout or "{}")
            return bool(j.get("loggedIn")), j.get("email") or j.get("authMethod") or ""
        r = subprocess.run(["codex", "login", "status"], env=e, capture_output=True, text=True, timeout=15)
        out = (r.stdout + r.stderr).strip().splitlines()
        return r.returncode == 0, (out[0] if out else "")
    except FileNotFoundError:
        return False, f"{prov} no está instalado"
    except (subprocess.TimeoutExpired, ValueError):
        return False, "?"


def listing(as_json):
    rows = [{"name": "principal", "provider": p, "dir": MAIN[p]} for p in ("claude", "codex")]
    rows += [{"name": n, "provider": provider(n), "dir": path(n)} for n in names()]
    for r in rows:
        r["logged_in"], r["who"] = status(r["provider"], None if r["name"] == "principal" else r["dir"])
    if as_json:
        print(json.dumps(rows, ensure_ascii=False))
        return
    for r in rows:
        mark = "●" if r["logged_in"] else "○"
        print(f"{mark} {r['name']:<14} {r['provider']:<7} {r['who'] or 'sin sesión'}")


def main(argv):
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        listing("--json" in argv)
    elif cmd == "new" and len(argv) == 3:
        new(argv[1], argv[2])
    elif cmd == "provider" and len(argv) == 2:
        print(provider(argv[1]))
    elif cmd == "env" and len(argv) == 2:
        env(argv[1])
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
