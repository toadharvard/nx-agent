#!/usr/bin/env python3
"""tests/s13/test_causes.py - self-test of tools/difffuzz/causes.py on hand-written divergence records.

No emulator, no decomp: every record below is built by hand in the shape runner.compare / fuzz.py write. Checks
each record-level rule, that a probe's definite cause is never overridden, that report.reclassify uses the rules and
the policy's soft / oracle-gap split, and the fnvnx-causes/1 summary of a small campaign directory.
"""
import json
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13causes'))
os.environ.setdefault('FNVNX_DFLINK', os.path.join(os.environ['FNVNX_DFWORK'], 'nolink'))

import causes  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def f32(x):
    return struct.pack('<f', x).hex()


def f64(x):
    return struct.pack('<d', x).hex()


def w(*vals):
    return b''.join(struct.pack('<I', v) for v in vals).hex()


def rec(div, cause='unclassified', addr='00401000'):
    return dict(source='src/t.cpp', address=addr, symbol='?f@@YAXXZ', divergence=div, hint=dict(cause=cause),
                case={})


def refined(div, cause='unclassified'):
    return causes.refine(rec(div, cause), cause)


def test_rules():
    # errors
    check(refined(dict(kind='error', x86=None, arm='exc:1@0a000010')) == 'native-trap', 'native brk: native-trap')
    check(refined(dict(kind='error', x86=None, arm='stack-protector')) == 'stack-protector', 'stack protector')
    check(refined(dict(kind='error', x86=None, arm='fault:r:00000008')) == 'native-null-read', 'native null read')
    check(refined(dict(kind='error', x86=None, arm='fault:r:08001234')) == 'native-stub-data',
          'native reads a stub page as data')
    check(refined(dict(kind='error', x86=None, arm='fault:x:08001234')) == 'unclassified',
          'native fetch fault in the stub range is not a data access')
    check(refined(dict(kind='error', x86='exc:3', arm=None)) == 'oracle-trap', 'oracle runs into int3 filler')
    check(refined(dict(kind='error', x86='fault:r:00000004', arm=None)) == 'oracle-fault', 'oracle-only data fault')
    check(refined(dict(kind='error', x86=None, arm='no-native-entry')) == 'no-native-entry', 'no native entry')
    check(refined(dict(kind='error', x86='fault:r:0be1aab8', arm=None)) == 'oracle-native-address',
          'oracle dereferences a native address')
    # call arguments
    check(refined(dict(kind='call-args', arg='p1', cls='F32', x86=f32(1.0), arm=f32(1.0000001))) == 'x87-precision',
          'float argument 1 ulp apart: x87-precision')
    check(refined(dict(kind='call-args', arg='p1', cls='F64', x86=f64(1.0), arm=f64(1.5))) == 'unclassified',
          'float argument far apart stays open')
    check(refined(dict(kind='call-args', arg='p1', cls='F32', x86=w(0xffc00000), arm=w(0x7fc00000))) == 'nan-bits',
          'NaN arguments: nan-bits')
    check(refined(dict(kind='call-args', arg='p0', cls='P', x86=w(0x01100000), arm=w(0x0a004000))) ==
          'native-address', 'image address vs native image address argument')
    check(refined(dict(kind='call-args', arg='p0', cls='I32', x86=w(5), arm=w(7), arm_garbage=True)) ==
          'native-garbage-arg', 'native garbage argument')
    check(refined(dict(kind='call-args', arg='sret', cls='P', x86=w(0x03000000), arm=w(0)))
          == 'sret-mismatch', 'hidden result pointer differs')
    check(refined(dict(kind='call-args', arg='this', cls='P', x86=w(0x06effff0), arm=w(0x03002000))) ==
          'arg-stack-vs-object', 'stack temporary vs the object')
    check(refined(dict(kind='call-args', arg='p0', cls='P', x86=w(0x03002000), arm=w(0x03001000))) ==
          'arg-other-buffer', 'another argument buffer')
    check(refined(dict(kind='call-args', arg='p0', cls='P', x86=w(0x03801048), arm=w(0x03801040))) ==
          'layout-offset', 'this+8 vs this+0: layout-offset')
    check(refined(dict(kind='call-args', arg='p1', cls='I32', x86=w(0x9cf46a07), arm=w(0x9cf46a27))) ==
          'x87-precision', 'float bits through an int parameter, 32 ulp apart')
    check(refined(dict(kind='call-args', arg='p0', cls='I32', x86=w(0x14), arm=w(0x13))) == 'unclassified',
          'small ints are not float bits')
    check(refined(dict(kind='call-args', arg='p0', cls='I32', x86=w(5), arm=w(7))) == 'unclassified',
          'plain int argument difference stays open')
    # return values
    check(refined(dict(kind='return', x86=w(0x01001000), arm=w(0x0a100000), pointee_x86='', pointee_arm='6869'))
          == 'literal-ret', 'pointer to a literal the oracle side cannot read')
    check(refined(dict(kind='return', x86=w(0x01001000), arm=w(0x0a100000), pointee_x86='6869', pointee_arm='6869'))
          == 'native-address', 'pointer result: native image vs image window, pointees known')
    check(refined(dict(kind='return', x86=f64(0.30000000000000004), arm=f64(0.3),
                       float=dict(tolerant=False, x86='0.30000000000000004', arm='0.3', x87='0.3')))
          == 'x87-precision', 'native equals the unrounded x87 value')
    check(refined(dict(kind='return', x86=w(1), arm=w(2))) == 'unclassified', 'int return stays open')
    check(causes.refine(dict(rec(dict(kind='return', x86=w(0x03000000), arm=w(0x1553bc3b))),
                             symbol='??_GFUN_00ba8e20@@UAEPAXI@Z'), 'unclassified') == 'deleting-dtor-ret',
          'scalar deleting destructor result')
    # memory writes
    check(refined(dict(kind='mem-write', addr='03000010', ndiff=4, only_x86=0, only_arm=0, x86=w(0xffc00000, 0),
                       arm=w(0x7fc00000, 0))) == 'nan-bits', 'NaN store: nan-bits')
    check(refined(dict(kind='mem-write', addr='03000010', ndiff=1, only_x86=0, only_arm=0, x86=w(1, 0),
                       arm=w(2, 0), float_word=dict(tolerant=True))) == 'x87-precision', 'tolerant float store')
    check(refined(dict(kind='mem-write', addr='03000010', ndiff=4, only_x86=0, only_arm=0, x86=w(0x01200000, 0),
                       arm=w(0x0a200000, 0))) == 'native-address', 'native image address stored')
    check(refined(dict(kind='mem-write', addr='03000011', ndiff=3, only_x86=3, only_arm=0, x86=w(0x00000001, 0),
                       arm=w(0x00000001, 0)[:2] + '000000' + w(0)) ) in ('store-width', 'x86-only-store'),
          'oracle-only bytes are a store difference')
    check(refined(dict(kind='mem-write', addr='03000000', ndiff=4, only_x86=0, only_arm=0, x86=w(0x02f018f0, 0),
                       arm=w(0x0a076000, 0))) == 'literal-store', 'pinned literal vs native .rodata literal stored')
    check(refined(dict(kind='call-args', arg='p0', cls='P', x86=w(0x02f00010), arm=w(0x0a076000))) == 'literal-arg',
          'pinned literal argument')
    causes._VFT, causes._LINKSYM = {0x0102e388, 0x010d7f44}, {}
    try:
        vw = dict(kind='mem-write', addr='03000000', ndiff=4, only_x86=0, only_arm=0, x86=w(0x0102e388, 0),
                  arm=w(0x010d7f44, 0))
        check(causes.refine(dict(rec(vw), symbol='??1FUN_00ddcc60@@UAE@XZ'), 'unclassified') == 'vptr-store',
              'base vftable reset in a destructor: vptr-store')
        check(causes.refine(dict(rec(vw), symbol='??0FUN_00590210@@QAE@XZ'), 'unclassified') == 'vptr-differs',
              'different vftables in a constructor: vptr-differs')
    finally:
        causes._VFT = causes._LINKSYM = None
    check(causes._narrower(bytes.fromhex(w(0x01020304)), bytes.fromhex('04000000')), 'narrower: byte vs dword')
    check(not causes._narrower(bytes.fromhex(w(0x01020304)), bytes.fromhex('05000000')), 'narrower: other value')
    check(refined(dict(kind='mem-write', addr='03000010', ndiff=4, only_x86=0, only_arm=4, x86=w(0, 0),
                       arm=w(0x12345678, 0))) == 'native-only-store', 'native-only store')
    # call flow
    check(refined(dict(kind='call-count', x86=3, arm=1, x86_trace=['00401100', '00401200', '00401300'],
                       arm_trace=['00401100']), 'call-flow') == 'native-early-exit', 'native stops early')
    check(refined(dict(kind='call-count', x86=1, arm=2, x86_trace=['00401100'],
                       arm_trace=['00401100', '00401200']), 'call-flow') == 'native-extra-calls', 'native calls more')
    check(refined(dict(kind='call-target', index=0, x86='00406d30', arm='name:_ZNK10NiFixedStrcvPKcEv'),
                  'call-flow') == 'native-undefined', 'native calls an unbound C++ name')
    check(refined(dict(kind='call-target', index=0, x86='imp:VirtualFree', arm='00fdf10c'), 'call-flow') ==
          'import-slot-called', 'native jumps to the IAT slot')
    real = causes.native_callees
    try:
        causes.native_callees = lambda src, a: {'__fnv_00401200'}
        check(refined(dict(kind='call-target', index=0, x86='00401100', arm='00401200'), 'call-flow') ==
              'native-inlined', 'oracle callee the native never names: native-inlined')
        check(refined(dict(kind='call-target', index=0, x86='00401200', arm='00401300'), 'call-flow') == 'call-flow',
              'oracle callee the native names: stays call-flow')
        causes._VFT, causes._LINKSYM = set(), {0x00ecd291: 'free'}
        causes.native_callees = lambda src, a: {'free'}
        check(refined(dict(kind='call-target', index=0, x86='00ecd291', arm='00401300'), 'call-flow') == 'call-flow',
              'oracle CRT callee the native calls by its link name: stays call-flow')
        causes._VFT = causes._LINKSYM = None
    finally:
        causes.native_callees = real
    check(refined(dict(kind='call-target', index=0, x86='00401100', arm='00401200'), 'call-flow') == 'call-flow',
          'call-target mismatch stays call-flow')
    # native null write after an intercepted allocator (static callee facts, faked here)
    real = causes.native_callees
    try:
        causes.native_callees = lambda src, a: {'__fnv_00aa13e0', '__fnv_00a57640'} if a == 0x00401000 else set()
        check(refined(dict(kind='error', x86=None, arm='fault:w:00000000')) == 'new-null',
              'null store after an allocator call: new-null')
        check(causes.refine(rec(dict(kind='error', x86=None, arm='fault:w:00000000'), addr='00402000'),
                            'unclassified') == 'native-null-read', 'null store without an allocator: native-null-read')
    finally:
        causes.native_callees = real
    check(causes.native_callees('src/none.cpp', 0x00401000) is None, 'no object: no callee facts')
    # effects lines refine triage causes
    causes.EFFECTS.clear()
    causes.EFFECTS.update({
        'e1.json': dict(first='width'),
        'e2.json': dict(first='store', store=dict(word=dict(x86='image address 0102e388',
                                                           arm='native image .data.rel.ro _ZTV9FirstBase+8'))),
        'e3.json': dict(first='store', store=dict(word=dict(x86='image address 01200000', arm='native named stub 0b000010')))})
    try:
        mw = dict(kind='mem-write', addr='03000010', ndiff=4, only_x86=0, only_arm=0, x86=w(5, 0), arm=w(6, 0))
        check(causes.refine(dict(rec(mw), _file='e1.json'), 'unclassified') == 'field-width', 'effects: field-width')
        check(causes.refine(dict(rec(mw), _file='e2.json'), 'unclassified') == 'vptr-unpaired', 'effects: vptr-unpaired')
        check(causes.refine(dict(rec(mw), _file='e3.json'), 'unclassified') == 'native-stub-data',
              'effects: native named stub stored')
        check(causes.refine(dict(rec(mw), _file='e1.json'), 'uninit-arm') == 'uninit-arm',
              'effects never override a non-triage cause')
    finally:
        causes.EFFECTS.clear()
    # a triage record in a function that calls an unbound C++ name
    real = causes.unbound_callees
    try:
        causes.unbound_callees = lambda src, a: ['_ZNK6Point3mlEf']
        check(causes.refine(rec(dict(kind='return', x86=w(1), arm=w(2))), 'unclassified') == 'after-native-undefined',
              'triage record downstream of an unbound name')
        check(causes.refine(rec(dict(kind='return', x86=w(1), arm=w(2))), 'bool-repr') == 'bool-repr',
              'a by-design cause stays')
    finally:
        causes.unbound_callees = real
    # a definite probe cause is never overridden
    check(causes.refine(rec(dict(kind='error', x86=None, arm='exc:1@0'), 'uninit-arm'), 'uninit-arm') == 'uninit-arm',
          'probe cause kept')
    check(causes.refine(rec(dict(kind='return', x86=w(1), arm=w(2)), None), None) == 'unclassified',
          'no hint: unclassified')


