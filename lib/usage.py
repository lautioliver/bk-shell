#!/usr/bin/env python3
"""Tokens del día de Claude Code y su costo equivalente en API.

Con suscripción no se paga por token: el costo es una referencia de "cuánto saldría por API".
Lee los transcripts (~/.claude/projects/**/*.jsonl) de forma incremental: guarda el offset de cada
archivo y solo parsea lo nuevo. Cada mensaje se cuenta una vez por message.id (el streaming lo
repite en varias líneas). El día es el local.

Uso: usage.py [--json]      (sin flag: tabla para la terminal)
Estado: ~/.cache/barrilete/usage-<fecha>.json
"""
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone

PROJECTS = os.path.expanduser("~/.claude/projects")
CACHE_DIR = os.path.expanduser("~/.cache/barrilete")

# USD por millón de tokens: input, output, cache read. Escritura de cache: x1.25 (5m) / x2 (1h) del input.
# Fuente: tabla de modelos del skill claude-api (cacheada 2026-10-06). Lectura de cache = x0.1 del input
# salvo donde hay precio publicado.
PRICES = {
    "claude-fable-5-1":  (10.00, 50.00, 0.25),
    "claude-fable-5":    (10.00, 50.00, 1.00),
    "claude-opus-5-5":   (4.00, 20.00, 0.20),
    "claude-opus-5":     (5.00, 25.00, 0.50),
    "claude-opus-4-8":   (5.00, 25.00, 0.50),
    "claude-opus-4-7":   (5.00, 25.00, 0.50),
    "claude-opus-4-6":   (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-sonnet-5":   (2.00, 10.00, 0.20),
    "claude-sonnet-4-6": (3.00, 15.00, 0.30),
    "claude-haiku-5-5":  (0.10, 0.50, 0.01),
    "claude-haiku-4-5":  (1.00, 5.00, 0.10),
}


def price_for(model):
    if model in PRICES:
        return PRICES[model]
    # ids con sufijo de fecha u otras variantes: el prefijo más largo que coincida
    for k in sorted(PRICES, key=len, reverse=True):
        if model.startswith(k):
            return PRICES[k]
    return None


def cost(model, u):
    p = price_for(model)
    if p is None:
        return None
    inp, out, cr = p
    return (u["in"] * inp + u["out"] * out + u["cw5"] * inp * 1.25 + u["cw1h"] * inp * 2
            + u["cr"] * cr) / 1e6


def project_name(cwd):
    if not cwd:
        return "?"
    base, parent = os.path.basename(cwd), os.path.basename(os.path.dirname(cwd))
    if parent.endswith("-wt"):  # worktree del kit: ../<repo>-wt/<n>
        return f"{parent[:-3]}#{base}"
    return base


def load_state(today):
    path = os.path.join(CACHE_DIR, f"usage-{today}.json")
    try:
        with open(path) as f:
            return path, json.load(f)
    except (OSError, ValueError):
        return path, {"offsets": {}, "msgs": {}}


def scan(state, day_start):
    """Lee lo nuevo de cada transcript tocado hoy. msgs[id] = [model, project, in, out, cw5, cw1h, cr]"""
    offsets, msgs = state["offsets"], state["msgs"]
    for path in glob.glob(os.path.join(PROJECTS, "**", "*.jsonl"), recursive=True):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_mtime < day_start:
            continue
        off = offsets.get(path, 0)
        if st.st_size < off:  # archivo reescrito
            off = 0
        if st.st_size == off:
            continue
        with open(path, "rb") as f:
            f.seek(off)
            data = f.read()
        # Solo líneas completas; la última puede estar a medio escribir
        end = data.rfind(b"\n") + 1
        for raw in data[:end].splitlines():
            if b'"usage"' not in raw:
                continue
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            m = e.get("message") or {}
            u = m.get("usage")
            mid = m.get("id")
            if not u or not mid or e.get("type") != "assistant":
                continue
            ts = e.get("timestamp", "")
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if t < day_start:
                continue
            model = m.get("model") or "?"
            if model == "<synthetic>":
                continue
            cc = u.get("cache_creation") or {}
            cw1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
            cw = u.get("cache_creation_input_tokens", 0) or 0
            row = [model, project_name(e.get("cwd")), u.get("input_tokens", 0) or 0,
                   u.get("output_tokens", 0) or 0, max(cw - cw1h, 0), cw1h,
                   u.get("cache_read_input_tokens", 0) or 0]
            prev = msgs.get(mid)
            # Las líneas repetidas del mismo mensaje: quedarse con la de más output (la final)
            if prev is None or row[3] >= prev[3]:
                msgs[mid] = row
        offsets[path] = off + end


def summarize(msgs):
    keys = ("in", "out", "cw5", "cw1h", "cr")
    total = dict.fromkeys(keys, 0)
    by_model, by_project = {}, {}
    total_cost, unpriced = 0.0, set()
    for model, proj, *vals in msgs.values():
        u = dict(zip(keys, vals))
        c = cost(model, u)
        if c is None:
            unpriced.add(model)
            c = 0.0
        total_cost += c
        for k in keys:
            total[k] += u[k]
        for bucket, name in ((by_model, model), (by_project, proj)):
            b = bucket.setdefault(name, {"tokens": 0, "cost": 0.0, "msgs": 0})
            b["tokens"] += u["in"] + u["out"] + u["cw5"] + u["cw1h"]
            b["cost"] += c
            b["msgs"] += 1
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "messages": len(msgs),
        # "tokens" = lo que se procesa nuevo: input + output + escritura de cache (sin lecturas de cache)
        "tokens": total["in"] + total["out"] + total["cw5"] + total["cw1h"],
        "breakdown": total,
        "cost_usd": round(total_cost, 2),
        "by_model": by_model,
        "by_project": by_project,
        "unpriced_models": sorted(unpriced),
    }


def human(n):
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return str(n)


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    # Un solo escaneo a la vez (la barra y bk tokens pueden coincidir)
    lock = os.path.join(CACHE_DIR, "usage.lock")
    for _ in range(100):
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            if time.time() - os.stat(lock).st_mtime > 120:
                os.rmdir(lock)
            time.sleep(0.1)
    try:
        path, state = load_state(today)
        scan(state, day_start)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
        # Borrar estados de días anteriores
        for old in glob.glob(os.path.join(CACHE_DIR, "usage-*.json")):
            if old != path:
                os.remove(old)
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass

    s = summarize(state["msgs"])
    if "--json" in sys.argv:
        json.dump(s, sys.stdout)
        return

    b = s["breakdown"]
    print(f"Hoy ({s['date']}): {human(s['tokens'])} tokens · ≈ ${s['cost_usd']:.2f} equivalente en API "
          f"({s['messages']} respuestas)")
    print(f"  input {human(b['in'])} · output {human(b['out'])} · cache escrito "
          f"{human(b['cw5'] + b['cw1h'])} · cache leído {human(b['cr'])}")
    for title, bucket in (("Por modelo", s["by_model"]), ("Por proyecto", s["by_project"])):
        print(f"\n{title}:")
        for name, v in sorted(bucket.items(), key=lambda kv: -kv[1]["cost"]):
            print(f"  {name:<40} {human(v['tokens']):>8}  ${v['cost']:>8.2f}  {v['msgs']:>5} resp")
    if s["unpriced_models"]:
        print(f"\nSin precio (no suman costo): {', '.join(s['unpriced_models'])}")


if __name__ == "__main__":
    main()
