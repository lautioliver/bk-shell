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
│●│                                          │ ╭ ✿ Status ─────────╮ │
│2│   chat con el orquestador (claude)       │ │ Project / Branch  │ │
│○│                                          │ ╰───────────────────╯ │  ← riel: estado, agentes,
│─│   le pedís cosas a los agentes, ellos    │ ╭ ∗ Agents ─────────╮ │    y lo que quedó
│1│   trabajan en sus ventanas y vos los     │ │ ● i42 sonnet  12m │ │    sin commitear
│●│   ves acá al costado                     │ ╰───────────────────╯ │
│2│                                          │ ╭ ✎ Changes ────────╮ │
└─┴──────────────────────────────────────────┴───────────────────────┘
 ↑ tira: workspaces y agentes (⠋ trabajando · ! te espera o pide permiso · ? te pregunta · ✓ listo · ○ libre)
```

## Qué trae

- **Un workspace por proyecto** (`bk`): sesión de tmux `bk-<proyecto>` con el chat, el riel y la tira de sesiones.
  `bk open <proyecto>` abre otro en una ventana nueva; cada escritorio puede tener el suyo.
- **Agentes de Barrilete Kit en paralelo**: `bk run <issue>`, `bk review <pr>`, `bk fix <pr> "…"`, `bk plan "…"`,
  cada uno en su ventana y su worktree, con el modelo que diga `roles.yml`. Si el kit tiene candado de worktree
  (`scripts/agents/worktree-lock.sh`), `bk run` corre `prepare.sh` con el nombre de la tarea, suelta el candado
  antes de lanzar (si es de otro agente vivo, no lanza) y `bk done` suelta el del agente al cerrar.
- **Modos por rol**: la tercera columna de `roles.yml` dice cómo lanza el orquestador cada rol: `main` (la sesión
  principal), `bk` (ventana propia, te puede preguntar en el momento) o `sub` (subagente que le devuelve el resultado).
  Sin modo es `bk`. `bk mode <rol>` lo muestra; `bk roles`, todos.
- **Hablarles sin entrar**: `bk say <agente> "…"`, `bk peek <agente>`, o `alt+shift+N` para traer el agente N al lugar del chat.
- **Nuevo sin salir**: `alt+n` (o el `+` de la tira) abre otro chat del proyecto (un workspace más en la tira,
  `bk-<proyecto>-2`, `-3`…), un chat en una rama aparte o el workspace de otro proyecto.
- **Chats por rama**: `bk chat --rama <nombre>` (o `alt+n` → *Chat en una rama aparte*) crea `../<repo>-wt/chat-<nombre>`
  en la rama `chat/<nombre>`, desde la rama principal de `origin`, y lo abre como workspace `bk-<proyecto>-<nombre>`.
  Varios cambios del mismo proyecto a la vez sin pisarse; si ya existe, lo reusa.
- **Seguir en un chat nuevo (handoff)**: `bk handoff [N]` le pide al chat del workspace N (o de este) un resumen con
  una plantilla fija (hecho, qué falta en orden, qué te espera, qué no tocar, archivos y comandos), que escribe en
  `${XDG_STATE_HOME:-~/.local/state}/bk/handoffs/<sesión>.md`; bk completa solo de dónde viene (workspace, rama,
  carpeta y cuenta). La tira y `alt+s` marcan ese chat con `↪`. `bk chat --desde N` (o `alt+n` → *Seguir desde ws N*)
  abre un workspace más del proyecto, en esa carpeta y con esa cuenta, que arranca con el resumen como primer mensaje
  y lo borra; después pregunta si cerrás el viejo. `bk handoff --listos` los lista. El rojo de
  `lib/context_hook.py` pide el mismo resumen.
- **Terminal rápida**: `alt+x` abre una terminal del proyecto flotando sobre el workspace (una sesión `term-<proyecto>`
  por repo, que arranca en la carpeta desde donde la abriste); `alt+x` de nuevo la esconde y vuelve como la dejaste. `bk term`.
- **Sonidos**: un sonido por tipo de pedido de los agentes, no por agente: terminó, te pregunta, pide permiso, error
  (en macOS, Glass · Ping · Purr · Basso; en Linux, los de freedesktop si hay `paplay` o `pw-play`). Suena solo cuando
  cambia el estado. `bk sound off` lo apaga, `bk sound test` los hace sonar.
- **Radio**: `bk song rock` (o techno, house, reggaeton, cachengue) pone una radio en segundo plano con `mpv`;
  `next`/`prev` pasan de radio dentro del género y `bk song ui` muestra el espectro del sonido en vivo (28 franjas,
  de graves a agudos) con las radios del género y control con teclas (`?` las muestra). El filtro que mide esas
  franjas cuesta CPU: mpv lo tiene solo mientras `bk song ui` está abierta (lo agrega al abrir y en cada estación
  nueva, y lo saca al cerrar); la radio de fondo suena sin él.
  `bk song <url>` toca cualquier stream o link de YouTube/SoundCloud (con `yt-dlp`), `bk song fav` guarda la que
  suena entre tus radios y `bk song hist` lista los temas que sonaron. Cuando suena el aviso de un agente, la radio
  baja sola y después vuelve a su volumen.
- **Subagentes a la vista**: los subagentes vivos de un chat o agente (herramienta Agent) aparecen como `└` debajo de él
  en la tira, como `<ventana>.<orden> <tipo>` (`3.2 reviewer`: el 2.º que lanzó el workspace 3; el orden cuenta los que
  ya terminaron y no cambia), y con su descripción en el riel. Clic en el `└` (o `bk sub`) sigue su sesión en vivo,
  de solo lectura.
- **Número fijo por workspace**: cada workspace toma al crearse el número libre más bajo y lo conserva aunque cierres
  otros (`@bk_id`; `bk _ids` los lista). `alt+N` va a ese número; la tira y el header lo muestran con su color, y
  `bk ls`, antes del nombre del chat. Los workspaces del mismo proyecto quedan juntos en la tira, unidos con una línea.
- **Quién lanzó a quién**: cada agente bk queda colgado (`└`) de la ventana que lo lanzó (un workspace u otro agente),
  y en la lista de abajo dice de dónde viene: `i7 ○ ←3` (←? si esa ventana ya se cerró). bk lo anota al lanzar
  (`@bk_parent`).
- **Overlays**: `alt+a` agentes en vivo · `alt+g` cambios (worktrees y diff) · `alt+k` comandos · `alt+s` sesiones
  de Claude (abiertas y recientes, retomables) · `alt+u` estadísticas · `alt+,` estilo.
- **Agentes con nombre fácil**: `bk say "sí, publicala"` le contesta al agente que te está esperando; si son varios,
  te deja elegir. También acepta un número (`bk say 240 "…"`, aunque la ventana sea `i218`) o una palabra del título
  (`bk say glosario "…"`); lo mismo `bk go`, `bk peek` y `bk stop`. `bk ls` y el riel muestran el título del issue o
  PR de cada agente, la pregunta que te dejó, y ponen primero a los que te esperan.
- **Chats con nombre**: cada workspace se llama como lo nombres (`bk name orquestador`, o `bk chat --nombre X` al
  abrirlo); si no, como lo renombraste con `/rename` o con el título que le puso Claude. `bk ls` los lista con sus
  subagentes, `bk go orquestador` te lleva y `bk say orquestador "…"` le escribe. `bk sub <palabra>` sigue al
  subagente vivo cuya descripción la contiene.
- **Monitor**: `bk monitor` es una pantalla para dejar abierta en otro monitor. Arriba, **Te esperan**: preguntas,
  permisos y reviewers propuestos, con el comando para contestar (`bk say …`, `bk approve …`). Después los agentes y
  los chats en tablas separadas (estado en palabras, herramienta, hace cuánto, tarea, contexto, costo de hoy, subagentes), las
  **tareas** (estado, cómo cerró, último evento, `bk say` en cola) y los desvíos en vivo, agrupados (×N): bloqueos de hooks y barandas,
  `red.sh`/`verify.py` que fallan, correcciones e interrupciones tuyas, permisos rechazados, agentes trabados, el mismo
  error repetido, force push, otra credencial, «CI en verde» con el CI rojo y decisiones que te atribuyen. Los graves
  suenan (Hero, fuerte) y notifican una vez. `enter` muestra la pantalla de un agente, `tab` + `d` el detalle de un desvío.
- **Estado real de cada agente**: trabajando, te espera (permiso, confianza en la carpeta, plan por aprobar),
  en segundo plano (comandos, monitores o subagentes que siguen corriendo) o listo.
- **Tareas** ([ADR-005 del kit](https://github.com/lautioliver/barrilete-kit/blob/main/docs/decisions/kit/ADR-005-tasks-and-events-for-bk-agents.md)):
  cada agente de `bk run`, `bk review` o `bk fix` trabaja sobre una tarea (`<issue>-<rol>`, `pr<n>-<rol>`) con su
  estado y sus eventos. Un segundo `bk run` sobre el mismo issue no lanza otro agente. El agente la cierra con
  `bk done pr <n>` (o `blockers`, `dropped "…"`, `escalate "…"`); si termina sin cerrarla queda `incomplete`, y
  `bk stop` la deja `cancelled`. Cuando un implementer abre un PR, `bk ls` propone su reviewer y lo lanzás con
  `bk approve pr<n>-reviewer`: nunca arranca solo. Si al implementer lo lanzó `/orchestrate` (`bk run --orchestrated`), no hay propuesta: el orquestador lanza al reviewer. `bk say` a un agente que trabaja se encola hasta que termine
  el turno (`--now` para ya). `bk tasks` las lista con su carpeta; `bk tasks prune --yes` borra las terminadas.
  `bk ls`, el riel y `alt+a` muestran cómo cerró cada agente y los mensajes en cola.
- **Checkpoints**: `bk checkpoints` junta lo que solo vos podés hacer, con el comando para hacerlo: planes por aprobar,
  reviewers propuestos, PRs que te pidieron revisar, PRs tuyos listos para mergear y lecciones propuestas. Es la misma
  lista que *Te esperan* en `bk monitor` (`--json` para la barra u otros scripts). No está en el riel, para no llenarlo.
- **Lecciones de los desvíos** (`bk learn`): lee los transcripts de Claude Code, guarda los desvíos de los agentes
  (barandas, hooks, permisos rechazados, correcciones, hallazgos de review…) en un vault, una carpeta de notas en
  markdown que elegís con `BARRILETE_VAULT` (sin ella, estos comandos te dicen qué poner y salen con error), y con 3
  repeticiones en 14 días propone una lección (`agents/lessons/`, `status: proposed`). `bk lessons` lista las que
  esperan; las que marcás `accepted` pasan a `agents/INDEX.md`, que pueden leer los agentes. `bk learn effect` mide si
  cada regla aceptada bajó los hallazgos de review de su clase (antes/después por ronda; `agents/efecto.md`) y
  `bk metrics` da, por PR, costo, rondas de review, desvíos y bloqueantes. Las retros y reviews de GitHub salen de los
  repos que pongas en `~/.config/bk/retros.json` (`{"repos": ["~/mi-proyecto"]}`); sin ese archivo, `bk learn` y `bk learn effect` no
  salen a GitHub (`bk metrics` sí: consulta con `gh` los repos de las sesiones que encuentra).
  `bk learn` no corre solo: corrélo a mano o desde tu propio cron.
- **Cuentas por sesión**: `bk account new claude trabajo` crea otra cuenta de Claude Code (o `codex`) y hace su
  login; `bk account use trabajo` la pone en esta sesión: los agentes y chats que lances desde acá corren con ella,
  y otra sesión puede usar otra al mismo tiempo. Las conversaciones son las mismas (`/resume` y `alt+s` ven todas);
  `--relaunch` también cambia el chat principal, retomando su conversación. `bk run … --account X` solo para un
  lanzamiento; `bk account use default` vuelve a la de siempre (`bk account login default` rehace su login). El riel muestra siempre la cuenta de la sesión
  en Status, también la default, con el mail con el que está logueada (el nombre de la cuenta si no tiene login;
  lo pregunta cada 5 min con `accounts.py list --json`, en segundo plano: el riel no espera, y hasta que llega
  muestra el nombre).
- **Contexto a la vista**: cada llamada de una sesión vuelve a leer (y a pagar) todo su contexto. Desde 200k
  (ámbar) y 400k (rojo) la tira marca la sesión con `▪` (el número, en el tooltip), el riel muestra el de cada agente
  y el de este chat con la sugerencia de `/clear` o un chat nuevo, y `bk monitor` lo tiene en la columna CTX.
  Los umbrales se cambian con `BK_CTX_WARN` y `BK_CTX_HIGH` (en tokens: `150000` o `150k`; el rojo nunca queda antes
  que el ámbar; un valor que no es un número deja el de siempre). Opcional: `lib/context_hook.py` se lo dice al agente, con los mismos umbrales (ver *Aviso de contexto*).
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
- registrar el hook de estado en `~/.claude/settings.json` (deja una copia en `settings.json.bk-backup`);
- registrar el aviso de contexto (`lib/context_hook.py`) y la baranda de tmux (`lib/tmux_guard.py`), cada uno con
  su pregunta (ver abajo).

Sin terminal (CI, un pipe sin tty) no toca nada; `BK_YES=s` contesta que sí a todo. Si ya registraste un hook a mano
(como en los ejemplos de abajo, con `~/.bk-shell/…` o `$HOME/.bk-shell/…`), reinstalar no lo duplica. `--uninstall`
saca los tres hooks de `settings.json`, también los que sumaste a mano así, y deja los tuyos.

**macOS:** los atajos `alt+…` necesitan que Option mande Alt.
Ghostty: `macos-option-as-alt = left` · iTerm2: Profiles → Keys → Left Option key: Esc+ ·
Terminal: Ajustes → Perfiles → Teclado → "Usar Opción como tecla Meta".

### Aviso de contexto (opcional)

`lib/context_hook.py` es un hook de Claude Code que, al cruzar 200k de contexto, le pide al agente que cuando
termine recomiende `/clear` o un chat nuevo; a 400k, que cierre en un punto estable y deje un resumen para seguir
en otra sesión (después avisa cada +100k). Los agentes de bk (con `BK_TASK_DIR`) cierran con su `bk done`. No
avisa dentro de subagentes, nunca bloquea y guarda lo último avisado en `~/.cache/bk/context_hook/`. El instalador
te pregunta si lo registra (en `UserPromptSubmit` y `PostToolUse`); si dijiste que no, volvé a correrlo o sumalo a
mano en `~/.claude/settings.json`:

```json
{ "hooks": {
  "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "python3 ~/.bk-shell/lib/context_hook.py" }] }],
  "PostToolUse":      [{ "hooks": [{ "type": "command", "command": "python3 ~/.bk-shell/lib/context_hook.py" }] }]
} }
```

(si ya tenés esos eventos, agregá el `{"type": "command", …}` a su lista en vez de reemplazarla).
A 400k le pide al agente el resumen con la plantilla de `lib/handoff.py` (ver *Seguir en un chat nuevo*).

### Baranda de tmux (opcional)

`lib/tmux_guard.py` es un hook `PreToolUse` de Claude Code para que un agente no tire tu tmux por error: frena
`tmux kill-server` que no apunta a otro servidor (`-L`, `-S`, `env -u TMUX tmux …` o un `unset TMUX` antes; con
la variable `TMUX` definida, `TMUX_TMPDIR` no alcanza y mata el tuyo). `env -u TMUX` y `TMUX=` valen solo para el
comando que les sigue: en `env -u TMUX bash tests/run.sh; tmux kill-server` el segundo va a tu servidor y se frena.
También frena `pkill`/`killall` de tmux, `kill <pid>` del server y `kill` de lo que da `pgrep`/`pidof`/`grep` de
tmux (`kill $(pgrep tmux)`, `pgrep tmux | xargs kill`), y pregunta antes de `tmux kill-session` de `agents` o de
un `bk-…`. Es contra accidentes, no contra quien quiera esquivarlo. El
instalador te pregunta si la registra (en `PreToolUse`, matcher `Bash`); si dijiste que no, volvé a correrlo o
sumala a mano en `~/.claude/settings.json`:

```json
{ "hooks": {
  "PreToolUse": [{ "matcher": "Bash", "hooks": [{ "type": "command", "command": "python3 ~/.bk-shell/lib/tmux_guard.py" }] }]
} }
```

Actualizar: volvé a correr el instalador (no repite los hooks que ya tenés). Desinstalar:
`bash ~/.bk-shell/install.sh --uninstall`. Si lo probás con otro `HOME`, corrélo con `env -u TMUX TMUX_TMPDIR=$(mktemp -d)`:
al sumar los atajos recarga tu `tmux.conf` en el servidor de tmux, y sin eso sería el tuyo.

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
bk monitor         # en el otro monitor: qué te espera, agentes, tareas, costo y desvíos en vivo
bk sound off       # sin sonidos (on, test)
bk song cachengue  # radio de fondo (rock, reggaeton, techno, house; bk song help)
bk help            # todo lo demás
```

