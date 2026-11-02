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
# Desinstalar: bash ~/.bk-shell/install.sh --uninstall
set -euo pipefail

REPO="${BK_REPO:-https://github.com/lautioliver/bk-shell.git}"
DIR="${BK_HOME:-$HOME/.bk-shell}"
BIN="$HOME/.local/bin"
SETTINGS="$HOME/.claude/settings.json"
HOOK="$DIR/hooks/agent_state.sh"
EVENTS="SessionStart UserPromptSubmit PreToolUse PostToolUse PostToolUseFailure PermissionRequest Notification Stop StopFailure SubagentStart SubagentStop SessionEnd"

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
    python3 - "$SETTINGS" "$HOOK" <<'PY'
import json, sys
p, hook = sys.argv[1:]
s = json.load(open(p))
for ev, groups in list(s.get("hooks", {}).items()):
    for g in groups:
        g["hooks"] = [h for h in g.get("hooks", []) if h.get("command") != hook]
    s["hooks"][ev] = [g for g in groups if g.get("hooks")]
    if not s["hooks"][ev]:
        del s["hooks"][ev]
json.dump(s, open(p, "w"), indent=2)
PY
    say "saqué el hook de $SETTINGS"
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

# 5. Hook de Claude Code
if ask "¿Registro el hook de estado de bk en $SETTINGS? (es lo que alimenta el riel y la tira)"; then
  mkdir -p "$(dirname "$SETTINGS")"
  [ -f "$SETTINGS" ] && cp "$SETTINGS" "$SETTINGS.bk-backup"
  python3 - "$SETTINGS" "$HOOK" $EVENTS <<'PY'
import json, os, sys
p, hook, *events = sys.argv[1:]
s = json.load(open(p)) if os.path.exists(p) else {}
hooks = s.setdefault("hooks", {})
for ev in events:
    groups = hooks.setdefault(ev, [])
    if any(h.get("command") == hook for g in groups for h in g.get("hooks", [])):
        continue
    groups.append({"hooks": [{"type": "command", "command": hook}]})
json.dump(s, open(p, "w"), indent=2)
PY
  say "hook registrado (copia de seguridad: $SETTINGS.bk-backup)"
else
  warn "sin hook: el riel no va a saber qué hace cada agente"
fi

cat <<EOF

$(c "✿ bk shell instalado")

  cd <tu proyecto>      # mejor si usa Barrilete Kit: bk run / review / fix usan sus comandos
  bk                    # abre el workspace: chat + riel de agentes + tira de sesiones
  bk help               # todos los comandos

  En macOS, Option tiene que mandar Alt para los atajos alt+… (ver README).
EOF
