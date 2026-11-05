#!/usr/bin/env python3
"""bk song ui — pantalla en vivo de la radio: el tema arriba, el sonido como un perfil que crece desde abajo
(lo nuevo entra por la derecha, en franjas de color por altura) y el estado abajo, con lo que sigue.

Le habla al mpv de `bk song` por su socket IPC. Es solo un control remoto: al salir, la música sigue.
El gráfico es real: mpv corre con astats y acá se lee el nivel RMS ~20 veces por segundo.

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
HISTORY_ROWS = 3          # últimos temas que se guardan (se ven con bk song hist)
FPS = 20
DB_RANGE = 12.0           # dB de la escala: el pico reciente llena la altura, 12 dB menos es vacío
COL_FRAMES, SMOOTH = 3, 0.6   # cuadros por columna y suavizado (1 = sin suavizar)
CURVE = 2.2               # >1 aplasta lo sostenido: picos y valles como un perfil, no un bloque
SCALE_SECONDS = 6         # ventana para la escala automática (las radios de techno vienen muy comprimidas)
HEADER_ROWS, FOOTER_ROWS = 4, 5   # tema/datos/cita/vacía arriba; vacía/estado/radios/vacía/"a continuación" abajo

# Paletas por fondo de terminal. BANDS: el color del perfil por altura, de abajo (apagado) a arriba (rosa).
PALETTES = {
    "dark": {
        "BANDS": [(122, 102, 112), (168, 148, 158), (242, 184, 216), (232, 150, 200)],
        "VIOLET": (232, 150, 200), "DIM": (150, 136, 146), "TEXT": (232, 222, 230), "PINK": (240, 149, 200),
    },
    "light": {
        "BANDS": [(150, 130, 140), (120, 100, 112), (214, 120, 170), (184, 70, 140)],
        "VIOLET": (184, 70, 140), "DIM": (111, 104, 128), "TEXT": (46, 42, 54), "PINK": (142, 79, 117),
    },
}
BANDS = VIOLET = DIM = TEXT = PINK = None


def use_palette(name):
    global BANDS, VIOLET, DIM, TEXT, PINK
    p = PALETTES[name]
    BANDS, VIOLET, DIM, TEXT, PINK = p["BANDS"], p["VIOLET"], p["DIM"], p["TEXT"], p["PINK"]


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

RESET, BOLD, ITALIC = "\033[0m", "\033[1m", "\033[3m"


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
        v = max(0.0, min(1.0, (db - (top - DB_RANGE)) / DB_RANGE))
        return v ** CURVE   # la curva baja lo sostenido y deja los golpes como picos (si no, todo queda lleno)


def clip(text, width):
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def render_graph(levels, width, rows):
    """Perfil del sonido que crece desde abajo, con resolución de 1/8 de celda. Lo nuevo entra por la derecha.
    El color va por altura en franjas fijas (como un ecualizador): abajo apagado, arriba rosa."""
    blocks = " ▁▂▃▄▅▆▇█"
    vals = levels[-width:]
    vals = [0.0] * (width - len(vals)) + vals
    out = []
    for r in range(rows):                       # r = 0 es la fila de arriba
        h = rows - 1 - r                        # altura de esta fila desde abajo
        color = fg(BANDS[min(len(BANDS) - 1, h * len(BANDS) // max(1, rows))])
        cells = []
        for v in vals:
            fill = v * rows - h
            cells.append("█" if fill >= 1 else blocks[int(fill * 8)] if fill > 0 else " ")
        out.append(color + "".join(cells))
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
    levels, pending = [], []
    scale = Scale()
    info = {"station": "", "key": "", "genre": "", "title": "", "paused": False, "vol": 0, "alive": False,
            "br": "", "icy": ""}
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
                    info["br"] = meta.get("icy-br") or ""
                    info["icy"] = meta.get("icy-name") or meta.get("icy-description") or ""
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
                loud = max((d for d in (db_l, db_r) if d is not None), default=None)
                pending.append(scale(loud, t0))
                if len(pending) >= COL_FRAMES:   # una columna cada COL_FRAMES cuadros: lomas anchas, no agujas
                    v = max(pending)
                    pending.clear()
                    prev = levels[-1] if levels else v
                    levels.append(prev + (v - prev) * SMOOTH)
                    del levels[:-cols]

            # ---- dibujo ----
            shown = view or info["genre"] or (genres[0] if genres else "")
            in_view = [st for st in stations if st[2] == shown][:9]
            buf = ["\033[H"]
            if info["alive"]:
                title = info["title"] or info["station"] or "?"
                datos = " · ".join(x for x in (info["genre"], info["station"] if info["title"] else "",
                                                f'{info["br"]} kbps' if info["br"] else "", f'vol {info["vol"]}%') if x)
                cita = info["icy"] if info["icy"] and info["icy"] != info["station"] else (history[1][2] if len(history) > 1 else "")
                buf.append("\033[2K  " + fg(PINK) + BOLD + "♪ " + clip(title, width - 2) + RESET + "\n")
                buf.append("\033[2K  " + fg(DIM) + clip(datos, width) + RESET + "\n")
                buf.append("\033[2K  " + (fg(TEXT) + ITALIC + "“" + clip(cita, width - 4) + "”" + RESET if cita else "") + "\n\033[2K\n")
            else:
                buf.append("\033[2K  " + fg(DIM) + "■ no está sonando nada — [n] o [1-%d] para arrancar" % len(stations)
                           + RESET + "\n\033[2K\n\033[2K\n\033[2K\n")
            rows = max(3, lines - HEADER_ROWS - FOOTER_ROWS)
            for line in render_graph(levels or [0.0], width, rows):
                buf.append("\033[2K  " + line + RESET + "\n")
            state = (BOLD + fg(TEXT) + "⏸ En pausa") if info["paused"] else (BOLD + fg(TEXT) + "▶ Sonando")
            buf.append("\033[2K\n\033[2K  " + state + RESET + fg(DIM) + " · " + clip(info["station"] or "-", 40)
                       + f' · vol {info["vol"]}%' + RESET + "\n")
            radios = "  ".join((fg(PINK) + BOLD if k == info["key"] else fg(DIM)) + "%d %s" % (n + 1, k) + RESET
                               for n, (k, _, _) in enumerate(in_view))
            keys = msg if msg and t0 < msg_until else "espacio pausa · g género · 1-9 radio · n/p · f guardar · +/- vol · q salir"
            buf.append("\033[2K  " + fg(DIM) + shown + ": " + RESET + radios + fg(DIM) + "   " + clip(keys, max(10, width - 60)) + RESET + "\n")
            nxt = ""
            if in_view:
                ks = [k for k, _, _ in in_view]
                k_next = ks[(ks.index(info["key"]) + 1) % len(ks)] if info["key"] in ks else ks[0]
                nxt = next((nm for k, nm, _ in in_view if k == k_next), k_next)
            buf.append("\033[2K\n\033[2K  " + fg(TEXT) + "A continuación: " + BOLD + clip(nxt or "-", 40) + RESET
                       + fg(DIM) + (" · " + shown if shown else "") + "  (n)" + RESET + "\033[J")
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
                    mpv.close(); song("next"); levels.clear(); scale.peaks.clear()
                elif ch in ("p", "P"):
                    msg, msg_until = "cambiando de estación…", time.monotonic() + 3
                    mpv.close(); song("prev"); levels.clear(); scale.peaks.clear()
                elif ch.isdigit() and 1 <= int(ch) <= len(in_view):
                    msg, msg_until = "cambiando de estación…", time.monotonic() + 3
                    mpv.close(); song(in_view[int(ch) - 1][0]); levels.clear(); scale.peaks.clear()
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