| Atajo | Qué hace |
|---|---|
| `alt+1…9` | ir al workspace con ese número (el fijo de la tira y el header) |
| `alt+shift+1…9` | traer el N-ésimo agente de la lista de abajo de la tira al lugar del chat (`alt+q` lo devuelve) |
| `alt+0` | volver a la sesión anterior |
| `alt+a` · `alt+g` · `alt+k` · `alt+s` · `alt+u` · `alt+,` | agentes · cambios · comandos · sesiones · stats · estilo |
| `alt+n` | nuevo: otro chat del proyecto, un chat en una rama aparte, seguir desde un resumen (handoff) o el workspace de otro proyecto |
| `alt+x` | terminal rápida del proyecto (`alt+x` la esconde) |
| clic en la tira | workspace: ir · agente (o `└` de un agente bk): verlo en vivo (`q` cierra, `enter` va a su ventana) · `└` subagente: seguirlo en vivo |
| mouse sobre la tira | el nombre de la fila (workspace N o agente · proyecto · feature · quién lo lanzó) en la barra de tmux |

## Cómo funciona

- Todo es tmux: cada workspace es una sesión `bk-<proyecto>`; los agentes, ventanas de la sesión `agents`.
- El hook (`hooks/agent_state.sh`) escribe el estado de cada sesión de Claude Code en `~/.cache/bk/agents/`
  (`BK_STATE_DIR` cambia la carpeta); el riel, la tira, el footer, `bk ls`, `bk s` y `bk monitor` lo leen
  de ahí.