def test_policy():
    classes = {v[0] for v in causes.POLICY.values()}
    check(classes <= set(causes.CLASS_ORDER), 'every policy class is known')
    for c in ('bool-repr', 'nan-bits', 'x87-precision', 'division', 'signedness', 'local-static', 'uninit-arm',
              'uninit-both', 'call-flow', 'falloff?', 'vptr-unpaired', 'call-order', 'ilp32-pointer',
              'native-undefined', 'oracle-vcall', 'x86-custom-cc', 'unclassified'):
        check(c in causes.POLICY, 'policy has %s' % c)
    rules = {'native-trap', 'stack-protector', 'native-null-read', 'native-stub-data', 'oracle-trap', 'oracle-fault',
             'no-native-entry', 'native-address', 'native-garbage-arg', 'sret-mismatch', 'literal-ret',
             'store-width', 'x86-only-store', 'native-only-store', 'native-early-exit', 'native-extra-calls'}
    check(rules <= set(causes.POLICY), 'every rule result has a policy row')
    check(causes.policy('no-such-cause')[0] == 'triage', 'unknown cause: triage')
    import report
    check('x87-precision' in report.SOFT_CAUSES and 'division' in report.SOFT_CAUSES, 'by-design causes are soft')
    check('uninit-both' in report.HARNESS_CAUSES and 'literal-ret' in report.HARNESS_CAUSES,
          'artefacts are oracle gaps in report')
    check('uninit-arm' not in report.SOFT_CAUSES | report.HARNESS_CAUSES, 'uninit-arm stays hard')
    check(report.reclassify(rec(dict(kind='error', x86=None, arm='exc:1@0a000000'))) == 'native-trap',
          'report.reclassify applies the record rules')
    check(report.reclassify(rec(dict(kind='return', x86=w(1), arm=w(2)), 'bool-repr')) == 'bool-repr',
          'report.reclassify keeps a probe cause')


