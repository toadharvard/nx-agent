#!/usr/bin/env python3
"""Failing tests for S6n's requests to the orchestrator (render/native/REQUESTS.md). Run from ports/fnv-nx:
    python3 -I tests/s6n/test_requests.py
Each test passes once its request is implemented."""
import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools", "link"))


class Requests(unittest.TestCase):
    def test_r1_vslot_rows(self):
        import fnvlink
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "o.tsv")
            with open(t, "w") as f:
                f.write("vslot:??_7NiDX9Renderer@@6B@:107\tsym_a\tfree text\n00bca760\tsym_b\tfree text\n")
            rows = fnvlink.load_overrides(t)
        self.assertEqual(rows[0][0], ("vslot", "??_7NiDX9Renderer@@6B@", 107))
        self.assertEqual(rows[1][0], 0x00bca760)
        real = fnvlink.load_overrides(os.path.join(ROOT, "render", "native", "overrides.tsv"))
        self.assertIn(0x00e744b0, [r[0] for r in real])

    def test_r2_vftable_dump(self):
        p = os.path.join(ROOT, "render", "native", "vftables.tsv")
        self.assertTrue(os.path.exists(p), "render/native/vftables.tsv (R2) missing")
        slots = {int(l.split("\t")[1]) for l in open(p) if l.startswith("??_7NiDX9Renderer@@6B@\t")}
        self.assertTrue(set(range(95, 112)) <= slots)

    def test_r3_native1_rule(self):
        rule = os.path.join(ROOT, "tools", "rules", "native1.py")
        self.assertTrue(os.path.exists(rule), "tools/rules/native1.py (R3) missing")
        with tempfile.TemporaryDirectory() as d:
            units = [{"src": "src/a.cpp"}, {"src": "src/gamebryo2.2/corelibs/niconsolerenderer/x.cpp"}, {"src": "src/b.cpp"}]
            json.dump({"units": units}, open(os.path.join(d, "units.json"), "w"))
            open(os.path.join(d, "drop.txt"), "w").write("# test\nsrc/gamebryo2.2/corelibs/niconsolerenderer/*\n")
            subprocess.run([sys.executable, "-I", rule, os.path.join(d, "units.json"), os.path.join(d, "drop.txt"),
                            os.path.join(d, "out.json")], check=True)
            out = json.load(open(os.path.join(d, "out.json")))
            self.assertEqual([u["src"] for u in out["units"]], ["src/a.cpp", "src/b.cpp"])


if __name__ == "__main__":
    unittest.main()
