"""Tests de las vistas en Python: anchos con color, estados de la tira, el humor de Barrilete y stats."""
import json, os, re, sys, tempfile, time, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
os.environ.setdefault("BK_STATE_DIR", tempfile.mkdtemp())
os.environ["BK_TASKS_DIR"] = tempfile.mkdtemp()   # nunca tus tareas reales
os.environ["BK_CACHE_DIR"] = tempfile.mkdtemp()   # ni tu caché de títulos
os.environ["BK_CONFIG_DIR"] = tempfile.mkdtemp()  # ni tu configuración (el sonido del monitor)
os.environ["BARRILETE_VAULT"] = tempfile.mkdtemp()   # ni tu vault (lecciones de bk checkpoints)
os.environ["BK_PRS_CACHE"] = os.path.join(tempfile.mkdtemp(), "prs.json")   # ni tu caché de PRs
os.environ["BK_CHECKPOINTS_OFFLINE"] = "1"   # bk checkpoints no consulta GitHub en los tests
import gentle as G          # noqa: E402
import rail                 # noqa: E402
import sidebar              # noqa: E402
import agents as AG         # noqa: E402
import tasks as TK          # noqa: E402

STRIP = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class Gentle(unittest.TestCase):
    def test_cut_respeta_el_ancho_visible(self):
        s = G.ROSE + "hola mundo con color" + G.RST
        self.assertEqual(G.vlen(G.cut(s, 8)), 8)
        self.assertTrue(STRIP.sub("", G.cut(s, 8)).endswith("…"))

    def test_fit_completa_con_espacios(self):
        self.assertEqual(G.vlen(G.fit("ab", 6)), 6)

    def test_card_float_y_neon_tienen_el_ancho_pedido(self):
        for style in ("float", "neon"):
            G.ui = lambda style=style: {**G.DEFAULTS, "cards": style}
            lines = [l for l in G.card("Título", ["fila"], 30) if l]
            self.assertTrue(all(G.vlen(l) <= 30 for l in lines), style)


class Sidebar(unittest.TestCase):
    def test_estados(self):
        st = {"%1": {"state": "waiting", "tool": "AskUserQuestion"}, "%2": {"state": "waiting", "tool": "Bash"},
              "%3": {"state": "working", "tool": "Bash", "ts": 0}}
        self.assertEqual(sidebar.pane_state(st, "%1", "claude", "0"), "question")
        self.assertEqual(sidebar.pane_state(st, "%2", "claude", "0"), "permission")
        self.assertEqual(sidebar.pane_state(st, "%3", "claude", "0"), "stale")      # 5 min quieto
        self.assertEqual(sidebar.pane_state(st, "%9", "zsh", "0"), "none")
        self.assertEqual(sidebar.pane_state(st, "%9", "claude", "1"), "none")       # pane muerto