def test_summary():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, 'div'))
        rows = [dict(address='00401000', source='src/t.cpp', status='ok'),
                dict(address='00402000', source='src/t.cpp', status='ok'),
                dict(address='00403000', source='src/t.cpp', status='ok'),
                dict(address='00404000', source='src/t.cpp', status='setup:KeyError')]
        open(os.path.join(d, 'results.jsonl'), 'w').write(''.join(json.dumps(r) + '\n' for r in rows))
        recs = {'00401000-0.json': rec(dict(kind='error', x86=None, arm='exc:1@0'), addr='00401000'),
                '00401000-1.json': rec(dict(kind='return', x86=w(1), arm=w(2)), 'bool-repr', addr='00401000'),
                '00402000-0.json': rec(dict(kind='call-args', arg='p1', cls='F32', x86=f32(1.0), arm=f32(1.0000001)),
                                       addr='00402000'),
                '00403000-0.json': rec(dict(kind='return', x86=w(1), arm=w(2)), addr='00403000'),
                '00403000-1.json': rec(dict(kind='return', x86=w(1), arm=w(3)), addr='00403000'),
                '00404000-0.json': rec(dict(kind='return', x86=w(1), arm=w(2)), addr='00404000')}
        for n, r in recs.items():
            json.dump(r, open(os.path.join(d, 'div', n), 'w'))
        open(os.path.join(d, 'recheck_t.jsonl'), 'w').write(
            json.dumps(dict(file='00403000-1.json', status='gone', divergence=None)) + '\n')
        out = os.path.join(d, 'causes.json')
        causes.main(['summary', d, '--recheck-tag', 't', '--out', out])
        s = json.load(open(out))
        check(s['format'] == 'fnvnx-causes/1', 'summary format')
        check(s['functions_ok'] == 3 and s['functions_diverging'] == 3, 'ok functions only (setup row skipped)')
        c = s['causes']
        check(c['native-trap']['verdict'] == 'real-bug' and c['native-trap']['owner'] == 'S1' and
              c['native-trap']['worst_of'] == 1, 'native-trap row: real bug, S1, worst of its function')
        check(c['bool-repr']['worst_of'] == 0 and c['bool-repr']['divergences'] == 1, 'bool-repr not the worst')
        check(c['x87-precision']['verdict'] == 'by-design', 'x87-precision by design')
        check(c['unclassified']['divergences'] == 1, 'a recheck-gone replay is dropped')
        check(s['functions_by_class'] == {'real-bug': 1, 'triage': 1, 'artifact': 0, 'by-design': 1},
              'functions by worst class')
        # merge: one shard as a report block, one as the JSON file
        md = os.path.join(d, 'fz1.md')
        open(md, 'w').write('# fuzz\n\n```json fnvnx-causes\n%s\n```\n' % json.dumps(s))
        mout = os.path.join(d, 'all.json')
        causes.main(['merge', md, out, '--out', mout])
        m = json.load(open(mout))
        check(m['shards'] == 2 and m['functions_ok'] == 6 and m['functions_diverging'] == 6, 'merge adds shards')
        check(m['causes']['native-trap']['divergences'] == 2 and m['functions_by_class']['real-bug'] == 2,
              'merge adds causes and classes')
        nob = os.path.join(d, 'empty.md')
        open(nob, 'w').write('# fuzz, no causes block\n')
        check(causes.merge([nob])['shards'] == 0, 'a report without the block is skipped')


if __name__ == '__main__':
    test_rules()
    test_policy()
    test_summary()
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)