- Las tareas (`lib/tasks.py`) viven en `${XDG_STATE_HOME:-~/.local/state}/bk/tasks/<proyecto>/<id>/`: `task.json`
  (el estado) y `events.jsonl` (un hecho por línea, con `seq`). El agente recibe esa carpeta en `BK_TASK_DIR`.
  Nada se borra solo (`BK_TASKS_DIR` cambia la carpeta).
- Cada cuenta (`lib/accounts.py`) es una carpeta de config en `${XDG_STATE_HOME:-~/.local/state}/bk/accounts/<nombre>`
  que se pasa como `CLAUDE_CONFIG_DIR` (o `CODEX_HOME`). Lo compartido (`projects/` con las conversaciones,
  `settings.json`, `CLAUDE.md`, skills, plugins, hooks, historial) es un symlink a `~/.claude` (o `~/.codex`); lo
  propio son las credenciales (Claude Code las guarda en el llavero con un sufijo por carpeta) y `.claude.json`.
- Las tarjetas del riel y de las vistas tienen un solo estilo: marco redondeado con título. Lo que se elige con
  `alt+,` (el header arriba o abajo) se guarda en `~/.config/bk/ui.json`.
- El riel se redibuja cada segundo y solo si algo cambió; con su workspace oculto no arma nada y mira cada 3 s si
  volvió a estar a la vista.
