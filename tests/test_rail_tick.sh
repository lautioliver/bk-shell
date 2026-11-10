#!/usr/bin/env bash
# El riel no gasta CPU de más: con el pane oculto no arma el frame ni escribe y espera más;
# visible, redibuja cada TICK_S y solo escribe si el frame cambió. Sin tmux: rail.sh se reemplaza.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
# shellcheck source=tests/lib.sh
. "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
export HOME="$TMP" BK_STATE_DIR="$TMP/agents" BK_CONFIG_DIR="$TMP/config" BK_CACHE_DIR="$TMP/cache"

# visible <TMUX_PANE> <respuesta de tmux>: lo que dice rail.visible()
visible() { TMUX_PANE="$1" python3 - "$ROOT/lib" "$2" <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import rail as R
R.sh = lambda *a, **k: sys.argv[2] if a[0] == "tmux" else ""
print(R.visible())
PY
}
check_eq "$(visible '%1' '1 1')" "True" "sesión con cliente y ventana activa: visible"
check_eq "$(visible '%1' '0 1')" "False" "sesión sin cliente (otro workspace en pantalla): oculto"
check_eq "$(visible '%1' '1 0')" "False" "otra ventana de la misma sesión al frente: oculto"
check_eq "$(visible '%1' '')" "True" "tmux no responde: se dibuja igual (mejor de más que un riel congelado)"
check_eq "$(visible '' '0 0')" "True" "fuera de tmux: siempre visible"

# steps <visible> <frames...>: corre rail.step() una vez por frame y muestra "escribió/espera" de cada paso
steps() { python3 - "$ROOT/lib" "$@" <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import rail as R
vis, frames = sys.argv[2] == "1", sys.argv[3:]
R.visible = lambda: vis
it = iter(frames)
def frame(W, H, cwd):
    if not vis:
        raise SystemExit("armó el frame con el pane oculto")
    return [next(it)]
R.frame = frame
last, out = None, []
for _ in frames:
    text, last, wait = R.step(last, "/x", 20, 3)
    out.append(f"{'w' if text else '-'}{wait}")
print(" ".join(out))
PY
}
check_eq "$(steps 1 a a b)" "w1.0 -1.0 w1.0" "visible: escribe el primero, se saltea el repetido y escribe el que cambió, cada 1 s"
check_eq "$(steps 0 a a)" "-3.0 -3.0" "oculto: no arma el frame, no escribe y espera 3 s"

finish
