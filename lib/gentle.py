"""Estilo compartido de las vistas de bk, a lo Gentle Shell 4.

Dos estilos de tarjeta (bk customize → Cards), guardados en ~/.config/bk/ui.json:
  float (default)  panel sin borde sobre un fondo apenas distinto, con barra de acento ▎
                    ▎ ✿ Status                          1 agente
                    ▎ Project   ~/barrilete-kit
  neon              marco redondeado con título, como Gentle Shell 3
Densidad: comfortable (filas en blanco arriba/abajo del título) o compact.
Los colores siguen al tema de Ghostty que maneja `bk theme` (day = lavanda claro, night = violeta oscuro).
"""
import json, os, re, subprocess, unicodedata

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
UI_FILE = os.path.expanduser("~/.config/bk/ui.json")
GHOSTTY = os.path.expanduser("~/Library/Application Support/com.mitchellh.ghostty/config.ghostty")
DEFAULTS = {"cards": "float", "density": "comfortable", "rail": "right", "header": "top"}
LAYOUT_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "layout.conf")


def layout():
    """Las proporciones de lib/layout.conf (NOMBRE=número; bin/bk lo carga con `source`): SIDE_W, RAIL_PCT, RAIL_MIN."""
    out = {}
    for l in open(LAYOUT_FILE, encoding="utf-8"):
        m = re.match(r"([A-Z_]+)=(\d+)", l)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


LAYOUT = layout()
SIDE_W, RAIL_PCT, RAIL_MIN = LAYOUT["SIDE_W"], LAYOUT["RAIL_PCT"], LAYOUT["RAIL_MIN"]
# Margen horizontal de una tarjeta: float = margen + ▎ + espacio … espacio + margen; neon = ╭│ … │╮ con un espacio a cada lado
CARD_PAD = {"float": 5, "neon": 4}


def ui():
    try:
        return {**DEFAULTS, **json.load(open(UI_FILE))}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_ui(conf):
    os.makedirs(os.path.dirname(UI_FILE), exist_ok=True)
    json.dump(conf, open(UI_FILE, "w"), indent=1)


def is_day():
    """Tema de día si Ghostty usa bk-day (o auto con macOS en modo claro)."""
    try:
        line = [l for l in open(GHOSTTY) if l.startswith("theme")][-1]
    except (OSError, IndexError):
        return False
    if "light:" in line:
        r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True)
        return "Dark" not in r.stdout
    return "bk-day" in line


def fg(h):
    h = h.lstrip("#")
    return f"\033[38;2;{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}m"


def bg(h):
    h = h.lstrip("#")
    return f"\033[48;2;{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}m"


RST, BOLD, DIM, ITAL = "\033[0m", "\033[1m", "\033[2m", "\033[3m"
NIGHT = {"rose": "F095C8", "petal": "D7A0B8", "iris": "A99CF5", "amber": "E6B450", "green": "8CCB8A",
         "red": "F07178", "text": "ECE4F0", "muted": "8A80A8", "frame": "7A5F9E", "tint": "2A2852"}
# Día: apagado (saturación ~la mitad que antes) y con contraste >= 5:1 sobre blanco para el texto
DAY = {"rose": "8E4F75", "petal": "7A6070", "iris": "5A5596", "amber": "76601E", "green": "3D6A4C",
       "red": "9C3F58", "text": "2E2A36", "muted": "6F6880", "frame": "BDB4CC", "tint": "EEEBF2"}
P = DAY if is_day() else NIGHT
ROSE, PETAL, IRIS, AMBER = fg(P["rose"]), fg(P["petal"]), fg(P["iris"]), fg(P["amber"])
GREEN, RED, TEXT, MUTED, FRAME = fg(P["green"]), fg(P["red"]), fg(P["text"]), fg(P["muted"]), fg(P["frame"])
TINT = bg(P["tint"])


_LEVELS = (0, 95, 135, 175, 215, 255)


def to256(h):
    """Color 256 más cercano a un hex (cubo 6x6x6 o rampa de grises), para curses."""
    h = h.lstrip("#")
    r, g_, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    near = lambda v: min(range(6), key=lambda i: abs(_LEVELS[i] - v))
    ci = [near(r), near(g_), near(b)]
    cube = 16 + 36 * ci[0] + 6 * ci[1] + ci[2]
    cd = sum((_LEVELS[i] - v) ** 2 for i, v in zip(ci, (r, g_, b)))
    gv = min(23, max(0, round((((r + g_ + b) / 3) - 8) / 10)))
    gd = sum((8 + 10 * gv - v) ** 2 for v in (r, g_, b))
    return 232 + gv if gd < cd else cube


def rgb(h):
    """(r, g, b) de un hex."""
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def ramp(n, lo="tint", hi="rose", pal=None):
    """n hex de `lo` a `hi` de la paleta del tema, o de `pal` (para mapas de calor y perfiles)."""
    a, b = (pal or P)[lo], (pal or P)[hi]
    ch = lambda s, i: int(s[i:i + 2], 16)
    return ["%02X%02X%02X" % tuple(round(ch(a, i) + (ch(b, i) - ch(a, i)) * k / max(1, n - 1)) for i in (0, 2, 4))
            for k in range(n)]


CURSES_NAMES = ("rose", "petal", "iris", "amber", "green", "red", "text", "muted")