- Comandos propios (`bk foo` que bk no conoce) y enganches para sumar lo tuyo desde otro repo: ver
  [Comandos propios](#comandos-propios).
- Las proporciones salen de `lib/layout.conf` (tira, riel y cuánto no cede el chat). Al cambiar el tamaño de la
  ventana (el hook `window-resized` de `tmux/bk.conf`) el riel se achica para dejarle al chat 80 columnas y, si
  no entra, el chat pasa a pantalla completa con el zoom de tmux hasta que vuelve a haber lugar; un zoom hecho a
  mano no se toca. Acomoda de a uno por sesión (un lock en `$TMPDIR` con el pid del dueño: vence solo si ese
  proceso murió). Los hooks y atajos de `tmux/bk.conf` le pasan a sh el nombre de la sesión con `#{q:…}`: un
  nombre con espacios, `;` o `$(…)` no se parte ni se ejecuta.
- Antes de lanzar un agente, bk mira la memoria libre (en Linux, `MemAvailable` de `/proc/meminfo`; en macOS,
  `memory_pressure`): con menos de `BK_MEM_MIN` % (15) no lanza, porque sin memoria el sistema puede matar al
  server de tmux con todo adentro. `BK_FORCE=1` lo saltea; sin ninguno de los dos datos no frena.
- `bk --dry-run` (con `bk`, `bk chat`, `--rama`, `--suelto`, `--desde` o un lanzamiento) dice qué abriría o lanzaría y no
  crea nada: ni sesiones, ni ramas, ni worktrees.
- `bk s`: el chat de cada workspace se ve como `ws N` (su número fijo, con su color); `x` cierra un agente o,
  con confirmación, un workspace entero (no el tuyo). Un estado cuyo pane de tmux ya no existe se borra y la
  conversación pasa a recientes.
- Los costos son el **equivalente en API** de los tokens (con una suscripción no se cobra así).

## Comandos propios

Como en git: si `bk` no conoce `bk foo`, corre `bk-foo` del `PATH` o `${BK_PLUGINS:-~/.config/bk/plugins}/foo`
(el primero que encuentre, y tiene que ser ejecutable) con el resto de los argumentos. Sirve para sumar comandos
desde otro repo sin tocar este. Los comandos de bk tienen prioridad (un `bk-ls` nunca corre), y el nombre solo
puede tener letras, números, `-` y `_`.

Uno mínimo:

```sh
#!/bin/sh
# bk hola [nombre]: saluda y cuenta cuántos agentes hay
[ "$DRY" = 1 ] && { echo "(dry-run: saludaría)"; exit 0; }
n=$(tmux list-windows -t "$BK_SESSION" -F x 2>/dev/null | grep -c .)
echo "hola ${1:-vos}: $n agentes"
```

- Lo que recibe en el entorno: `BK_WS` (la carpeta de bk, para usar sus `lib/`), `BK_SESSION` (la sesión de tmux de
  los agentes, `agents` si no la cambiaste), `BK_STATE_DIR` (la carpeta del estado de los agentes) y `DRY=1` si vino
  `--dry-run` (`DRY=0` si no: el comando decide qué no hacer). Su código de salida es el de `bk`.
- `bk help` termina con **Comandos propios**: los que encuentra, uno por nombre (sin los que tapa un comando de bk)
  y, como descripción, la primera línea de sus primeras 5 que empiece con `# bk <nombre>` (en el ejemplo:
  `[nombre]: saluda y cuenta cuántos agentes hay`), sin caracteres de control. Sin esa línea sale solo el nombre.

Enganches: bk corre solo algunos comandos propios con nombre fijo, si existen.

- **`on-empty`**: `bk` o `bk ws` sin ningún workspace abierto (ninguna sesión de tmux que empiece con `bk-`) lo corre
  antes de abrir el workspace; por ejemplo, para reabrir lo que tenías antes de reiniciar. Su código de salida no
  importa: el workspace se abre igual. No corre si `BK_RESTORING` tiene algo (lo pone tu restore mientras reabre,
  para no entrar en un círculo) ni con `--dry-run` (ahí bk solo dice que lo correría). bk lo corre con
  `BK_RESTORING=1`: si llama a `bk` o `bk ws` para reabrir, esas llamadas no lo vuelven a correr.
- **`launch-env`**: antes de crear la ventana de un agente, bk lo corre con `BK_LAUNCH_CWD` (la carpeta del agente) y
  `BK_LAUNCH_ROLE` (su rol) y pasa a la ventana, como variables de entorno, cada línea de su salida con la forma
  `NOMBRE=valor` (el nombre: letras, números y `_`, sin empezar con número). Las demás líneas y lo que diga por
  stderr se ignoran; si sale con error, el agente se lanza sin esas variables. Las de bk (`BARRILETE_ROLE`, la
  cuenta, `BK_TASK_DIR`) ganan si se repiten. Con `--dry-run` no corre.

  ```sh
  #!/bin/sh
  # bk launch-env: el gh de un bot para los reviewers
  [ "$BK_LAUNCH_ROLE" = reviewer ] && echo "GH_CONFIG_DIR=$HOME/.cache/bk/bot/gh"
  exit 0
  ```

  Si `GH_CONFIG_DIR` está dentro de `${BK_CACHE_DIR:-~/.cache/bk}/bot/`, bk lo saca (y `GH_TOKEN`) al arrancar: un
  chat que heredó el entorno de un agente vuelve a usar tu gh, no el del bot.
- **`palette.d`**: la paleta (`alt+k`), después de sus entradas, suma las de
  `${BK_CONFIG_DIR:-~/.config/bk}/palette.d/*.tsv`, los archivos en orden alfabético. Una por línea, separada con
  tabs: `grupo`, `etiqueta`, `comando` (lo que va después de `bk`), el argumento que pide (vacío si no pide nada) y el
  tipo: `run` o `show` (deja la salida en pantalla hasta una tecla) o `view` (reemplaza a la paleta). Las líneas
  vacías, las que empiezan con `#`, las que no tienen esos 5 campos (o un tipo que no es uno de esos) y las que llevan
  un carácter de control (como NUL o ESC) no cuentan.

  ```
  # grupo	etiqueta	comando	argumento	tipo
  Mías	Saludar	hola	nombre	show
  ```

## Tests

```bash
bash tests/run.sh        # hook de estado, bk, tareas, cuentas, instalador y vistas (también corre en CI: macOS y Linux)
```

Los tests corren con `BK_NO_WARM=1` (lo pone `tests/lib.sh`): bk no deja corriendo `agents.py warm` al lanzar un
agente ni consulta a GitHub los títulos de issues y PRs. Fuera de los tests sirve igual si no querés que bk salga
a la red por eso.

Inspirado en [Gentle Shell](https://github.com/Gentleman-Programming/gentle-shell) de Gentleman Programming.
Licencia MIT.
