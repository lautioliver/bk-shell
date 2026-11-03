"""Tests de las vistas en Python: anchos con color, estados de la tira, el humor de Barrilete y stats."""
import json, os, re, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
os.environ.setdefault("BK_STATE_DIR", tempfile.mkdtemp())
import gentle as G          # noqa: E402
import rail                 # noqa: E402
import sidebar              # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
