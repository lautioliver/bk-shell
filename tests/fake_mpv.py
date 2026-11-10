#!/usr/bin/env python3
"""mpv falso para los tests: escucha en un socket unix, contesta get_property volume y anota cada set_property.
También lleva la lista de filtros: `af add "@etiqueta:…"` y `af remove @etiqueta` se anotan en el log como
"af add etiqueta" / "af remove etiqueta", y get_property af la devuelve (como mpv: [{"label": …, "name": …}]).
get_property pid contesta un pid; otra propiedad que no sea volume, "property unavailable".
uso: fake_mpv.py <socket> <log> <volumen inicial>"""
import json, os, socket, sys
path, log, vol = sys.argv[1], sys.argv[2], float(sys.argv[3])
af = []
if os.path.exists(path):
    os.unlink(path)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(path); srv.listen(8)


def note(text):
    with open(log, "a") as f:
        f.write(text + "\n")


while True:
    c, _ = srv.accept()
    with c:
        line = c.makefile().readline()
        try:
            msg = json.loads(line)
            cmd = msg["command"]
        except (ValueError, KeyError, TypeError):
            continue
        data = vol
        if cmd[:2] == ["set_property", "volume"]:
            vol = float(cmd[2])
            note("%d" % vol)
            data = vol
        elif cmd[:2] == ["af", "add"] and len(cmd) > 2 and str(cmd[2]).startswith("@"):
            label = str(cmd[2])[1:].split(":", 1)[0]
            af.append({"label": label, "name": "lavfi"})
            note("af add " + label)
            data = None
        elif cmd[:2] == ["af", "remove"] and len(cmd) > 2:
            label = str(cmd[2]).lstrip("@")
            af = [f for f in af if f["label"] != label]
            note("af remove " + label)
            data = None
        elif cmd == ["get_property", "af"]:
            data = af
        elif cmd == ["get_property", "pid"]:
            data = 4242
        elif cmd[:1] == ["get_property"] and cmd[1:] != ["volume"]:   # lo demás no lo tiene, como un mpv sin datos
            data = "unavailable"
        reply = {"data": data, "error": "success"} if data != "unavailable" else {"error": "property unavailable"}
        if isinstance(msg, dict) and "request_id" in msg:
            reply["request_id"] = msg["request_id"]
        c.sendall((json.dumps(reply, separators=(",", ":")) + "\n").encode())   # compacto, como mpv
