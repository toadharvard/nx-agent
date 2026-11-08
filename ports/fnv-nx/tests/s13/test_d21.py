#!/usr/bin/env python3
"""tests/s13/test_d21.py - F2 d21 (virtual calls and vtables): a call into a hole of the native stub range is traced
once (d21-holes), a destructor's vptr words are not compared (d21-dtorvptr), and a call through another slot of the
planted vftable is vslot-shift / vslot-differs, not followed (d21-vslot). Synthetic inputs only."""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
sys.path.insert(0, HERE)
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13d21'))

import arm64emu   # noqa: E402
import causes     # noqa: E402
import classify   # noqa: E402
import conv       # noqa: E402
import fnkeys     # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
import x86emu     # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def words(base, *ws):
    out = {}
    for i, v in enumerate(ws):
        if v is not None:
            for k, b in enumerate(struct.pack('<I', v)):
                out[base + 4 * i + k] = b
    return out


def main():
    V, V2, AP = 0x0104dd7c, 0x010b4460, 0x0a16b634
    is_vft = {V, V2}.__contains__
    is_nat = lambda v: v == AP   # noqa: E731
    B = world.BUF
    # dtor_vptr_bytes: which differing words are vptr words
    for wx, wa, n, what in (
            (words(B, V), words(B, AP), 4, 'image vftable vs native address point'),
            (words(B, V), {}, 4, 'image vftable vs left as it was'),
            ({}, words(B, AP), 4, 'left as it was vs native address point'),
            (words(B, V), words(B, V2), 3, 'two image vftables (a kind-V row maps the native one; 3 bytes differ)'),
            (words(B, 0x12345678), words(B, AP), 0, 'not a vftable on the oracle: compared'),
            (words(B, V), words(B, 0x0a000010), 0, 'not a vtable natively: compared'),
            (words(B, 0), words(B, AP), 0, 'a nulled vptr: compared')):
        diff = sorted(k for k in set(wx) | set(wa) if wx.get(k) != wa.get(k))
        got = runner.dtor_vptr_bytes(diff, wx, wa, is_vft, is_nat)
        check(len(got) == n, 'dtor vptr word, %s: %d bytes dropped (%d)' % (what, n, len(got)))
    # compare: the vptr word alone is soft vptr-store, a later field still differs
    cv = conv.Conv('??1Foo@@QAE@XZ', 'test')
    w = world.World(world.Case(seed=1), {}, {})

    def res(wx, wa):
        a, b = x86emu.Result(), arm64emu.Result()
        a.ret = b.ret = None
        a.writes, b.writes = dict(wx), dict(wa)
        return a, b
    rx, ra = res(words(B, V), words(B, AP))
    d = runner.compare(cv, rx, ra, w, vptr_words=(is_vft, is_nat))
    check(d is not None and d.get('soft') and d.get('vptr_store'), 'only the vptr word: soft vptr_store (%s)' % d)
    check(classify.hint(None, None, None, d, dict(code=b'\xc3', address=0x00401000), 0).get('cause') == 'vptr-store',
          'classify: vptr_store -> vptr-store')
    rx, ra = res(words(B, V, None, 5), words(B, AP, None, 6))
    d = runner.compare(cv, rx, ra, w, vptr_words=(is_vft, is_nat))
    check(d is not None and d['kind'] == 'mem-write' and not d.get('soft') and d['addr'] == '%08x' % (B + 8),
          'the vptr word and a field: the field is reported (%s)' % (d and d.get('addr')))
    rx, ra = res(words(B, V), words(B, AP))
    d = runner.compare(cv, rx, ra, w)
    check(d is not None and d['kind'] == 'mem-write' and not d.get('soft'), 'no destructor: compared as before')
    # d21-holes: a fetch in a hole of the stub range is the wild hook's call only
    calls = []

    class Fake:
        def holes(self):
            return [(0x09800000, 0x0a000000)]

        def _builtin(self, *a):
            calls.append(a)
            return True
    r = arm64emu.Result()
    arm64emu.Arm64Engine._on_stub(Fake(), None, 0x09800100, 4, (world.Case(), r))
    check(not calls and not r.calls, 'stub hook: a hole address is left to the wild hook')
    arm64emu.Arm64Engine._on_stub(Fake(), None, 0x09800100, 4, (world.Case(), r), wild=True)
    check(len(calls) == 1, 'wild hook: the hole address is the call')
    # integration: native code branching into a code-stub hole (below .text) and a named-stub hole
    from test_d14 import build_image
    cvv = conv.Conv('?f@@YAXXZ', 'test')
    body = ('\tstp x29, x30, [sp, #-16]!\n'
            '\tmovz w16, #0x5ef8\n\tmovk w16, #0x0811, lsl #16\n\tblr x16\n'
            '\tmovz w16, #0x0100\n\tmovk w16, #0x0980, lsl #16\n\tblr x16\n'
            '\tldp x29, x30, [sp], #16\n\tret\n')
    oracle = bytes.fromhex('b8f85e1108' 'ffd0' 'b800018009' 'ffd0' 'c3')   # mov eax, X; call eax (twice); ret
    with tempfile.TemporaryDirectory() as td:
        img = build_image(body, td)
        p = runner.Pair(runner.Target(cvv, 0x00401000, {0x00401000: oracle}, img, {}), {})
        rx, ra, dv = p.run(world.Case(seed=1))
        ta = ['%08x' % (c.target & 0xffffffff) for c in ra.calls]
        tx = ['%08x' % (c.target & 0xffffffff) for c in rx.calls]
        check(ta == ['08115ef8', '09800100'], 'native: each hole call traced once, by its raw word (%s)' % ta)
        check(tx == ta and dv is None, 'oracle: the same calls, no divergence (%s, %s)' % (tx, dv))
    # d21-vslot: Pair.vslots numbers both callees in the planted vftable
    slots = [0x00401100, 0x00401200, 0x00401300, 0x00401400]

    class T:
        vt_pins = {V: struct.pack('<4I', *slots)}
        x86_code = {0x00401300: b'\xc3'}
    pr = runner.Pair.__new__(runner.Pair)
    pr.target = T()
    case = world.Case(seed=1, vptrs={B: V})
    vs = pr.vslots(case, 0x00401300, 0x00401200)
    check(vs and vs[0]['shift'] == -1 and vs[0]['x86'] == [2] and vs[0]['arm'] == [1],
          'vslots: the native callee one slot lower (%s)' % vs)
    vs = pr.vslots(case, 0x00401400, 0x00401200)
    check(vs and vs[0]['shift'] is None, 'vslots: two slots lower is no shift -1')
    a, b = x86emu.Result(), arm64emu.Result()
    b.calls = [arm64emu.Call(0x00401200, [0] * 8, [0] * 8, 0)]
    a.blocks = {0x00401300}
    vs = pr.vslots_run(case, a, b)
    check(vs and vs[0]['shift'] == -1 and vs[0].get('ran'), 'vslots_run: the oracle ran slot k+1 for real (%s)' % vs)
    a.blocks = set()
    check(pr.vslots_run(case, a, b) is None, 'vslots_run: no evidence without the oracle running slot k+1')
    fn = dict(code=b'\xc3', address=0x00401000)
    h = lambda dv: classify.hint(None, None, None, dv, fn, 0).get('cause')   # noqa: E731
    check(h(dict(kind='call-target', x86='00401300', arm='00401200', vcall='planted', arm_vcall='planted',
                 vslot=[dict(shift=-1)])) == 'vslot-shift', 'classify: vslot-shift')
    check(h(dict(kind='call-target', x86='00401400', arm='00401200', vcall='planted', arm_vcall='planted',
                 vslot=[dict(shift=None)])) == 'vslot-differs', 'classify: vslot-differs')
    check(h(dict(kind='call-count', x86=0, arm=1, vslot=[dict(shift=-1)])) == 'vslot-shift',
          'classify: call-count with the shifted slot is vslot-shift')
    for c in ('vslot-shift', 'vslot-differs'):
        check(causes.policy(c)[0] == 'real-bug' and causes.FIX_CLASS_OF.get(c) == 'vtable-slots',
              '%s: real bug of the vtable-slots fix class' % c)
    # compare marks the native's planted slot
    rx, ra = x86emu.Result(), arm64emu.Result()
    rx.calls = [x86emu.Call(0x00401300, [0, 0], 0)]
    ra.calls = [arm64emu.Call(0x00401200, [0] * 8, [0] * 8, 0)]
    rx.ret = ra.ret = None
    d = runner.compare(cvv, rx, ra, w, vcalls=(set(), set(slots)))
    check(d and d.get('vcall') == 'planted' and d.get('arm_vcall') == 'planted', 'compare: both callees planted')
    # scoped changes
    tags = [c.tag for c in fnkeys.CHANGES]
    check(fnkeys.SCOPED == 'd21' and {'d21-holes', 'd21-dtorvptr', 'd21-vslot'} <= set(tags), 'd21 change rows')
    rc = {c.tag: c.recheck for c in fnkeys.CHANGES}
    check(rc['d21-holes'](dict(divergence=dict(arm_trace=['00401000', 'name:9775ef7', '09775ef7']))) and
          rc['d21-holes'](dict(divergence=dict(arm='00115ef8', arm_next='08115ef8'))) and
          not rc['d21-holes'](dict(divergence=dict(arm_trace=['00401000', '00402000']))), 'd21-holes recheck')
    check(rc['d21-dtorvptr'](dict(symbol='??_GFoo@@UAEPAXI@Z', divergence=dict(kind='mem-write'))) and
          not rc['d21-dtorvptr'](dict(symbol='?f@@YAXXZ', divergence=dict(kind='mem-write'))), 'd21-dtorvptr recheck')
    check(rc['d21-vslot'](dict(case=dict(vptrs={'03000000': '0104dd7c'}), divergence=dict(kind='call-count'))) and
          not rc['d21-vslot'](dict(case=dict(), divergence=dict(kind='call-count'))), 'd21-vslot recheck')
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
