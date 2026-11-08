#!/usr/bin/env python3
"""tests/s2d - STAND6 (tools/rules/s2_bodies.py) on a hand-written /O2 unit (no decomp content).

    python3 -I tests/s2d/test_stand6.py

An untagged `__declspec(noinline)` definition whose MSVC name the unit binds to an address another unit defines
goes (a member's definition) or becomes a declaration (a free function); one bound to this unit's own address, a
tagged one, an unbound one, a virtual one and a plain (inlinable) one keep their bodies.
"""
import collections
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'rules'))
import s2_bodies   # noqa: E402

SRC = '''struct Quad { float v[4]; };
extern Quad probe_in[4];
struct Obj
{
    Quad* Get();
    Quad* Own();
    Quad* Tagged();
    virtual Quad* Virt();
    Quad* Plain();
};
__declspec(noinline) Quad* Obj::Get()
{
    probe_in[0] = probe_in[1];
    return &probe_in[0];
}
__declspec(noinline) Quad* Obj::Own()
{
    return &probe_in[1];
}
// @fnv 0x00402000
__declspec(noinline) Quad* Obj::Tagged()
{
    return &probe_in[2];
}
__declspec(noinline) Quad* Obj::Virt()
{
    return &probe_in[3];
}
Quad* Obj::Plain()
{
    return &probe_in[3];
}
__declspec(noinline) int FreeProbe(int k)
{
    return k + 1;
}
__declspec(noinline) int Unbound(int k)
{
    return k + 2;
}
'''


class Stand6(unittest.TestCase):
    def test_rule(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'build'))
            os.makedirs(os.path.join(d, 'src'))
            units = {'units': [
                {'source': 'src/a.cpp', 'flags': ['/O2'],
                 'functions': [{'symbol': '?Own@Obj@@QAEPAUQuad@@XZ', 'address': '0x00401200'}],
                 'symbols': {'?Get@Obj@@QAEPAUQuad@@XZ': '0x00405000', '?Own@Obj@@QAEPAUQuad@@XZ': '0x00401200',
                             '?Tagged@Obj@@QAEPAUQuad@@XZ': '0x00405100', '?Virt@Obj@@UAEPAUQuad@@XZ': '0x00405200',
                             '?Plain@Obj@@QAEPAUQuad@@XZ': '0x00405300', '?FreeProbe@@YAHH@Z': '0x00405400'}},
                {'source': 'src/b.cpp', 'flags': ['/O2'], 'symbols': {}, 'functions': [
                    {'symbol': '?Get@Real@@QAEPAXXZ', 'address': '0x00405000'},
                    {'symbol': '?F@@YAXXZ', 'address': '0x00405100'},
                    {'symbol': '?G@@YAXXZ', 'address': '0x00405200'},
                    {'symbol': '?H@@YAXXZ', 'address': '0x00405300'},
                    {'symbol': '?P@@YAHH@Z', 'address': '0x00405400'}]}]}
            with open(os.path.join(d, 'build', 'units.json'), 'w') as fh:
                json.dump(units, fh)
            path = os.path.join(d, 'src', 'a.cpp')
            with open(path, 'w') as fh:
                fh.write(SRC)
            st = collections.Counter()
            out = s2_bodies.r_noinline_stand_ins(path, SRC, st)
        self.assertEqual(st['STAND6'], 2)
        self.assertNotIn('probe_in[0] = probe_in[1];', out)
        self.assertIn('/* fnv-nx STAND6: Obj::Get is the function at the bound address */', out)
        self.assertIn('__declspec(noinline) int FreeProbe(int k); /* fnv-nx STAND6 */', out)
        for keep in ('return &probe_in[1];', 'return &probe_in[2];', 'Quad* Obj::Virt()\n{', 'Quad* Obj::Plain()\n{',
                     'return k + 2;'):
            self.assertIn(keep, out)
        self.assertEqual(out.count('\n'), SRC.count('\n'))   # line numbers stay


if __name__ == '__main__':
    unittest.main()
