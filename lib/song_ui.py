#!/usr/bin/env python3
"""bk song ui — pantalla en vivo de la radio: gráfico horizontal del sonido + control con teclas.

Le habla al mpv de `bk song` por su socket IPC. Es solo un control remoto: al salir, la música sigue.
El gráfico es real: mpv corre con astats y acá se lee el nivel RMS de cada canal ~20 veces por segundo.
Canal izquierdo hacia arriba, derecho hacia abajo; lo nuevo entra por la derecha.

uso: song_ui.py <socket de mpv> <ruta a song.sh>
"""
import json
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import termios
import time
import tty

SOCK, SONG = sys.argv[1], sys.argv[2]
CACHE = os.environ.get("BK_SONG_CACHE") or os.path.expanduser("~/.cache/bk")
HISTORY_ROWS = 3          # últimos temas que se muestran abajo del gráfico
FPS = 20
DB_RANGE = 18.0           # dB de la escala: el pico reciente llena la barra, 18 dB menos es vacío
SCALE_SECONDS = 6         # ventana para la escala automática (las radios de techno vienen muy comprimidas)
MAX_GRAPH_ROWS = 7        # filas por canal (arriba L, abajo R)

# Paletas por fondo de terminal. GRADIENT va del centro del gráfico hacia afuera.
PALETTES = {
    "dark": {   # fondo oscuro: violetas que se aclaran hacia afuera
        "GRADIENT": [(76, 29, 149), (109, 40, 217), (139, 92, 246), (167, 139, 250),
                     (196, 181, 253), (232, 121, 249), (240, 171, 252)],
        "VIOLET": (167, 139, 250), "DIM": (124, 116, 158), "TEXT": (237, 233, 254), "PINK": (240, 171, 252),
    },
    "light": {  # fondo claro (violeta pastel): violetas profundos, que se lean sobre lavanda
        "GRADIENT": [(59, 7, 100), (76, 29, 149), (91, 33, 182), (109, 40, 217),
                     (124, 58, 237), (147, 51, 234), (168, 85, 247)],
        "VIOLET": (109, 40, 217), "DIM": (110, 90, 138), "TEXT": (43, 29, 63), "PINK": (192, 38, 211),
    },
}
GRADIENT = VIOLET = DIM = TEXT = PINK = None


def use_palette(name):
    global GRADIENT, VIOLET, DIM, TEXT, PINK
    p = PALETTES[name]
    GRADIENT, VIOLET, DIM, TEXT, PINK = p["GRADIENT"], p["VIOLET"], p["DIM"], p["TEXT"], p["PINK"]


def terminal_background(fd, out):
    """Le pregunta a la terminal su color de fondo (OSC 11). "light", "dark" o None si no contesta.
    BK_SONG_THEME=light|dark lo fuerza (p. ej. si tmux no pasa la pregunta)."""
    forced = os.environ.get("BK_SONG_THEME", "")
    if forced in PALETTES:
        return forced
    out.write("\033]11;?\033\\")
    out.flush()
    reply, deadline = b"", time.monotonic() + 0.3
    while time.monotonic() < deadline and not (reply.endswith(b"\x07") or reply.endswith(b"\033\\")):
        if select.select([fd], [], [], 0.05)[0]:
            reply += os.read(fd, 64)
    m = re.search(rb"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)", reply)
    if not m:
        return None
    r, g, b = (int(x, 16) / (16 ** len(x) - 1) for x in m.groups())
    return "light" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5 else "dark"

def fg(rgb):
    return "\033[38;2;%d;%d;%dm" % rgb

RESET, BOLD = "\033[0m", "\033[1m"


class Mpv:
    """Conexión persistente al socket de mpv; se reconecta sola si mpv se reinicia (cambio de estación)."""

    def __init__(self, path):
        self.path, self.sock, self.buf, self.rid = path, None, b"", 0

    def _connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.3)
        s.connect(self.path)
        self.sock, self.buf = s, b""

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def cmd(self, *args):
        """Devuelve `data` si mpv respondió success, None si no (o si no hay mpv)."""
        for _ in range(2):
            try:
                if self.sock is None:
                    self._connect()
                self.rid += 1
                rid = self.rid
                self.sock.sendall((json.dumps({"command": list(args), "request_id": rid}) + "\n").encode())
                deadline = time.monotonic() + 0.3
                while time.monotonic() < deadline:
                    while b"\n" in self.buf:
                        line, self.buf = self.buf.split(b"\n", 1)
                        try:
                            msg = json.loads(line)
                        except ValueError:
                            continue
                        if msg.get("request_id") == rid:   # el resto son eventos asíncronos de mpv
                            return msg.get("data") if msg.get("error") == "success" else None
                    chunk = self.sock.recv(65536)
                    if not chunk:
                        raise OSError("socket cerrado")
                    self.buf += chunk
                return None
            except OSError:
                self.close()
        return None

    def get(self, prop):
        return self.cmd("get_property", prop)