def curses_attrs(base=20, shades=0):
    """Inicia pares de curses con la paleta del tema y devuelve {nombre: atributo}.

    Las vistas curses piden los colores acá en vez de tener sus números 256: así siguen el tema
    día/noche igual que el riel. Con `shades`, suma "shade0".."shadeN" (tint → rose, para mapas de calor).
    """
    import curses
    out, n = {}, base
    for name in CURSES_NAMES:
        curses.init_pair(n, to256(P[name]), -1)
        out[name] = curses.color_pair(n)
        n += 1
    for i, h in enumerate(ramp(shades)):
        curses.init_pair(n, to256(h), -1)
        out[f"shade{i}"] = curses.color_pair(n)
        n += 1
    return out


# Estados de un agente o chat: una sola tabla para la tira, el riel, el monitor y los overlays.
# glifo, texto y nombre de color de la paleta. Cada estado tiene su propio glifo: no se distinguen solo por color.
STATES = {
    "working":    ("●", "trabajando", "iris"),     # la tira y el riel lo animan con un spinner
    "background": ("◐", "en 2º plano", "iris"),
    "waiting":    ("!", "te espera", "red"),
    "permission": ("!", "pide permiso", "red"),
    "question":   ("?", "te pregunta", "amber"),
    "stale":      ("⚠", "¿trabado?", "amber"),     # "working" sin eventos del hook hace rato
    "trust":      ("●", "confiá la carpeta", "red"),
    "done":       ("✓", "listo", "green"),
    "idle":       ("○", "libre", "muted"),
    "ended":      ("·", "terminó", "muted"),
    "none":       ("·", "sin estado", "muted"),
    "error":      ("✗", "error", "red"),
}
STATE_COLOR = {"iris": "IRIS", "amber": "AMBER", "red": "RED", "green": "GREEN", "muted": "MUTED"}


def state_ui(state):
    """(glifo, texto, nombre de color) del estado; uno desconocido cae en "none" con su nombre como texto."""
    if state in STATES:
        return STATES[state]
    g, _, c = STATES["none"]
    return g, state or STATES["none"][1], c


def state_ansi(state):
    """Como state_ui, pero con el color como secuencia ANSI del tema."""
    g, txt, c = state_ui(state)
    return g, txt, globals()[STATE_COLOR[c]]


def cw(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def vlen(s):
    return sum(cw(c) for c in ANSI.sub("", s))


def cut(line, width):
    """Corta a `width` columnas visibles conservando los colores (con "…" si sobra texto)."""
    if vlen(line) <= width:
        return line + RST
    out, w, i = [], 0, 0
    while i < len(line):
        m = ANSI.match(line, i)
        if m:
            out.append(m.group(0)); i = m.end(); continue
        c = cw(line[i])
        if w + c > width - 1:
            break
        out.append(line[i]); w += c; i += 1
    return "".join(out) + ("…" if width > 0 else "") + RST


def fit(s, width):
    s = cut(s, width) if vlen(s) > width else s
    return s + " " * max(0, width - vlen(s))


def lr(left, right, width):
    """Texto a la izquierda y a la derecha en `width` columnas."""
    gap = width - vlen(left) - vlen(right)
    if gap < 1:
        return fit(left, width)
    return left + " " * gap + right


def _float(title, rows, width, accent, glyph, right, compact):
    """Panel float: margen de 1, fondo tintado, barra ▎ del color del tono, aire arriba y abajo."""
    inner = width - CARD_PAD["float"]
    def paint(content):
        # El fondo se re-aplica después de cada RST interno para que el panel quede parejo
        body = fit(content, inner).replace(RST, RST + TINT)
        return f" {TINT}{accent}▎{RST}{TINT} {body} {RST} "
    head = f"{accent}{glyph} {BOLD}{title}{RST}"
    tr = f"{MUTED}{right}{RST}" if right else ""
    out = [] if compact else [paint("")]
    out.append(paint(lr(head, tr, inner)))
    if not compact:
        out.append(paint(""))
    out += [paint(r) for r in rows]
    if not compact:
        out.append(paint(""))
    out.append("")
    return out


def _neon(title, rows, width, color, accent, glyph, right):
    inner = width - CARD_PAD["neon"]
    head = f"{accent}{glyph} {BOLD}{title}{RST}"
    tr = f" {MUTED}{right}{RST} " if right else ""
    fill = width - 4 - vlen(head) - vlen(tr) - 1
    out = [f"{color}╭─ {RST}{head}{color} {'─' * max(0, fill)}{RST}{tr}{color}╮{RST}"]
    for r in rows:
        out.append(f"{color}│{RST} {fit(r, inner)} {color}│{RST}")
    out.append(f"{color}╰{'─' * (width - 2)}╯{RST}")
    return out


def card(title, rows, width, color=None, accent=None, glyph="✿", right=""):
    """Tarjeta en el estilo elegido. `color` es el tono (marco en neon, barra en float)."""
    conf = ui()
    accent = accent or ROSE
    tone = color if color and color != FRAME else accent
    if conf["cards"] == "neon":
        return _neon(title, rows, width, color or FRAME, accent, glyph, right)
    return _float(title, rows, width, tone, glyph, right, conf["density"] == "compact")


def inner(width):
    """Columnas de texto que caben en una tarjeta de `width` (según el estilo elegido): lo que `card` no corta."""
    return width - CARD_PAD["float" if ui()["cards"] == "float" else "neon"]


def hint(keys, width):
    """Fila de atajos: `j/k select   enter abrir …` con la tecla resaltada."""
    parts = [f"{PETAL}{k}{RST} {MUTED}{v}{RST}" for k, v in keys]
    return fit("   ".join(parts), width)
