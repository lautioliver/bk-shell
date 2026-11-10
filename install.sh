#!/usr/bin/env bash
# bk shell — instalador.
#   curl -fsSL https://raw.githubusercontent.com/lautioliver/bk-shell/main/install.sh | bash
#
# Qué hace (y pregunta antes de tocar tu configuración):
#   1. revisa las dependencias: tmux >= 3.3, python3, jq, git, claude (Claude Code); gh es opcional
#   2. clona o actualiza el repo en ~/.bk-shell
#   3. enlaza ~/.local/bin/bk
#   4. agrega `source-file ~/.bk-shell/tmux/bk.conf` a tu tmux.conf            (te pregunta)
#   5. registra el hook de estado en ~/.claude/settings.json (con copia de seguridad) (te pregunta)
#   6. registra el aviso de contexto (lib/context_hook.py)                          (te pregunta)
#   7. registra la baranda de tmux (lib/tmux_guard.py)                              (te pregunta)
# Desinstalar: bash ~/.bk-shell/install.sh --uninstall
set -euo pipefail

REPO="${BK_REPO:-https://github.com/lautioliver/bk-shell.git}"
DIR="${BK_HOME:-$HOME/.bk-shell}"
BIN="$HOME/.local/bin"
SETTINGS="$HOME/.claude/settings.json"
HOOK="$DIR/hooks/agent_state.sh"
EVENTS="SessionStart UserPromptSubmit PreToolUse PostToolUse PostToolUseFailure PermissionRequest Notification Stop StopFailure SubagentStart SubagentStop SessionEnd"
# Hooks opcionales de lib/: el comando que queda en settings.json (el mismo que busca --uninstall)
shq() { python3 -c 'import shlex, sys; print(shlex.quote(sys.argv[1]))' "$1" 2>/dev/null || printf '%s' "$1"; }   # una ruta con espacios o ' no se parte
CTX_HOOK="python3 $(shq "$DIR/lib/context_hook.py")"
GUARD_HOOK="python3 $(shq "$DIR/lib/tmux_guard.py")"
# Para saber si un hook ya está: el mismo comando escrito de otra forma (el del README, con ~ o $HOME y sin
# comillas, o con comillas dobles) cuenta como el mismo. same(a, b) compara las palabras con ~ y $HOME expandidos.
SAME_PY='
import os, shlex
def words(c):
    if not isinstance(c, str):
        return [c]
    try:
        ws = shlex.split(c)
    except ValueError:
        ws = c.split()
    home = os.path.expanduser("~")
    return [os.path.normpath(os.path.expanduser(w).replace("${HOME}", home).replace("$HOME", home)) if "/" in w or w.startswith("~") else w
            for w in ws]
def same(a, b):
    return words(a) == words(b)
'

c() { printf '\033[38;2;240;149;200m%s\033[0m' "$*"; }
say() { printf '%s %s\n' "$(c ✿)" "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
die() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
# Con `curl | bash` el stdin es el script: las preguntas se leen de la terminal
ask() {
  local a
  # Sin terminal (CI, pipe sin tty): no tocar nada salvo que BK_YES=s
  if ! { exec 3</dev/tty; } 2>/dev/null; then a="${BK_YES:-n}"
  else read -r -p "$1 [S/n] " a <&3 || a=n; exec 3<&-; fi
  case "$a" in ""|s|S|si|sí|y|Y|yes) return 0 ;; *) return 1 ;; esac
}

tmux_conf() { [ -f "$HOME/.config/tmux/tmux.conf" ] && echo "$HOME/.config/tmux/tmux.conf" || echo "$HOME/.tmux.conf"; }

uninstall() {
  rm -f "$BIN/bk"
  local tc; tc=$(tmux_conf)
  [ -f "$tc" ] && sed -i.bk-bak -e '/^# bk shell$/d' -e '/bk-shell\/tmux\/bk.conf/d' "$tc" && say "saqué bk.conf de $tc"
  if [ -f "$SETTINGS" ]; then
    { printf '%s\n' "$SAME_PY"; cat <<'PY'; } | python3 - "$SETTINGS" "$HOOK" "$CTX_HOOK" "$GUARD_HOOK"
import json, sys
p, *mine = sys.argv[1:]
s = json.load(open(p))
for ev, groups in list(s.get("hooks", {}).items()):
    for g in groups:
        g["hooks"] = [h for h in g.get("hooks", []) if not any(same(h.get("command"), m) for m in mine)]
    s["hooks"][ev] = [g for g in groups if g.get("hooks")]
    if not s["hooks"][ev]:
        del s["hooks"][ev]
json.dump(s, open(p, "w"), indent=2)
PY
    say "saqué los hooks de bk de $SETTINGS (estado, aviso de contexto y baranda de tmux)"
  fi
  say "listo. Borrá $DIR si no lo vas a usar más."
  exit 0
}
[ "${1:-}" = "--uninstall" ] && uninstall

say "instalando bk shell"

