#!/usr/bin/env bash
# bk learn effect: veredicto por regla con un vault y PRs de prueba (no toca GitHub ni tu vault).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
# shellcheck source=tests/lib.sh
. "$HERE/lib.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
export HOME="$TMP/home" BK_CONFIG_DIR="$TMP/config"; mkdir -p "$HOME" "$BK_CONFIG_DIR"
unset BK_VAULT

export BARRILETE_VAULT="$TMP/vault"; L="$BARRILETE_VAULT/agents/lessons"; mkdir -p "$L"
lesson() {  # nombre, frontmatter extra
  printf -- '---\nstatus: accepted\n%b\n---\n# x\n\n## Regla para los agentes\n<!-- una línea -->\nRegla de %s.\n' "$2" "$1" >"$L/$1.md"
}
lesson baja      'type: review_finding\nsignature: "review_finding::metadatos del PR::kit"\nscope: kit\naccepted: 2026-11-10T00:00Z'
lesson no-baja   'type: manual\nscope: global\naccepted: 2026-11-10\nmide: docs desactualizados'
lesson sin-fecha 'type: manual\nscope: global\nmide: caso no cubierto'
lesson sin-medir 'type: manual\nscope: global\naccepted: 2026-11-10\nmide: -   # del reviewer'
lesson otro-repo 'type: review_finding\nsignature: "review_finding::metadatos del PR::otro"\nscope: otro\naccepted: 2026-11-10T00:00Z'
lesson pocos     'type: manual\nscope: global\naccepted: 2026-11-10\nmide: decisión no aprobada'
lesson sin-zona  'type: review_finding\nsignature: "review_finding::metadatos del PR::kit"\nscope: kit\naccepted: 2026-11-10T00:00'
printf -- '---\nstatus: proposed\ntype: manual\naccepted: 2026-11-10\nmide: docs desactualizados\n---\n' >"$L/propuesta.md"

# Reviews: "metadatos" = un hallazgo de metadatos del PR; "docs" = docs desactualizados; "ok" = ronda sin hallazgos
python3 - "$TMP/prs.json" <<'PY'
import json, sys
def body(kind):
    items = {"metadatos": "- El cuerpo del PR dice 12/12 casos y son 13.",
             "docs": "- El README no se actualizó: docs desactualizados.",
             "decision": "- Agrega una regla que el issue no trae (decisión no aprobada, sin aprobación).",
             "ok": ""}[kind]
    return "## Bloqueantes\n" + (items or "Ninguno.") + "\n\n## Sugerencias\n- nada\n"
def pr(n, created, kinds):
    return {"number": n, "createdAt": created, "reviews": [{"body": body(k)} for k in kinds]}
before = [pr(i, "2026-11-0%dT12:00:00Z" % (1 + i % 8), ["metadatos", "docs", "ok"]) for i in range(1, 5)]
before.append(pr(9, "2026-11-05T12:00:00Z", ["decision"]))
after = [pr(10 + i, "2026-11-1%dT12:00:00Z" % (1 + i % 8), ["docs", "docs", "ok"]) for i in range(4)]
after.append(pr(20, "2026-11-15T12:00:00Z", ["ok"]))
after[-1]["reviews"].append({"body": "un comentario suelto, sin secciones: no es una ronda"})
old = [pr(30, "2026-09-01T12:00:00Z", ["metadatos"] * 5)]   # fuera de la ventana de 30 días
json.dump({"kit": before + after + old, "otro": [pr(40, "2026-11-12T12:00:00Z", ["metadatos"])]}, open(sys.argv[1], "w"))
PY
export BK_EFFECT_PRS="$TMP/prs.json"
J=$(python3 "$ROOT/lib/effect.py" --json)
v() { jq -r --arg n "$1" '.[] | select(.lesson == $n) | .verdict' <<<"$J"; }
f() { jq -r --arg n "$1" ".[] | select(.lesson == \$n) | $2" <<<"$J"; }

check_eq "$(v baja)" baja "metadatos desaparece después de aceptarla: baja"
check_eq "$(f baja '.before.rounds')" 13 "antes: las 13 rondas de los 5 PRs del mes (no la de hace 2 meses, ni la de otro repo)"
check_eq "$(f baja '.after.rounds')" 13 "después: también cuenta las rondas sin hallazgos"
check_eq "$(f baja '.before.class')" 4 "antes: 4 hallazgos de metadatos"
check_eq "$(v no-baja)" "no baja" "docs sube después: no baja (para reescribir o retirar)"
check_eq "$(v sin-fecha)" "sin fecha" "sin accepted: no se mide y lo dice"
check_eq "$(v sin-medir)" "no se mide" "mide: - no se mide"
check_eq "$(v otro-repo)" "poca muestra" "scope otro: solo mira las rondas de otro (1 ronda: poca muestra)"
check_eq "$(v pocos)" "pocos casos" "un solo hallazgo de la clase antes: pocos casos, no un veredicto"
check_eq "$(jq -r '[.[] | select(.lesson == "propuesta")] | length' <<<"$J")" 0 "las propuestas no entran"
check_eq "$(v sin-zona)" baja "accepted sin Z ni offset (2026-11-10T00:00): hora local, se mide igual"

# --days sin número y un vault que no existe: avisan y salen con error (no un traceback ni un 0 callado)
out=$("$ROOT/bin/bk" learn effect --no-write --days 2>&1); rc=$?
[ $rc -ne 0 ] && grep -q -- '--days necesita un número' <<<"$out" && ! grep -q Traceback <<<"$out"
check $? "bk learn effect --days sin número: dice qué poner y sale con error"
out=$("$ROOT/bin/bk" learn effect --no-write --days cero 2>&1); rc=$?
[ $rc -ne 0 ] && grep -q -- '--days necesita un número' <<<"$out"; check $? "bk learn effect --days cero: también"
out=$(BARRILETE_VAULT="$TMP/no-existe" "$ROOT/bin/bk" learn effect --no-write 2>&1); rc=$?
[ $rc -ne 0 ] && grep -q "no existe el vault $TMP/no-existe" <<<"$out"; check $? "bk learn effect con un vault que no existe avisa y sale con error"

python3 "$ROOT/lib/effect.py" >/dev/null
grep -q '| \[\[baja\]\] | metadatos del PR |' "$BARRILETE_VAULT/agents/efecto.md"
check_eq $? 0 "escribe agents/efecto.md con una fila por regla"

finish
