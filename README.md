# ✿ bk shell

**Un workspace de agentes en la terminal para [Barrilete Kit](https://github.com/lautioliver/barrilete-kit).**
Chat con el orquestador, los agentes trabajando en vivo, las sesiones de Claude Code a un atajo y todo lo que
quedó sin commitear, en una sola sesión de tmux.

> *An agent workspace for the terminal, built on tmux and Claude Code, for projects that use Barrilete Kit.
> The UI is in Spanish. Install with the one-liner below; `bk help` lists every command.*

```
┌─┬──────────────────────────────────────────┬───────────────────────┐
│1│ ✿ mi-proyecto · ~/mi-proyecto main · opus│ ∗ 2 activos · usage   │  ← header: proyecto, rama, modelo, uso
│●│                                          │ ▎ ✿ Status            │
│2│   chat con el orquestador (claude)       │ ▎ Project / Branch    │
│○│                                          │ ▎ ∗ Agents            │  ← riel: estado, agentes,
│─│   le pedís cosas a los agentes, ellos    │ ▎ ● i42 sonnet  12m   │    tus checkpoints y
│1│   trabajan en sus ventanas y vos los     │ ▎ ◆ Checkpoints       │    lo que quedó sin commitear
│●│   ves acá al costado                     │ ▎ ✎ Changes           │
│2│                                          │                       │
└─┴──────────────────────────────────────────┴───────────────────────┘
 ↑ tira: workspaces y agentes (● trabajando · ● rojo te espera · ○ libre)
```

## Qué trae

- **Un workspace por proyecto** (`bk`): sesión de tmux `bk-<proyecto>` con el chat, el riel y la tira de sesiones.
  `bk open <proyecto>` abre otro en una ventana nueva; cada escritorio puede tener el suyo.
- **Agentes de Barrilete Kit en paralelo**: `bk run <issue>`, `bk review <pr>`, `bk fix <pr> "…"`, `bk plan "…"`,
  cada uno en su ventana y su worktree, con el modelo que diga `roles.yml`.
- **Hablarles sin entrar**: `bk say <agente> "…"`, `bk peek <agente>`, o `alt+shift+N` para traer el agente N al lugar del chat.
- **Nuevo sin salir**: `alt+n` (o el `+` de la tira) abre un chat nuevo en el proyecto o el workspace de otro proyecto.
- **Overlays**: `alt+a` agentes en vivo · `alt+g` cambios (worktrees y diff) · `alt+k` comandos · `alt+s` sesiones
  de Claude (abiertas y recientes, retomables) · `alt+u` estadísticas · `alt+,` estilo.
- **Estado real de cada agente**: trabajando, te espera (permiso, confianza en la carpeta, plan por aprobar),
  en segundo plano (comandos, monitores o subagentes que siguen corriendo) o listo.
- **Stats**: mapa de actividad, modelo favorito, rachas y costo equivalente en API de todo tu historial de Claude Code.

## Requisitos

- macOS o Linux, **tmux 3.3+**, `python3`, `jq`, `git`
- [Claude Code](https://claude.com/claude-code) (`claude`)
- Opcional: [`gh`](https://cli.github.com) para PRs y checks; un proyecto con [Barrilete Kit](https://github.com/lautioliver/barrilete-kit)
  para `run`, `review`, `fix`, `plan` y `orchestrate` (el workspace, las sesiones y las stats andan en cualquier repo)

## Instalar

```bash
curl -fsSL https://raw.githubusercontent.com/lautioliver/bk-shell/main/install.sh | bash
```

Clona en `~/.bk-shell`, enlaza `~/.local/bin/bk` y **te pregunta** antes de:
- agregar `source-file ~/.bk-shell/tmux/bk.conf` a tu `tmux.conf` (los atajos; no toca tu prefijo ni tu tema);
- registrar el hook de estado en `~/.claude/settings.json` (deja una copia en `settings.json.bk-backup`).

**macOS:** los atajos `alt+…` necesitan que Option mande Alt.
Ghostty: `macos-option-as-alt = left` · iTerm2: Profiles → Keys → Left Option key: Esc+ ·
Terminal: Ajustes → Perfiles → Teclado → "Usar Opción como tecla Meta".

Actualizar: volvé a correr el instalador. Desinstalar: `bash ~/.bk-shell/install.sh --uninstall`.

## Primeros pasos

```bash
cd mi-proyecto
bk                 # abre (o retoma) el workspace del proyecto
bk run 42          # un implementer sobre el issue #42 (proyecto con Barrilete Kit)
bk review 57       # un reviewer sobre el PR #57
bk s               # todas tus sesiones de Claude, para saltar o retomar
bk help            # todo lo demás
```

| Atajo | Qué hace |
|---|---|
| `alt+1…9` | ir al workspace N de la tira |
| `alt+shift+1…9` | traer el agente N al lugar del chat (`alt+q` lo devuelve) |
| `alt+0` | volver a la sesión anterior |
| `alt+a` · `alt+g` · `alt+k` · `alt+s` · `alt+u` · `alt+,` | agentes · cambios · comandos · sesiones · stats · estilo |
| clic en la tira | workspace: ir · agente: verlo en vivo (`q` cierra, `enter` va a su ventana) |

## Cómo funciona

- Todo es tmux: cada workspace es una sesión `bk-<proyecto>`; los agentes, ventanas de la sesión `agents`.
- El hook (`hooks/agent_state.sh`) escribe el estado de cada sesión de Claude Code en `~/.cache/bk/agents/`;
  el riel, la tira, el footer, `bk ls` y `bk s` lo leen de ahí.
- El estilo (tarjetas *float* o *neon*, densidad, header arriba o abajo) se guarda en `~/.config/bk/ui.json`.
- Los costos son el **equivalente en API** de los tokens (con una suscripción no se cobra así).

## Tests

```bash
bash tests/run.sh        # hook de estado, bk, instalador y vistas (también corre en CI: macOS y Linux)
```

Inspirado en [Gentle Shell](https://github.com/Gentleman-Programming/gentle-shell) de Gentleman Programming.
Licencia MIT.
