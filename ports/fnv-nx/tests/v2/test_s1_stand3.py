#!/usr/bin/env python3
"""V2 adversarial cases for S1-fuzzA d9a5519 (STAND3, tools/rules/s1_compile.py r_inline_stand_ins): "only when every
spelling of the name in the unit binds one address that some unit decompiles".

    python3 tests/v2/test_s1_stand3.py      # our own synthetic unit and units.json; nothing decomp-derived

The rule collects the bound addresses by the name's decoration prefix (`?f@S@@`), so a definition whose own spelling
is not in units.json loses its body when an overload of the same name is bound:
  O1  `int f(int)` bound to 0x00401230, `int f(float)` unbound (its own inline body): f(float) must keep its body
  O2  const overload: `int Get()` bound, `int Get() const` unbound
  C1  control: the bound f(int) becomes a declaration (STAND3's claim)
With the body gone the unbound overload is an undefined name GCC calls, where MSVC /Od called the unit's own copy.
Real at decomp 87eb9373: tesworldspace.cpp `NiPoint2() {}` (unbound) becomes `NiPoint2(); /* fnv-nx STAND3 0x00452dc0 */`,
the address of the bound NiPoint2(float, float); a scan of the 109 STAND3 sites finds 16 names with more overloads
than bound spellings.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from collections import Counter

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
TOOLS = os.path.join(PORT, 'tools')
sys.path.insert(0, TOOLS)
spec = importlib.util.spec_from_file_location('s1_compile', os.path.join(TOOLS, 'rules', 's1_compile.py'))
s1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s1)

SRC = ('struct S\n{\n    int f(int x)\n    {\n        return x + 1;\n    }\n    int f(float y)\n    {\n        return 2;\n'
       '    }\n    int Get()\n    {\n        return 3;\n    }\n    int Get() const\n    {\n        return 4;\n    }\n};\n'
       '// @fnv 0x00401300\nint use(S* s, const S* c) { return s->f(1) + s->f(1.0f) + s->Get() + c->Get(); }\n')
SYMS = {'?f@S@@QAEHH@Z': '0x00401230', '?Get@S@@QAEHXZ': '0x00401240'}


def main():
    work = tempfile.mkdtemp(prefix='v2-stand3-')   # raises when it cannot create the directory
    fails = 0
    try:
        os.makedirs(os.path.join(work, 'src'))
        os.makedirs(os.path.join(work, 'build'))
        json.dump({'units': [
            {'source': 'src/od.cpp', 'flags': ['/MT', '/Od', '/Oy-', '/GS', '/EHsc', '/Gy'], 'symbols': SYMS,
             'functions': []},
            {'source': 'src/other.cpp', 'flags': ['/MT', '/Od'], 'symbols': {},
             'functions': [{'address': a, 'symbol': '?g@@YAXXZ', 'size': 4} for a in ('0x00401230', '0x00401240')]}]},
            open(os.path.join(work, 'build', 'units.json'), 'w'))
        p = os.path.join(work, 'src', 'od.cpp')
        open(p, 'w').write(SRC)
        s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = None, None, None
        out = s1.r_inline_stand_ins(p, SRC, Counter())
        for case, ok in (('O1 f(float) keeps its body', 'return 2;' in out),
                         ('O2 Get() const keeps its body', 'return 4;' in out),
                         ('C1 bound f(int) becomes a declaration', 'return x + 1;' not in out)):
            print('%s %s' % ('ok' if ok else 'FAIL', case))
            fails += not ok
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