# 1. Dependencias
missing=()
for cmd in tmux python3 jq git; do command -v "$cmd" >/dev/null || missing+=("$cmd"); done
if [ ${#missing[@]} -gt 0 ]; then
  hint="brew install ${missing[*]}"; [ "$(uname)" = Linux ] && hint="sudo apt install ${missing[*]}"
  die "faltan: ${missing[*]}  →  $hint"
fi
tv=$(tmux -V | sed -E 's/[^0-9.]//g'); tmaj=${tv%%.*}; tmin=$(echo "$tv" | cut -d. -f2)
{ [ "$tmaj" -gt 3 ] || { [ "$tmaj" -eq 3 ] && [ "${tmin:-0}" -ge 3 ]; }; } || die "tmux $tv es viejo: hace falta 3.3 o más"
command -v claude >/dev/null || warn "no encuentro claude (Claude Code): bk lo usa para el chat y los agentes"
command -v gh >/dev/null || warn "sin gh (GitHub CLI): no vas a ver PRs en el riel ni podrás usar bk fix/rerun"

# 2. Repo
if [ -d "$DIR/.git" ]; then
  say "actualizando $DIR"; git -C "$DIR" pull -q --ff-only
else
  say "clonando en $DIR"; git clone -q "$REPO" "$DIR"
fi
chmod +x "$DIR/bin/bk" "$DIR/hooks/agent_state.sh" "$DIR"/lib/*.py

# 3. bk en el PATH
mkdir -p "$BIN"; ln -sf "$DIR/bin/bk" "$BIN/bk"
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN no está en tu PATH: agregá  export PATH=\"\$HOME/.local/bin:\$PATH\"  a tu ~/.zshrc" ;; esac

# 4. tmux
tc=$(tmux_conf)
if grep -qs 'bk-shell/tmux/bk.conf' "$tc"; then
  say "tmux ya carga bk.conf"
elif ask "¿Agrego los atajos de bk (alt+a/g/k/s…) a $tc?"; then
  mkdir -p "$(dirname "$tc")"
  printf '\n# bk shell\nsource-file %s/tmux/bk.conf\n' "$DIR" >>"$tc"
  tmux source-file "$tc" 2>/dev/null || true
  say "listo: $tc carga bk.conf"
else
  warn "sin atajos: agregá a mano  source-file $DIR/tmux/bk.conf  a tu tmux.conf"
fi

# 5–7. Hooks de Claude Code. add_hook <comando> <matcher o ""> <eventos…>: lo suma a cada evento donde no está.
# La copia de seguridad es la de antes del primer cambio de esta corrida.
backed_up=0
add_hook() {
  local cmd="$1" matcher="$2"; shift 2
  mkdir -p "$(dirname "$SETTINGS")"
  if [ "$backed_up" -eq 0 ] && [ -f "$SETTINGS" ]; then cp "$SETTINGS" "$SETTINGS.bk-backup"; backed_up=1; fi
  { printf '%s\n' "$SAME_PY"; cat <<'PY'; } | python3 - "$SETTINGS" "$cmd" "$matcher" "$@"
import json, os, sys
p, hook, matcher, *events = sys.argv[1:]
s = json.load(open(p)) if os.path.exists(p) else {}
hooks = s.setdefault("hooks", {})
for ev in events:
    groups = hooks.setdefault(ev, [])
    if any(same(h.get("command"), hook) for g in groups for h in g.get("hooks", [])):
        continue   # ya está (aunque lo hayas escrito a mano como dice el README)
    g = {"hooks": [{"type": "command", "command": hook}]}
    if matcher:
        g = {"matcher": matcher, **g}
    groups.append(g)
json.dump(s, open(p, "w"), indent=2)
PY
}

if ask "¿Registro el hook de estado de bk en $SETTINGS? (es lo que alimenta el riel y la tira)"; then
  # shellcheck disable=SC2086  # EVENTS se parte en eventos a propósito
  add_hook "$HOOK" "" $EVENTS
  say "hook registrado (copia de seguridad: $SETTINGS.bk-backup)"
else
  warn "sin hook: el riel no va a saber qué hace cada agente"
fi

if ask "¿Registro el aviso de contexto (lib/context_hook.py)? Le avisa al agente desde 200k de contexto"; then
  add_hook "$CTX_HOOK" "" UserPromptSubmit PostToolUse
  say "aviso de contexto registrado en UserPromptSubmit y PostToolUse"
else
  warn "sin aviso de contexto (lo podés sumar después: ver el README)"
fi

if ask "¿Registro la baranda de tmux (lib/tmux_guard.py)? Frena a un agente que iba a tirar tu servidor de tmux"; then
  add_hook "$GUARD_HOOK" Bash PreToolUse
  say "baranda de tmux registrada en PreToolUse (Bash)"
else
  warn "sin baranda de tmux (la podés sumar después: ver el README)"
fi

cat <<EOF

$(c "✿ bk shell instalado")

  cd <tu proyecto>      # mejor si usa Barrilete Kit: bk run / review / fix usan sus comandos
  bk                    # abre el workspace: chat + riel de agentes + tira de sesiones
  bk help               # todos los comandos

  En macOS, Option tiene que mandar Alt para los atajos alt+… (ver README).
EOF