class Subagentes(unittest.TestCase):
    """Vivo = el meta.json no dice finished y el transcript se escribió hace menos de 10 min."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.now = 1_000_000.0

    def sub(self, name, meta, age):
        base = os.path.join(self.d, f"agent-{name}")
        with open(base + ".meta.json", "w") as f:
            json.dump(meta, f)
        open(base + ".jsonl", "w").close()
        os.utime(base + ".jsonl", (self.now - age, self.now - age))

    def test_regla_de_vivos(self):
        self.sub("vivo", {"description": "busca \x1b[2Jtests", "requestShape": "background"}, 30)
        self.sub("terminado", {"description": "listo", "finished": True}, 30)
        self.sub("viejo", {"description": "murió sin avisar"}, sidebar.SUB_TTL + 1)
        self.sub("tipo", {"agentType": "Explore"}, 5)
        with open(os.path.join(self.d, "agent-roto.meta.json"), "w") as f:
            f.write("{no es json")
        open(os.path.join(self.d, "agent-roto.jsonl"), "w").close()
        os.utime(os.path.join(self.d, "agent-roto.jsonl"), (self.now, self.now))
        got = sidebar.live_subagents_in(self.d, now=self.now)
        self.assertEqual([(d, bg) for d, bg, _ in got], [("Explore", False), ("busca  [2Jtests", True)])
        self.assertTrue(all("\x1b" not in d for d, _, _ in got))   # sin escapes del modelo en pantalla

    def test_sin_transcript_no_cuenta(self):
        with open(os.path.join(self.d, "agent-x.meta.json"), "w") as f:
            json.dump({"description": "sin jsonl"}, f)
        self.assertEqual(sidebar.live_subagents_in(self.d, now=self.now), [])

    def test_registro_previo_de_bk_no_busca_subagentes(self):
        self.assertEqual(sidebar.subagents("bk-i42"), [])
        self.assertEqual(sidebar.subagents(""), [])

    def test_tooltip_no_se_expande_en_tmux(self):
        self.assertEqual(sidebar.tip("agente 1 · #(rm -rf ~) #{x}"), "agente 1 · ##(rm -rf ~) ##{x}")


class Mascot(unittest.TestCase):
    def label(self, states):
        return STRIP.sub("", rail.mascot(40, states)[-1]).strip()

    def test_humor_segun_las_sesiones(self):
        self.assertIn("durmiendo", self.label([]))
        self.assertIn("volando", self.label(["working"]))
        self.assertIn("segundo plano", self.label(["background"]))
        self.assertIn("pide permiso", self.label(["waiting", "working"]))
        self.assertIn("pregunta", self.label(["question", "waiting"]))

    def test_el_dibujo_entra_en_el_panel(self):
        for line in rail.mascot(40, ["working"]):
            self.assertLessEqual(G.vlen(line), 40)


class Stats(unittest.TestCase):
    def test_resumen_rachas_y_costo(self):
        import stats
        st = {"offsets": {}, "sessions": {"a": [0, 3600]}, "msgs": {
            "m1": ["2026-01-01", "claude-opus-5-5", "p", 1000, 1000, 0, 0, 0],
            "m2": ["2026-01-02", "claude-opus-5-5", "p", 1000, 1000, 0, 0, 0]}}
        s = stats.summary(st)
        self.assertEqual(s["active_days"], 2)
        self.assertEqual(s["longest_streak"], 2)
        self.assertEqual(s["favorite"], "claude-opus-5-5")
        self.assertGreater(s["cost"], 0)

class Tareas(unittest.TestCase):
    """Los lectores de las tareas (ADR-005): bk ls, el riel y alt+a muestran cómo cerró y la cola de bk say."""

    def setUp(self):
        os.environ["BK_TASKS_DIR"] = tempfile.mkdtemp()

    def test_la_tarea_de_una_ventana_prefiere_la_activa(self):
        vieja, _ = TK.create("kit", "9-implementer", "implementer", window="i9")
        TK.close(vieja, "incomplete")
        nueva, _ = TK.create("kit", "9-implementer", "implementer", window="i9")
        self.assertEqual(AG.task_by_window()["i9"]["status"], "in_progress")
        TK.close(nueva, "cancelled")
        self.assertEqual(AG.task_by_window()["i9"]["status"], "cancelled")   # la última que terminó ahí

    def test_notas_de_cierre(self):
        self.assertEqual(AG.task_note({"status": "completed", "result": {"kind": "pr", "pr": 12}}), ("✓", "cerró con PR #12"))
        self.assertEqual(AG.task_note({"status": "completed", "result": {"kind": "blockers"}})[1], "review con bloqueantes")
        self.assertEqual(AG.task_note({"status": "failed", "result": {"kind": "dropped", "reason": "otro alcance"}}),
                         ("✕", "dropped: otro alcance"))
        self.assertEqual(AG.task_note({"status": "incomplete"}), ("⚠", "terminó sin bk done"))
        self.assertEqual(AG.task_note({"status": "cancelled"}), ("✕", "cancelada"))
        self.assertIsNone(AG.task_note({"status": "waiting"}))
        self.assertIsNone(AG.task_note(None))

    def test_cola_y_propuestas(self):
        d, _ = TK.create("kit", "pr3-reviewer", "reviewer", window="pr3", repo="/r/kit")
        self.assertEqual(AG.queued(TK.read(d)), 0)
        for m in ("uno", "dos"):
            with TK.locked(d), open(os.path.join(d, "queue.jsonl"), "a") as f:
                f.write(json.dumps({"msg": m}) + "\n")
        self.assertEqual(AG.queued(TK.read(d)), 2)
        TK.create("kit", "pr4-reviewer", "reviewer", repo="/r/kit", pending=True)
        TK.create("otro", "pr5-reviewer", "reviewer", repo="/r/otro", pending=True)
        self.assertEqual([t["id"] for t in AG.proposals("/r/kit")], ["pr4-reviewer"])
        self.assertEqual(len(AG.proposals()), 2)

    def test_bk_ls_muestra_el_cierre_y_la_cola(self):
        a = {"win": "i9", "kind": "i", "num": "9", "ref": "", "pr": "", "title": "x", "project": "kit",
             "asks": False, "why": "", "question": "", "state": "done", "mins": 3, "note": "terminó sin bk done", "queued": 0}
        self.assertIn("terminó sin bk done", AG.line(a, color=False))
        a.update(note="", state="working", queued=1)
        self.assertIn("trabajando 3m · 1 en cola", AG.line(a, color=False))

    def test_una_tarea_cerrada_no_te_pregunta(self):
        d, _ = TK.create("kit", "pr7-reviewer", "reviewer", window="pr7")
        TK.close(d, "incomplete")
        for name in ("windows", "states", "subagents"):
            self.addCleanup(setattr, AG, name, getattr(AG, name))
        AG.windows = lambda: {"pr7": "%9"}
        AG.states = lambda: {"%9": {"state": "waiting", "tool": "confianza", "ts": 1, "cwd": "/tmp"}}
        AG.subagents = lambda sid: []
        a = [x for x in AG.gather(fetch_titles=False, look=False, chats_too=False) if x["win"] == "pr7"][0]
        self.assertFalse(a["asks"])
        self.assertIn("terminó sin bk done", AG.line(a, color=False))

    def test_el_riel_muestra_el_cierre_y_la_cola(self):
        d, _ = TK.create("kit", "pr7-reviewer", "reviewer", window="pr7")
        with open(os.path.join(d, "queue.jsonl"), "w") as f:
            f.write(json.dumps({"msg": "hola"}) + "\n")
        e, _ = TK.create("kit", "9-implementer", "implementer", window="i9")
        TK.close(e, "incomplete")
        live = [{"win": "pr7", "pane": "%1", "s": {"state": "working", "tool": "-", "ts": 9e9}}]
        ended = [{"win": "i9", "pane": "%2", "s": {}}]
        text = STRIP.sub("", "\n".join(rail.card_agents(60, live, ended)))
        self.assertIn("1 mensaje en cola", text)
        self.assertIn("i9 · terminó sin bk done", text)

    def test_una_carpeta_de_tareas_rota_no_rompe_los_lectores(self):
        os.makedirs(os.path.join(os.environ["BK_TASKS_DIR"], "kit", "roto"))
        with open(os.path.join(os.environ["BK_TASKS_DIR"], "kit", "roto", "task.json"), "w") as f:
            f.write("{no es json")
        self.assertEqual(AG.task_by_window(), {})
        self.assertEqual(AG.proposals(), [])



if __name__ == "__main__":
    unittest.main()


class MonitorVista(unittest.TestCase):
    """bk monitor: qué te espera arriba, agentes y chats separados con encabezados, tareas, desvíos agrupados y leyenda."""

    def setUp(self):
        os.environ["BK_TASKS_DIR"] = tempfile.mkdtemp()
        os.environ["BK_MONITOR_DRY"] = "1"
        import monitor as M
        self.M = M
        self.mon = M.Monitor()
        self.mon.events = []

    def agente(self, **kw):
        a = {"win": "i9", "pane": "%1", "kind": "i", "num": "9", "ref": "", "pr": "", "title": "Algo", "project": "kit",
             "role": "implementer", "model": "sonnet", "state": "working", "asks": False, "why": "", "question": "",
             "mins": 2, "subs": [], "note": "", "queued": 0, "_cost": 1.5, "_tool": "Bash"}
        a.update(kw)
        return a

    def texto(self, lines=60):
        return STRIP.sub("", "\n".join(self.M.frame(self.mon, 150, lines, 0, "agents", 0, None)))

    def test_te_esperan_arriba_con_como_contestar(self):
        self.mon.agents = [self.agente(), self.agente(win="pr7", kind="pr", state="done", asks=True, why="te pregunta",
                                                   question="¿La publico?")]
        t = self.texto()
        self.assertIn("Te esperan", t)
        self.assertLess(t.index("Te esperan"), t.index("Agentes"))
        self.assertIn("«¿La publico?»", t)
        self.assertIn('bk say pr7 "…"', t)
        self.assertIn("1 te espera", t.splitlines()[0])

    def test_nadie_te_espera_y_sin_tarjeta(self):
        self.mon.agents = [self.agente()]
        t = self.texto()
        self.assertNotIn("Te esperan", t)
        self.assertIn("nadie te espera", t.splitlines()[0])
        self.assertIn("1 agente (1 trabajando) · 0 chats", t.splitlines()[0])

    def test_reviewer_propuesto(self):
        import checkpoints as CK
        TK.create("kit", "pr41-reviewer", "reviewer", pr=41, pending=True, origin="40-implementer")
        self.mon.agents, self.mon.checkpoints = [], CK.items(refresh=False)
        t = self.texto()
        self.assertIn("reviewer propuesto", t)
        self.assertIn("bk approve pr41-reviewer", t)
        self.assertIn("1 te espera", t.splitlines()[0])

    def test_agentes_y_chats_separados_con_encabezados(self):
        self.mon.agents = [self.agente(), self.agente(win="chat:bk-kit", kind="chat", short="kit", title="Plan", role="chat",
                                                   state="done", _tool="")]
        t = self.texto()
        for h in ("AGENTE", "ROL · MODELO", "ESTADO", "HACE", "TAREA", "COSTO HOY", "CHAT", "DE QUÉ"):
            self.assertIn(h, t)
        self.assertIn("trabajando · Bash", t)
        self.assertLess(t.index("Agentes"), t.index("Chats"))

    def test_tarea_cerrada_y_cola(self):
        d, _ = TK.create("kit", "9-implementer", "implementer", window="i9")
        with open(os.path.join(d, "queue.jsonl"), "w") as f:
            f.write('{"msg": "hola"}\n')
        self.mon.agents, self.mon.task_list = [self.agente()], TK.tasks()
        self.mon.tasks_by_win = AG.task_by_window()
        t = self.texto()
        self.assertIn("en curso ✉1", t)
        TK.close(d, "incomplete")
        self.mon.agents = [self.agente(note="terminó sin bk done")]
        self.mon.task_list, self.mon.tasks_by_win = TK.tasks(), AG.task_by_window()
        t = self.texto()
        self.assertIn("⚠ sin bk done", t)
        self.assertIn("terminó", t)
        self.assertIn("task.incomplete", t)

    def test_tareas_archivadas_no_se_repiten(self):
        a, _ = TK.create("kit", "9-implementer", "implementer", window="i9")
        TK.close(a, "incomplete")
        TK.create("kit", "9-implementer", "implementer", window="i9")
        self.mon.agents, self.mon.task_list = [], TK.tasks()
        t = self.texto()
        self.assertEqual(t.count("9-implementer"), 1)

    def test_desvios_agrupados(self):
        now = time.time()
        self.mon.events = [{"ts": now - 60 * i, "type": "guardrail_block", "who": "i9", "detail": f"d{i}"} for i in range(3)]
        self.mon.events.append({"ts": now - 600, "type": "interrupt", "who": "i9", "detail": "x"})
        self.mon.events.sort(key=lambda e: e["ts"])
        self.mon.agents = []
        t = self.texto()
        self.assertIn("bloqueado por una baranda ×3", t)
        self.assertIn("lo interrumpiste", t)
        self.assertIn("QUIÉN", t)

    def test_leyenda_y_que_entra_en_pantalla(self):
        self.mon.agents = [self.agente(win=f"i{i}") for i in range(30)]
        lines = self.M.frame(self.mon, 150, 40, 0, "agents", 0, None)
        self.assertLessEqual(len(lines), 40)
        self.assertIn("te pregunta", STRIP.sub("", lines[-2]))   # la leyenda
        self.assertIn("q salir", STRIP.sub("", lines[-1]))
        self.assertTrue(all(G.vlen(l) <= 150 for l in lines))


class Checkpoints(unittest.TestCase):
    """bk checkpoints: una sola fuente de lo que te espera (el monitor, la barra y el comando la leen)."""

    def setUp(self):
        import checkpoints as CK
        self.CK = CK
        os.environ["BK_TASKS_DIR"] = tempfile.mkdtemp()
        CK.PRS = os.path.join(tempfile.mkdtemp(), "prs.json")
        CK.LESSONS = os.path.join(tempfile.mkdtemp(), "lessons")
        os.makedirs(CK.LESSONS)
        self.addCleanup(setattr, AG, "states", AG.states)
        AG.states = lambda: {}

    def test_los_cinco_tipos_en_orden(self):
        AG.states = lambda: {"%1": {"phase": "plan_pending", "state": "done", "cwd": "/r/kit", "_sid": "s1", "pane": "%1"},
                             "%2": {"phase": "plan_pending", "state": "working", "cwd": "/r/kit"}}
        with open(self.CK.PRS, "w") as f:
            json.dump([{"kind": "merge", "repo": "a/kit", "number": 5, "title": "listo"},
                       {"kind": "review", "repo": "a/otro", "number": 7, "title": "miralo"}], f)
        with open(os.path.join(self.CK.LESSONS, "l1.md"), "w") as f:
            f.write("---\nstatus: proposed\nscope: kit  # alcance\n---\n# No mergear solo\n")
        with open(os.path.join(self.CK.LESSONS, "l2.md"), "w") as f:
            f.write("---\nstatus: accepted\n---\n# Ya aceptada\n")
        TK.create("kit", "pr9-reviewer", "reviewer", pr=9, pending=True, origin="8-implementer")
        cs = self.CK.items(refresh=False)
        self.assertEqual([c["kind"] for c in cs], ["plan", "reviewer", "review", "merge", "lesson"])
        todo = {c["kind"]: c["todo"] for c in cs}
        self.assertEqual(todo["merge"], "gh pr merge 5 --squash --repo a/kit")
        self.assertEqual(todo["review"], "bk review 7")
        self.assertEqual(todo["reviewer"], "bk approve pr9-reviewer")
        self.assertEqual([c["title"] for c in cs if c["kind"] == "lesson"], ["No mergear solo"])
        self.assertEqual([c["where"] for c in cs if c["kind"] == "lesson"], ["kit"])
        self.assertEqual([c["cwd"] for c in cs if c["kind"] == "plan"], ["/r/kit"])

    def test_filtra_por_proyecto(self):
        with open(self.CK.PRS, "w") as f:
            json.dump([{"kind": "merge", "repo": "a/kit", "number": 5}, {"kind": "merge", "repo": "a/otro", "number": 6}], f)
        TK.create("kit", "pr1-reviewer", "reviewer", pending=True, repo="/r/kit")
        TK.create("otro", "pr2-reviewer", "reviewer", pending=True, repo="/r/otro")
        cs = self.CK.items("/r/kit", refresh=False)
        self.assertEqual(sorted(str(c.get("number") or c.get("id")) for c in cs), ["5", "pr1-reviewer"])

    def test_una_fuente_rota_no_tapa_las_demas(self):
        with open(self.CK.PRS, "w") as f:
            f.write("{no es json")
        TK.create("kit", "pr1-reviewer", "reviewer", pending=True)
        AG.states = lambda: (_ for _ in ()).throw(RuntimeError("estado roto"))
        self.assertEqual([c["kind"] for c in self.CK.items(refresh=False)], ["reviewer"])

    def test_el_comando(self):
        import subprocess
        TK.create("kit", "pr1-reviewer", "reviewer", pending=True)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, BK_PRS_CACHE=self.CK.PRS, BARRILETE_VAULT=os.path.dirname(os.path.dirname(self.CK.LESSONS)),
                   BK_STATE_DIR=tempfile.mkdtemp())
        out = subprocess.run([os.path.join(root, "bin", "bk"), "checkpoints", "--json"], capture_output=True,
                             text=True, env=env).stdout
        self.assertEqual([c["id"] for c in json.loads(out)], ["pr1-reviewer"])
        out = subprocess.run([os.path.join(root, "bin", "bk"), "checkpoints"], capture_output=True, text=True, env=env).stdout
        self.assertIn("→ bk approve pr1-reviewer", out)

    def test_el_riel_ya_no_tiene_checkpoints(self):
        self.assertFalse(hasattr(rail, "card_checkpoints"))

    def test_pide_los_prs_una_vez_cada_tanto(self):
        import subprocess
        calls = []
        self.addCleanup(setattr, subprocess, "Popen", subprocess.Popen)
        subprocess.Popen = lambda *a, **k: calls.append(a)
        os.environ.pop("BK_CHECKPOINTS_OFFLINE", None)
        self.addCleanup(os.environ.__setitem__, "BK_CHECKPOINTS_OFFLINE", "1")
        self.CK._asked[0] = 0
        for _ in range(5):   # sin caché (sin red): una sola vez, no cada refresco del monitor
            self.CK.refresh_prs()
        self.assertEqual(len(calls), 1)

    def test_prs_viejos_marcados_y_al_final(self):
        old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 25 * 86400))
        new = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
        with open(self.CK.PRS, "w") as f:
            json.dump([{"kind": "merge", "repo": "a/vet", "number": 13, "updated": old},
                       {"kind": "merge", "repo": "a/kit", "number": 5, "updated": new},
                       {"kind": "review", "repo": "a/kit", "number": 6}], f)   # sin fecha: caché vieja
        cs = self.CK.items(refresh=False)
        self.assertEqual([c["number"] for c in cs], [6, 5, 13])
        by = {c["number"]: c for c in cs}
        self.assertTrue(by[13]["stale"])
        self.assertEqual(by[13]["days"], 25)
        self.assertFalse(by[5]["stale"])
        self.assertNotIn("stale", by[6])



class MonitorAgrupa(unittest.TestCase):
    """Te esperan no se llena: las lecciones y los PRs sin movimiento van en una línea cada grupo."""

    def test_lecciones_y_prs_viejos_en_una_linea(self):
        import monitor as M
        mon = M.Monitor()
        mon.events, mon.agents = [], []
        lesson = {"kind": "lesson", "what": "lección propuesta", "where": "kit", "todo": "aceptala o descartala"}
        mon.checkpoints = ([dict(lesson, title=f"lección {i}") for i in range(10)]
                           + [{"kind": "merge", "what": "PR listo para mergear", "title": "viejo", "where": "vet", "number": n,
                               "todo": "gh pr merge", "stale": True, "days": 25} for n in (13, 14)]
                           + [{"kind": "merge", "what": "PR listo para mergear", "title": "nuevo", "where": "kit", "number": 5,
                               "todo": "gh pr merge 5", "stale": False, "days": 0}])
        t = STRIP.sub("", "\n".join(M.frame(mon, 160, 60, 0, "agents", 0, None)))
        self.assertIn("10 lecciones propuestas", t)
        self.assertNotIn("lección 3", t)
        self.assertIn("2 PRs sin movimiento hace más de 14 días (vet#13, vet#14; el más viejo, 25 días)", t)
        self.assertIn("#5 nuevo", t)
        self.assertIn("2 te esperan", t.splitlines()[0])   # las 10 lecciones cuentan como 1, + el PR nuevo; los viejos no


class MonitorLimpiar(unittest.TestCase):
    """c / bk monitor --clear: lo que pasó antes no vuelve, aunque el monitor relea los transcripts del día."""

    def test_limpiar_y_reabrir(self):
        import monitor as M
        self.addCleanup(lambda: os.path.exists(M.CLEARED) and os.remove(M.CLEARED))
        m = M.Monitor()
        m.when = time.time() - 120
        m.add("interrupt", "i9", "viejo")
        self.assertEqual(len(m.events), 1)
        M.clear_events()
        self.assertEqual(os.path.getsize(M.LOG), 0)
        m2 = M.Monitor()                       # reabrir: relee el registro y los transcripts
        m2.when = time.time() - 120            # el mismo desvío releído del transcript
        m2.add("interrupt", "i9", "viejo")
        self.assertEqual(m2.events, [])
        m2.when = time.time() + 1              # uno nuevo, después de limpiar
        m2.add("interrupt", "i9", "nuevo")
        self.assertEqual([e["detail"] for e in m2.events], ["nuevo"])

    def test_el_comando(self):
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run([sys.executable, os.path.join(root, "lib", "monitor.py"), "--clear"],
                             capture_output=True, text=True, env=dict(os.environ)).stdout
        self.assertIn("desvíos limpiados", out)
        import monitor as M
        self.assertGreater(M.cleared_at(), time.time() - 60)
        os.remove(M.CLEARED)


class Detectores(unittest.TestCase):
    """Credencial y atribución sin ruido: lo que el shell ejecuta (no un heredoc o una cita) y solo si la persona no habló."""

    def setUp(self):
        import deviations as D
        self.D = D

    def test_credencial_y_force_push_en_lo_que_corre(self):
        for c in ("gh auth login", "cd x && env -u GH_CONFIG_DIR gh pr list | head", "(unset GH_CONFIG_DIR; gh api user)",
                  "f() { gh auth token; }; f", "bash -c 'gh auth login'", "eval 'unset GH_CONFIG_DIR'",
                  'X="$(gh auth token)" make', 'X="`gh auth token`"', "GH_TOKEN=abc gh pr view",
                  'G="env -u GH_CONFIG_DIR -u GH_TOKEN gh"; $G issue list', "alias g='env -u GH_CONFIG_DIR gh'; g pr list"):
            self.assertTrue(self.D.is_credential(c), c)
        for c in ("python3 - <<'EOF'\nrep('env -u GH_CONFIG_DIR')\nEOF", "printf '%s' '- usa env -u GH_CONFIG_DIR'",
                  'echo "gh auth login es para vos"', "cat <<-EOF > x\n\tunset GH_CONFIG_DIR\n\tEOF\nls",
                  "git commit -m 'no usar gh auth login'"):
            self.assertFalse(self.D.is_credential(c), c)
        self.assertTrue(self.D.is_force_push("bash -c 'git push -f origin main'"))
        self.assertFalse(self.D.is_force_push("git commit -m 'evitá git push --force'"))

    def test_lineal_con_un_comando_enorme(self):
        big = "echo '" + "a" * 400000 + "' && " + "$(" * 2000 + "x"
        t = time.time()
        self.D.is_credential(big)
        self.assertLess(time.time() - t, 2)

    def transcript(self, *mensajes):
        p = os.path.join(tempfile.mkdtemp(), "t.jsonl")
        with open(p, "w") as f:
            for m in mensajes:
                f.write(json.dumps(m) + "\n")
        return p

    def test_atribucion_solo_si_la_persona_no_hablo(self):
        sin = self.transcript({"type": "user", "message": {"content": "/implement-issue 42"}},
                              {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok, sí"}]}},
                              {"type": "user", "message": {"content": "<local-command-stdout>ok</local-command-stdout>"}})
        con = self.transcript({"type": "user", "message": {"content": "sí, dale con la opción b"}})
        txt = "Lo dejé así, como decidiste."
        self.assertIsNotNone(self.D.attribution(txt, sin))       # nadie decidió nada en esa sesión
        self.assertIsNone(self.D.attribution(txt, con))          # la persona sí dijo algo
        self.assertIsNone(self.D.attribution("El revisor escribió: «como decidiste»", sin))   # citado
        self.assertIsNone(self.D.attribution("```\nyou approved the wording\n```", sin))       # en código

    def test_lee_solo_lo_nuevo_del_transcript(self):
        p = self.transcript({"type": "user", "message": {"content": "/implement-issue 1"}})
        self.assertFalse(self.D.person_decided(p))
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "ok, seguí"}}) + "\n")
        self.assertTrue(self.D.person_decided(p))

