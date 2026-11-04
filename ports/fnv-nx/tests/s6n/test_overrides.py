#!/usr/bin/env python3
"""The N1 override table against the linker and the built archive. Run from ports/fnv-nx:
    python3 -I tests/s6n/test_overrides.py [<libnxrender.a>]     (default /home/user/work/s6n/n1/libnxrender.a)
Every row the linker enables (not N1-pending) must name a symbol libnxrender.a defines, and its address must be a
decomp definition (so the ABI check has the signature)."""
import json, os, sys, unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools", "link"))
LIB = sys.argv.pop(1) if len(sys.argv) > 1 else "/home/user/work/s6n/n1/libnxrender.a"
TABLE = os.path.join(ROOT, "render", "native", "overrides.tsv")


class Overrides(unittest.TestCase):
    def setUp(self):
        import fnvlink, elfsyms
        self.rows = fnvlink.load_overrides(TABLE, LIB)
        self.pending = fnvlink.override_pending(TABLE)
        self.syms = elfsyms.archive_symbols(LIB)
        self.active = [r for r in self.rows if r[0] not in self.pending]

    def test_active_rows_defined_in_archive(self):
        self.assertGreaterEqual(len(self.active), 5)
        for key, sym, obj in self.active:
            self.assertEqual(obj, LIB)
            self.assertIn(sym, self.syms, "%r: %s not in %s" % (key, sym, LIB))

    def test_active_addresses_are_decomp_definitions(self):
        units = json.load(open(os.path.join(ROOT, "ext", "fnv_decomp", "build", "units.json")))["units"]
        defs = {int(f["address"], 16) for u in units for f in u["functions"]}
        for key, sym, _ in self.active:
            if isinstance(key, int):
                self.assertIn(key, defs, "%08x has no decomp definition" % key)

    def test_no_duplicate_keys(self):
        keys = [r[0] for r in self.rows]
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
