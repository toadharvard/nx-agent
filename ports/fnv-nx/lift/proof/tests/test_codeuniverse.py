#!/usr/bin/env python3
"""proof/codeuniverse.py on a small real image region. The code is the concatenation of the real encodings of sdm_shape_rows.tsv (the expected keys there are
read from the SDM's opcode map and ModRM / SIB tables, not computed by shape.py), placed at 0x401000 as an image; the pack is built from it exactly as liftgen
writes one (instrs.tsv rows, shapes.tsv, u/*.cpp with the lifter's `L_<va>: ;` marker per instruction). Green case and the red cases: a key missing from
shapes.tsv, an extra key, a wrong count, a byte mismatch against the image, an instruction of the code list without a marker, a marker without a row,
a doubled marker, an undeclared image, two overlapping images."""
import collections
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from fnvlift.image import Image  # noqa: E402
from proof import codeuniverse as C  # noqa: E402

BASE = 0x401000
ROWS = [r.rstrip("\n").split("\t") for r in open(os.path.join(HERE, "sdm_shape_rows.tsv")) if r[0] != "#"]
CODE = b"".join(bytes.fromhex(r[0]) for r in ROWS)
ADDR = []
a = BASE
for r in ROWS:
    ADDR.append(a)
    a += len(r[0]) // 2


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.img = {"exe": Image.from_bytes(CODE, BASE)}

    def pack(self, instrs=None, shapes=None, marks=None):
        """write a pack of the code: INSTRS rows (image, va, hex) / SHAPES {key: (family, count)} / MARKS [va] override the faithful ones"""
        rows = instrs if instrs is not None else [("exe", ADDR[i], r[0]) for i, r in enumerate(ROWS)]
        shp = shapes if shapes is not None else {r[2]: (r[3], 1) for r in ROWS}
        mk = marks if marks is not None else [ADDR[i] for i in range(len(ROWS))]
        os.makedirs(self.d + "/u", exist_ok=True)
        with open(self.d + "/instrs.tsv", "w") as f:
            for n, va, hx in sorted(rows, key=lambda x: (x[0], x[1])):
                f.write("%s\t%08x\t%d\t%s\n" % (n, va, len(hx) // 2, hx))
        with open(self.d + "/shapes.tsv", "w") as f:
            for k in sorted(shp):
                f.write("%s\t%s\t%d\n" % (k, shp[k][0], shp[k][1]))
        with open(self.d + "/u/000.cpp", "w") as f:
            f.write('#include "x.h"\nextern "C" void L_00401000(void)\n{ DECL; SYNC_IN; u32 E=esp;\n')
            for va in mk:
                f.write("L_%08x: ;\nTRACE(0x%xu);\n" % (va, va))
            f.write("L_00401fff: ; SYNC_OUT; REGS(); FNVL_RAISE(FNVL_UD,0x401fffu); REGS(); SYNC_IN; goto nonlocal;\n}\n")     # bytes that do not decode: not a marker

    def codes(self, images=None):
        return collections.Counter(f["code"] for f in C.judge_pack(self.d, images or self.img))

    def test_green(self):
        self.pack()
        self.assertEqual(C.judge_pack(self.d, self.img), [])

    def test_key_missing_from_shapes(self):
        shp = {r[2]: (r[3], 1) for r in ROWS}
        del shp[ROWS[3][2]]
        self.pack(shapes=shp)
        fs = C.judge_pack(self.d, self.img)
        self.assertEqual([(f["code"], f["image"], f["va"]) for f in fs], [("code-key-missing", "exe", "%08x" % ADDR[3])])
        self.assertIn(ROWS[3][2], fs[0]["detail"])

    def test_extra_key(self):
        shp = {r[2]: (r[3], 1) for r in ROWS}
        shp["90/0.0.0.0"] = ("int", 1)
        self.pack(shapes=shp)
        self.assertEqual(self.codes(), {"code-key-extra": 1})

    def test_count_and_family(self):
        shp = {r[2]: (r[3], 1) for r in ROWS}
        shp[ROWS[0][2]] = ("int", 2)
        shp[ROWS[9][2]] = ("simd", 1)
        self.pack(shapes=shp)
        self.assertEqual(self.codes(), {"code-count": 2})

    def test_bytes_differ_from_the_image(self):
        rows = [("exe", ADDR[i], r[0]) for i, r in enumerate(ROWS)]
        rows[1] = ("exe", ADDR[1], "8b45f5")        # a row that says another displacement than the image has
        self.pack(instrs=rows)
        fs = C.judge_pack(self.d, self.img)
        self.assertEqual([(f["code"], f["va"]) for f in fs if f["code"] == "code-bytes"], [("code-bytes", "%08x" % ADDR[1])])

    def test_instruction_without_marker(self):
        self.pack(marks=[v for v in ADDR if v != ADDR[4]])
        fs = C.judge_pack(self.d, self.img)
        self.assertEqual([(f["code"], f["va"]) for f in fs], [("code-marker-missing", "%08x" % ADDR[4])])

    def test_marker_without_row(self):
        self.pack(marks=ADDR + [BASE + 2])      # an address in the code that the list does not name
        fs = C.judge_pack(self.d, self.img)
        self.assertEqual([(f["code"], f["image"], f["va"]) for f in fs], [("code-marker-extra", "exe", "%08x" % (BASE + 2))])

    def test_marker_count(self):
        self.pack(marks=ADDR + [ADDR[2]])       # the same instruction emitted in two functions, listed once
        self.assertEqual(self.codes(), {"code-marker-count": 1})

    def test_undeclared_image(self):
        rows = [("exe", ADDR[i], r[0]) for i, r in enumerate(ROWS)]
        rows[0] = ("zzz", ADDR[0], ROWS[0][0])
        self.pack(instrs=rows)
        self.assertIn("code-image", self.codes())

    def test_overlapping_images(self):
        self.pack()
        both = {"exe": self.img["exe"], "dll": Image.from_bytes(CODE, BASE + 4)}
        self.assertIn("code-image", self.codes(both))

    def test_malformed_files(self):
        self.pack()
        with open(self.d + "/instrs.tsv", "a") as f:
            f.write("exe\t00401000\t1\t55\n")       # not sorted
        with self.assertRaises(C.Bad):
            C.judge_pack(self.d, self.img)


if __name__ == "__main__":
    unittest.main()