def song(*args):
    """Reusa bk song para lo que cambia de estación o pausa (así también se entera la barra)."""
    subprocess.run([SONG, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def db_of(meta, ch):
    """dB RMS del canal `ch` según astats; None en silencio ("-inf") o sin datos."""
    try:
        db = float((meta or {}).get("lavfi.astats.%d.RMS_level" % ch))
    except (TypeError, ValueError):
        return None
    return db if db > -120 else None


class Scale:
    """Escala automática: el pico de los últimos segundos llena la barra. Con una escala fija, una radio
    comprimida queda siempre arriba y no se ve el ritmo."""

    def __init__(self):
        self.peaks = []   # (t, db)

    def __call__(self, db, now):
        if db is None:
            return 0.0
        self.peaks.append((now, db))
        while self.peaks and now - self.peaks[0][0] > SCALE_SECONDS:
            self.peaks.pop(0)
        top = max(d for _, d in self.peaks)
        return max(0.0, min(1.0, (db - (top - DB_RANGE)) / DB_RANGE))


def clip(text, width):
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def render_graph(left, right, width, rows):
    """Onda espejada: L crece hacia arriba desde el centro, R hacia abajo. Resolución de 1/8 en L y 1/2 en R."""
    up = " ▁▂▃▄▅▆▇█"
    out = []
    # Mitad de arriba (canal L): la fila 0 es la de afuera
    for r in range(rows):
        dist = rows - 1 - r                       # 0 = pegada al centro
        color = fg(GRADIENT[min(dist, len(GRADIENT) - 1)])
        cells = []
        for v in left[-width:]:
            fill = v * rows - dist                # cuánto de esta fila ocupa la barra
            cells.append(up[8] if fill >= 1 else up[max(0, int(fill * 8))] if fill > 0 else " ")
        out.append(color + "".join(cells).rjust(width))
    # Línea central
    out.append(fg(DIM) + "─" * width)
    # Mitad de abajo (canal R), crece hacia abajo
    for dist in range(rows):
        color = fg(GRADIENT[min(dist, len(GRADIENT) - 1)])
        cells = []
        for v in right[-width:]:
            fill = v * rows - dist
            cells.append("█" if fill >= 1 else "▀" if fill >= 0.5 else " ")
        out.append(color + "".join(cells).rjust(width))
    return out


def main():
    if not sys.stdin.isatty():
        sys.exit("bk song ui necesita una terminal")

    mpv = Mpv(SOCK)
    def load_stations():
        rows = [l.split("\t") for l in
                subprocess.run([SONG, "stations"], capture_output=True, text=True).stdout.splitlines()]
        return [(r[0], r[1], r[2] if len(r) > 2 else "") for r in rows if len(r) >= 2]

    stations = load_stations()
    genres = list(dict.fromkeys(g for _, _, g in stations))
    view = None           # género que muestran los números (sigue al que suena hasta que apretás g)
    left, right = [], []
    scale = Scale()       # una sola escala para los dos canales, así no se deforma el balance
    info = {"station": "", "key": "", "genre": "", "title": "", "paused": False, "vol": 0, "alive": False}
    history = []
    last_info, msg, msg_until = 0.0, "", 0.0

    fd = sys.stdin.fileno()
    old_tty = termios.tcgetattr(fd)
    out = sys.stdout

    def restore(*_):
        termios.tcsetattr(fd, termios.TCSADRAIN, old_tty)
        out.write("\033[?25h\033[?1049l")         # cursor visible, salir de la pantalla alternativa
        out.flush()

    def on_term(*_):
        restore()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, on_term)
    tty.setcbreak(fd)
    # Sin respuesta (terminal vieja o tmux sin passthrough): claro, que es el tema de Ghostty en este setup
    use_palette(terminal_background(fd, out) or "light")
    out.write("\033[?1049h\033[?25l")
    try:
        while True:
            t0 = time.monotonic()
            cols, lines = shutil.get_terminal_size((80, 24))
            width = max(20, cols - 4)
            rows = max(2, min(MAX_GRAPH_ROWS, (lines - 9 - 1 - HISTORY_ROWS) // 2))

            # ---- estado (1 vez por segundo) ----
            if t0 - last_info > 1.0:
                last_info = t0
                info["alive"] = mpv.get("pid") is not None
                if info["alive"]:
                    meta = mpv.get("metadata") or {}
                    title = meta.get("icy-title") or meta.get("title") or ""
                    if title and title != info["title"]:
                        song("bar-status")    # anota el tema nuevo en el historial
                    info["title"] = title
                    info["paused"] = bool(mpv.get("pause"))
                    info["vol"] = int(mpv.get("volume") or 0)
                try:
                    parts = open(os.path.join(CACHE, "song.station")).read().rstrip("\n").split("\t")
                except OSError:
                    parts = []
                key, name, genre = (parts + ["", "", ""])[:3]
                genre = next((g for k, _, g in stations if k == key), genre)
                info["key"], info["station"], info["genre"] = key, name, genre
                try:
                    lines_h = open(os.path.join(CACHE, "song.history")).read().splitlines()[-HISTORY_ROWS:]
                    history = [l.split("\t") for l in reversed(lines_h) if l.count("\t") >= 2]
                except OSError:
                    history = []

            # ---- niveles (cada frame; en pausa el gráfico se congela) ----
            if info["alive"] and not info["paused"]:
                meta = mpv.get("af-metadata/viz")
                db_l = db_of(meta, 1)
                db_r = db_of(meta, 2) if meta and "lavfi.astats.2.RMS_level" in meta else db_l   # radio mono
                left.append(scale(db_l, t0))
                right.append(scale(db_r, t0))
                del left[:-cols], right[:-cols]

            # ---- dibujo ----
            buf = ["\033[H"]
            if info["alive"]:
                state = (fg(DIM) + "⏸ en pausa") if info["paused"] else (fg(PINK) + "▶ sonando")
                head = "♫ " + clip(info["station"] or "?", width - 14)
                buf.append("\033[2K  " + BOLD + fg(VIOLET) + head + RESET
                           + " " * max(1, width - len(head) - 10) + state + RESET + "\n")
                buf.append("\033[2K    " + fg(TEXT) + clip(info["title"] or "sin datos del tema", width - 2) + RESET + "\n\n")
            else:
                buf.append("\033[2K  " + fg(DIM) + "■ no está sonando nada — [n] o [1-%d] para arrancar" % len(stations)
                           + RESET + "\n\033[2K\n\n")
            for line in render_graph(left or [0.0], right or [0.0], width, rows):
                buf.append("\033[2K  " + line + RESET + "\n")
            vol = max(0, min(100, info["vol"]))
            bar = "█" * (vol // 10) + "░" * (10 - vol // 10)
            buf.append("\n\033[2K    " + fg(DIM) + "vol " + fg(VIOLET) + bar + fg(DIM) + " %d%%" % info["vol"] + RESET + "\n")
            shown = view or info["genre"] or (genres[0] if genres else "")
            tabs = "  ".join((fg(VIOLET) + BOLD + "[" + g + "]" if g == shown else fg(DIM) + g) + RESET for g in genres)
            buf.append("\033[2K    " + tabs + "\n")
            in_view = [s for s in stations if s[2] == shown][:9]
            st = "  ".join((fg(PINK) + BOLD if k == info["key"] else fg(TEXT)) + "%d %s" % (i + 1, k) + RESET
                           for i, (k, _, _) in enumerate(in_view))
            buf.append("\033[2K    " + st + "\n")
            for h in history:
                buf.append("\033[2K    " + fg(DIM) + h[0] + "  " + RESET + fg(TEXT)
                           + clip(h[2], max(10, width - 8 - len(h[1]) - 3)) + fg(DIM) + " · " + clip(h[1], 30) + RESET + "\n")
            keys = "[espacio] pausa  [g] género  [1-9] radio  [n/p] estación  [f] guardar  [+/-] vol  [s] stop  [q] salir"
            if msg and t0 < msg_until:
                keys = msg
            buf.append("\033[2K    " + fg(DIM) + clip(keys, width) + RESET + "\033[J")
            out.write("".join(buf))
            out.flush()

            # ---- teclas ----
            timeout = max(0.0, 1.0 / FPS - (time.monotonic() - t0))
            if select.select([sys.stdin], [], [], timeout)[0]:
                ch = os.read(fd, 32).decode(errors="ignore")[:1]
                if ch in ("q", "Q", "\x1b"):
                    break
                elif ch == " ":
                    song("pause")
                elif ch in ("n", "N"):
                    msg, msg_until = "cambiando de estación…", time.monotonic() + 3
                    mpv.close(); song("next"); left.clear(); right.clear(); scale.peaks.clear()
                elif ch in ("p", "P"):
                    msg, msg_until = "cambiando de estación…", time.monotonic() + 3
                    mpv.close(); song("prev"); left.clear(); right.clear(); scale.peaks.clear()
                elif ch.isdigit() and 1 <= int(ch) <= len(in_view):
                    msg, msg_until = "cambiando de estación…", time.monotonic() + 3
                    mpv.close(); song(in_view[int(ch) - 1][0]); left.clear(); right.clear(); scale.peaks.clear()
                    view = None
                elif ch in ("g", "G", "\t") and genres:
                    i = genres.index(shown) if shown in genres else -1
                    view = genres[(i + (-1 if ch == "G" else 1)) % len(genres)]
                elif ch in ("f", "F"):
                    r = subprocess.run([SONG, "fav"], capture_output=True, text=True)
                    msg, msg_until = (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else "", time.monotonic() + 3
                    stations = load_stations(); genres = list(dict.fromkeys(g for _, _, g in stations))
                elif ch in ("+", "=", "-", "_"):
                    step = 5 if ch in ("+", "=") else -5
                    mpv.cmd("set_property", "volume", max(0, min(100, int(mpv.get("volume") or 0) + step)))
                elif ch in ("s", "S"):
                    mpv.close(); song("stop")
                    break
                last_info = 0.0               # refrescar estado ya después de cualquier tecla
    except KeyboardInterrupt:
        pass
    finally:
        restore()
        mpv.close()


if __name__ == "__main__":
    main()
