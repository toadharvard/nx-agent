#!/usr/bin/env python3
"""Tests for verify/gate_rawrite.py (pure judge; the build evidence is gate_build.ensure's PP and macro-fact stages, read as in gate_hostapi): both directions.
  - a hand-written unit that includes fnvl_prelude.h or whose -dU facts hold `#define FNVL_RA_WRITE` / `#define FNVL_PRELUDE_H` is red;
  - the same unit as a generator output is green; the TU that defines fnvl_native_transfer and fnvl_raise_resume is green (by its definition fact, not its name);
  - a unit that only includes fnvl.h (both macros tested undefined) is green; a second TU that defines the pair is red.
Run: python3 -I <this file>."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import gate_rawrite as g  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:300]))
    fails += 0 if ok else 1


READER_II = '# 1 "u.cc"\n# 1 "/rt/fnvl.h" 1\nint x;\n'
READER_DU = "#undef FNVL_PRELUDE_H\n#undef FNVL_RA_WRITE\n"
PRELUDE_II = READER_II + '# 1 "/rt/fnvl_prelude.h" 1\n'
WRITE_DU = "#define FNVL_RA_WRITE 1\n"


def unit(i, ii=READER_II, du=READER_DU, generated=False, defines=()):
    return {"id": i, "ii": ii, "du": du, "generated": generated, "defines": set(defines)}


check("a reader of fnvl.h (macros tested undefined) is no writer", g.is_writer(READER_II, READER_DU) == [])
check("including fnvl_prelude.h is write access", g.is_writer(PRELUDE_II, READER_DU) == ["includes fnvl_prelude.h"])
check("-dU holding #define FNVL_RA_WRITE is write access, however it was defined", g.is_writer(READER_II, WRITE_DU) == ["defines FNVL_RA_WRITE"])
check("-dU holding #define FNVL_PRELUDE_H is write access", g.is_writer(READER_II, "#define FNVL_PRELUDE_H\n") == ["defines FNVL_PRELUDE_H"])
check("a macro named only in an #undef or a longer name is none", g.is_writer(READER_II, "#undef FNVL_RA_WRITE\n#define FNVL_RA_WRITE_X 1\n") == [])
u, c, ids = g.judge([unit("r:a.cc"), unit("r:b.cc", generated=True, du=WRITE_DU), unit("r:enter.cc", du=WRITE_DU, defines=g.WRITER_SYMBOLS)])
check("readers, a generated writer and the pair's TU are green", (u, c, ids) == (3, 3, []), (u, c, ids))
u, c, ids = g.judge([unit("r:hand.cc", du=WRITE_DU)])
check("a hand-written writer is red", (u, c) == (1, 0) and "hand-written unit with write access" in ids[0], (u, c, ids))
u, c, ids = g.judge([unit("r:hand.cc", ii=PRELUDE_II)])
check("a hand-written unit that includes the prelude is red", (u, c) == (1, 0), (u, c, ids))
u, c, ids = g.judge([unit("r:one.cc", du=WRITE_DU, defines=["fnvl_native_transfer"])])
check("defining only one symbol of the pair is no exception", (u, c) == (1, 0), (u, c, ids))
u, c, ids = g.judge([unit("r:e1.cc", du=WRITE_DU, defines=g.WRITER_SYMBOLS), unit("r:e2.cc", du=WRITE_DU, defines=g.WRITER_SYMBOLS)])
check("a second TU that defines the pair is red (the exception is one TU)", (u, c) == (2, 0) and len(ids) == 2, (u, c, ids))
u, c, ids = g.judge([])
check("no unit: universe 0", (u, c, ids) == (0, 0, []))
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
