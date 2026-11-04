# ✿ bk shell

![estado: beta](https://img.shields.io/badge/estado-beta-f095c8) [![tests](https://github.com/lautioliver/bk-shell/actions/workflows/tests.yml/badge.svg)](https://github.com/lautioliver/bk-shell/actions/workflows/tests.yml)

> **Beta:** se usa todos los días, pero los comandos, los atajos y la forma de guardar el estado todavía
> pueden cambiar entre versiones. Si algo se rompe, abrí un issue.

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
- **Modos por rol**: la tercera columna de `roles.yml` dice cómo lanza el orquestador cada rol: `main` (la sesión
  principal), `bk` (ventana propia, te puede preguntar en el momento) o `sub` (subagente que le devuelve el resultado).
  Sin modo es `bk`. `bk mode <rol>` lo muestra; `bk roles`, todos.
- **Hablarles sin entrar**: `bk say <agente> "…"`, `bk peek <agente>`, o `alt+shift+N` para traer el agente N al lugar del chat.
- **Nuevo sin salir**: `alt+n` (o el `+` de la tira) abre otro chat del proyecto (un workspace más en la tira,
  `bk-<proyecto>-2`, `-3`…), un chat en una rama aparte o el workspace de otro proyecto.
- **Chats por rama**: `bk chat --rama <nombre>` (o `alt+n` → *Chat en una rama aparte*) crea `../<repo>-wt/chat-<nombre>`
  en la rama `chat/<nombre>`, desde la rama principal de `origin`, y lo abre como workspace `bk-<proyecto>-<nombre>`.
  Varios cambios del mismo proyecto a la vez sin pisarse; si ya existe, lo reusa.
- **Terminal rápida**: `alt+x` abre una terminal del proyecto flotando sobre el workspace (una sesión `term-<proyecto>`
  por repo, que arranca en la carpeta desde donde la abriste); `alt+x` de nuevo la esconde y vuelve como la dejaste. `bk term`.
- **Sonidos**: un sonido por tipo de pedido de los agentes, no por agente: terminó, te pregunta, pide permiso, error
  (en macOS, Glass · Ping · Purr · Basso; en Linux, los de freedesktop si hay `paplay` o `pw-play`). Suena solo cuando
  cambia el estado. `bk sound off` lo apaga, `bk sound test` los hace sonar.
- **Radio**: `bk song rock` (o techno, house, reggaeton, cachengue) pone una radio en segundo plano con `mpv`;
  `next`/`prev` pasan de radio dentro del género, `bk song ui` muestra el sonido, los géneros y los últimos temas.
  `bk song <url>` toca cualquier stream o link de YouTube/SoundCloud (con `yt-dlp`), `bk song fav` guarda la que
  suena entre tus radios y `bk song hist` lista los temas que sonaron. Cuando suena el aviso de un agente, la radio
  baja sola y después vuelve a su volumen.
- **Subagentes a la vista**: los subagentes vivos de un chat o agente (herramienta Agent) aparecen como `└` debajo de él
  en la tira y con su descripción en el riel. Clic en el `└` (o `bk sub`) sigue su sesión en vivo, de solo lectura.
- **Overlays**: `alt+a` agentes en vivo · `alt+g` cambios (worktrees y diff) · `alt+k` comandos · `alt+s` sesiones
  de Claude (abiertas y recientes, retomables) · `alt+u` estadísticas · `alt+,` estilo.
- **Agentes con nombre fácil**: `bk say "sí, publicala"` le contesta al agente que te está esperando; si son varios,
  te deja elegir. También acepta un número (`bk say 240 "…"`, aunque la ventana sea `i218`) o una palabra del título
  (`bk say glosario "…"`); lo mismo `bk go`, `bk peek` y `bk stop`. `bk ls` y el riel muestran el título del issue o
  PR de cada agente, la pregunta que te dejó, y ponen primero a los que te esperan.
- **Estado real de cada agente**: trabajando, te espera (permiso, confianza en la carpeta, plan por aprobar),
  en segundo plano (comandos, monitores o subagentes que siguen corriendo) o listo.
- **Stats**: mapa de actividad, modelo favorito, rachas y costo equivalente en API de todo tu historial de Claude Code.

## Requisitos

- macOS o Linux, **tmux 3.3+**, `python3`, `jq`, `git`
- [Claude Code](https://claude.com/claude-code) (`claude`)
- Opcional: [`gh`](https://cli.github.com) para PRs y checks; un proyecto con [Barrilete Kit](https://github.com/lautioliver/barrilete-kit)
  para `run`, `review`, `fix`, `plan` y `orchestrate` (el workspace, las sesiones y las stats andan en cualquier repo)
- Opcional para `bk song`: `mpv` (y `yt-dlp` para links de YouTube o SoundCloud)

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
bk chat --rama login  # otro chat del proyecto en su worktree y rama (chat/login)
bk sub             # los subagentes vivos, para seguir uno en vivo
bk say "sí"        # al agente que te espera (o: bk say 240 "…", bk say glosario "…")
bk sound off       # sin sonidos (on, test)
bk song cachengue  # radio de fondo (rock, reggaeton, techno, house; bk song help)
bk help            # todo lo demás
```

| Atajo | Qué hace |
|---|---|
| `alt+1…9` | ir al workspace N de la tira |
| `alt+shift+1…9` | traer el agente N al lugar del chat (`alt+q` lo devuelve) |
| `alt+0` | volver a la sesión anterior |
| `alt+a` · `alt+g` · `alt+k` · `alt+s` · `alt+u` · `alt+,` | agentes · cambios · comandos · sesiones · stats · estilo |
| `alt+n` | nuevo: otro chat del proyecto, un chat en una rama aparte o el workspace de otro proyecto |
| `alt+x` | terminal rápida del proyecto (`alt+x` la esconde) |
| clic en la tira | workspace: ir · agente: verlo en vivo (`q` cierra, `enter` va a su ventana) · `└` subagente: seguirlo en vivo |
| mouse sobre la tira | el nombre de la fila (workspace o agente N · proyecto · feature) en la barra de tmux |

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
