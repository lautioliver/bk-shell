# shellcheck shell=bash
# Mini TAP para los tests de bk shell: check <rc> <descripción>, check_eq <got> <want> <descripción>
N=0; FAILED=0
# Los tests que lanzan agentes no dependen de la memoria libre de la máquina (mem_ok en bin/bk)
export BK_FORCE=1
# Ni el "agents.py warm" que bk deja corriendo al lanzar un agente, ni consultas a GitHub por los títulos
export BK_NO_WARM=1
check() { N=$((N+1)); if [ "$1" -eq 0 ]; then echo "ok $N - $2"; else echo "not ok $N - $2"; FAILED=$((FAILED+1)); fi; }
check_eq() { N=$((N+1)); if [ "$1" = "$2" ]; then echo "ok $N - $3"; else echo "not ok $N - $3 (got '$1', want '$2')"; FAILED=$((FAILED+1)); fi; }
finish() { echo "1..$N"; [ "$FAILED" -eq 0 ]; }
