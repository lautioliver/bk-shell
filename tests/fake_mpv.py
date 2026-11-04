#!/usr/bin/env python3
"""mpv falso para los tests: escucha en un socket unix, contesta get_property volume y anota cada set_property.
uso: fake_mpv.py <socket> <log> <volumen inicial>"""
import json, os, socket, sys
path, log, vol = sys.argv[1], sys.argv[2], float(sys.argv[3])
if os.path.exists(path):
    os.unlink(path)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(path); srv.listen(8)
while True:
    c, _ = srv.accept()
    with c:
        line = c.makefile().readline()
        try:
            cmd = json.loads(line)["command"]
        except (ValueError, KeyError):
            continue
        if cmd[:2] == ["set_property", "volume"]:
            vol = float(cmd[2])
            with open(log, "a") as f:
                f.write("%d\n" % vol)
        c.sendall((json.dumps({"data": vol, "error": "success"}) + "\n").encode())
