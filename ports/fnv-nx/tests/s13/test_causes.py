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
          'runtime-equiv', 'native calls the same import, traced at its IAT slot')
    check(refined(dict(kind='call-target', index=0, x86='imp:VirtualFree', arm='00fdf114'), 'call-flow') ==
          'import-slot-called', 'native reaches another import\'s IAT slot')
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
        'e4.json': dict(first='store', store=dict(addr='03000000', x86=dict(v='00', size=4, pc='0044deea'), arm=None,
                                                  word=dict(x86='zero', arm='zero'))),
        'e3.json': dict(first='store', store=dict(word=dict(x86='image address 01200000', arm='native named stub 0b000010')))})
    try:
        mw = dict(kind='mem-write', addr='03000010', ndiff=4, only_x86=0, only_arm=0, x86=w(5, 0), arm=w(6, 0))
        check(causes.refine(dict(rec(mw), _file='e1.json'), 'unclassified') == 'field-width', 'effects: field-width')
        check(causes.refine(dict(rec(mw), _file='e2.json'), 'unclassified') == 'vptr-unpaired', 'effects: vptr-unpaired')
        check(causes.refine(dict(rec(mw), _file='e3.json'), 'unclassified') == 'native-stub-data',
              'effects: native named stub stored')
        # oracle-callee-store needs evidence that the harness intercepted the call (V1 C1-C3): the store's pc is in
        # a callee the native calls out of line through a name its own object does not define
        check(causes.refine(dict(rec(mw), _file='e4.json', _size=0x40), 'unclassified') == 'unclassified',
              'effects: callee store without object evidence stays open')
        saved = (causes.native_callees, causes.function_at, dict(causes._OBJ_CACHE), os.environ.get('FNVNX_CAUSES_OBJ'))
        try:
            os.environ['FNVNX_CAUSES_OBJ'] = '/nonexistent-obj'
            obj = os.path.join('/nonexistent-obj', 'src_t.cpp.o')
            causes.function_at = lambda pc: 0x0044dee0 if 0x0044dee0 <= pc < 0x0044df00 else None
            causes.native_callees = lambda src, a: {'__fnv_0044dee0', 'memset'}
            causes._OBJ_CACHE[obj] = {'_defined': {'__fnv_00401000'}}
            check(causes.refine(dict(rec(mw), _file='e4.json', _size=0x40), 'unclassified') == 'oracle-callee-store',
                  'effects: store made by a callee the native calls out of line (intercepted)')
            for c in causes.NOT_INTERCEPTED:
                check(causes.refine(dict(rec(mw), _file='e4.json', _size=0x40), c) == c,
                      'effects: %s is never a callee-store artefact' % c)
            causes._OBJ_CACHE[obj] = {'_defined': {'__fnv_00401000', '__fnv_0044dee0'}}
            check(causes.refine(dict(rec(mw), _file='e4.json', _size=0x40), 'unclassified') == 'unclassified',
                  'effects: a callee the unit defines runs natively, not intercepted')
            causes._OBJ_CACHE[obj] = {'_defined': {'__fnv_00401000'}}
            causes.native_callees = lambda src, a: {'memset'}
            check(causes.refine(dict(rec(mw), _file='e4.json', _size=0x40), 'unclassified') == 'unclassified',
                  'effects: the native never calls that callee: stays open')
        finally:
            causes.native_callees, causes.function_at = saved[0], saved[1]
            causes._OBJ_CACHE.clear(); causes._OBJ_CACHE.update(saved[2])
            if saved[3] is None:
                os.environ.pop('FNVNX_CAUSES_OBJ', None)
            else:
                os.environ['FNVNX_CAUSES_OBJ'] = saved[3]
        check(causes.refine(dict(rec(mw, addr='0044dee0'), _file='e4.json', _size=0x40), 'unclassified') ==
              'unclassified', 'effects: store inside the function stays open')
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
    g = rec(dict(kind='call-count', x86=0, arm=1, x86_trace=[], arm_trace=['00aa1460']), 'call-flow')
    g['symbol'] = '??_GFoo@@UAEPAXI@Z'
    g['case'] = {'args': {'p0': '00000000'}}
    check(report.reclassify(g) == 'deleting-dtor-flags', '??_G with flags 0: the native D0 frees, the oracle does not')
    g['case'] = {'args': {'p0': '01000000'}}
    check(report.reclassify(g) == 'falloff?', '??_G with flags 1 that frees only natively stays falloff?')
    g = rec(dict(kind='call-count', x86=1, arm=3, x86_trace=['00401234'], arm_trace=['00401234', '00401030',
                                                                                       '00555555']), 'call-flow')
    g['symbol'] = '??_GFoo@@UAEPAXI@Z'
    check(report.reclassify(g) != 'deleting-dtor-flags', '??_G: an extra call that is not a deallocator is not dtor-flags')
    n = rec(dict(kind='call-count', x86=0, arm=2, x86_trace=[], arm_trace=['name:_ZNK3FooIcEcvPKcEv', '0043b4a0']),
            'call-flow')
    check(report.reclassify(n) == 'native-undefined', 'oracle no calls, native first calls a named stub')
    n = rec(dict(kind='call-count', x86=0, arm=2, x86_trace=[], arm_trace=['00559450', 'name:_ZNK3FooIcEcvPKcEv']),
            'call-flow')
    check(report.reclassify(n) == 'after-native-undefined', 'oracle no calls, a later native call is a named stub')
    n = rec(dict(kind='call-count', x86=0, arm=1, x86_trace=[], arm_trace=['00559450']), 'call-flow')
    check(report.reclassify(n) == 'falloff?', 'oracle no calls, native calls an address: falloff?')
    a = rec(dict(kind='call-args', arg='p0', cls='F32', x86=f32(0.88138), arm=w(struct.unpack('<I', struct.pack(
        '<f', 0.88138))[0] + 1)), 'signedness')
    check(report.reclassify(a) == 'x87-precision', 'signedness probe on a 1-ulp float argument: x87-precision')
    b = rec(dict(kind='return', x86='01', arm='80'), 'signedness')
    b['symbol'] = '?f@@YA_NXZ'
    check(report.reclassify(b) == 'bool-repr', 'signedness probe on a bool result byte 0x80: bool-repr')
    b['symbol'] = '?f@@YAEXZ'
    check(report.reclassify(b) == 'signedness', 'an unsigned char result 01 vs 80 stays signedness')
    e = rec(dict(kind='error', x86=None, arm='fault:r:fffffffe'), 'signedness')
    check(report.reclassify(e) == 'ilp32-pointer', 'signedness probe on a native access across 4 GiB')
    c = rec(dict(kind='call-target', index=1, x86='00aa3e40', arm='name:_ZN3Foo3BarEv'), 'signedness')
    check(report.reclassify(c) == 'native-undefined', 'signedness probe on a call of a named stub: native-undefined')
    e = rec(dict(kind='error', x86=None, arm='fault:r:00000010'), 'signedness')
    check(report.reclassify(e) == 'signedness', 'a native near-null read keeps the probe cause')


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


def test_object_reader():
    """Without pyelftools the object rules are off: the reader says so instead of passing silently."""
    import io
    saved = {k: v for k, v in sys.modules.items() if k == 'elftools' or k.startswith('elftools.')}
    stats, cache, err = dict(causes.OBJ_STATS), dict(causes._OBJ_CACHE), sys.stderr
    try:
        for k in saved:
            del sys.modules[k]
        sys.modules['elftools'] = None              # import elftools.* raises ImportError
        causes.OBJ_STATS.update(reader=None, scanned=0, missing=0)
        causes._OBJ_CACHE.clear()
        sys.stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, 'src_t.cpp.o'), 'wb').write(b'\x7fELF')
            open(os.path.join(d, 'src_u.cpp.o'), 'wb').write(b'\x7fELF')
            os.environ['FNVNX_CAUSES_OBJ'] = d
            check(causes.native_callees('src/t.cpp', 0x00401000) is None, 'no reader: no callees')
            causes.native_callees('src/u.cpp', 0x00401000)
            causes.native_callees('src/missing.cpp', 0x00401000)
            check(causes.OBJ_STATS['reader'] == 'unavailable' and causes.OBJ_STATS['scanned'] == 0 and
                  causes.OBJ_STATS['missing'] == 1, 'reader state: unavailable, one object missing')
            check('pyelftools is not installed' in sys.stderr.getvalue(), 'missing reader warns on stderr')
            check(sys.stderr.getvalue().count('is not installed') == 1, 'it warns once')
    finally:
        sys.stderr = err
        os.environ.pop('FNVNX_CAUSES_OBJ', None)
        del sys.modules['elftools']
        sys.modules.update(saved)
        causes.OBJ_STATS.clear(); causes.OBJ_STATS.update(stats)
        causes._OBJ_CACHE.clear(); causes._OBJ_CACHE.update(cache)


def test_handoffs():
    """Per-owner lists: real bugs, triage and owned artefacts get a row each, by-design causes none."""
    with tempfile.TemporaryDirectory() as root:
        d = os.path.join(root, '7of26')
        os.makedirs(os.path.join(d, 'div'))
        rows = [dict(address=a, source='src/t.cpp', status='ok') for a in ('00401000', '00402000', '00403000')]
        rows.append(dict(address='00404000', source='src/t.cpp', status='setup:KeyError'))
        open(os.path.join(d, 'results.jsonl'), 'w').write(''.join(json.dumps(r) + '\n' for r in rows))
        recs = {'00401000-0.json': rec(dict(kind='error', x86=None, arm='exc:1@0'), addr='00401000'),
                '00401000-1.json': rec(dict(kind='return', x86=w(1), arm=w(2)), 'bool-repr', addr='00401000'),
                '00402000-0.json': rec(dict(kind='return', x86=w(1), arm=w(2)), addr='00402000'),
                '00403000-0.json': rec(dict(kind='return', x86=w(1), arm=w(2)), 'local-static', addr='00403000'),
                '00404000-0.json': rec(dict(kind='error', x86=None, arm='exc:1@0'), addr='00404000')}
        for n, r in recs.items():
            json.dump(r, open(os.path.join(d, 'div', n), 'w'))
        eff = os.path.join(root, 'effects.jsonl')
        open(eff, 'w').write(json.dumps(dict(file='/x/00402000-0.json', first='value')) + '\n')
        out = os.path.join(root, 'ho')
        saved = dict(causes.EFFECTS)
        try:
            causes.main(['handoffs', d, '--out-dir', out, '--effects', eff])
        finally:
            causes.EFFECTS.clear(); causes.EFFECTS.update(saved)
        files = sorted(os.listdir(out))
        check(files == ['handoffs_S1.tsv', 'harness_S13.tsv', 'index.tsv', 'triage_S13b.tsv'],
              'one file per class and owner, none for by-design (%s)' % files)
        s1 = [l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'handoffs_S1.tsv'))]
        check(s1[0] == list(causes.HANDOFF_COLUMNS) and len(s1) == 2, 'S1 list: header and one row')
        check(s1[1][:5] == ['S1', 'native-trap', '00401000', 'src/t.cpp', 'fuzz/cache/7of26:div/00401000-0.json'],
              'row: owner, cause, address, unit, reproducer (setup function skipped)')
        tr = [l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'triage_S13b.tsv'))]
        check(tr[1][1] == 'unclassified' and tr[1][5] == 'value', 'triage row carries the first effect')
        ix = [l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'index.tsv'))]
        check(['harness_S13.tsv', 'artifact', 'S13', '1', '1', 'local-static 1'] in ix, 'index row per file')


def test_cli_effects():
    """Run as a script, `summary --effects` reaches the rules report.reclassify calls (one module, not two)."""
    import subprocess
    with tempfile.TemporaryDirectory() as root:
        d = os.path.join(root, '1of1')
        os.makedirs(os.path.join(d, 'div'))
        open(os.path.join(d, 'results.jsonl'), 'w').write(
            json.dumps(dict(address='00401000', source='src/t.cpp', status='ok', size=16)) + '\n')
        json.dump(rec(dict(kind='mem-write', addr='03000010', x86='01000000', arm='02000000')),
                  open(os.path.join(d, 'div', '00401000-0.json'), 'w'))
        eff = os.path.join(root, 'eff.jsonl')
        open(eff, 'w').write(json.dumps(dict(file='x/00401000-0.json', first='width')) + '\n')
        out = os.path.join(root, 'c.json')
        tool = os.path.join(HERE, '..', '..', 'tools', 'difffuzz', 'causes.py')
        env = dict(os.environ, FNVNX_CAUSES_OBJ=root)
        subprocess.run([sys.executable, tool, 'summary', d, '--effects', eff, '--out', out], check=True,
                       stdout=subprocess.DEVNULL, env=env)
        c = json.load(open(out))['causes']
        check('field-width' in c and 'unclassified' not in c, 'CLI summary applies --effects (%s)' % sorted(c))


def test_vcall_split():
    """oracle-vcall (the runner's vcall='unplanted' tag, classify's fault probe) and oracle-fault split by the record."""
    def vc(x, a, vptrs=None, trace=None):
        r = rec(dict(kind='call-target', index=0, x86=x, arm=a, x86_trace=[x], arm_trace=trace or [a],
                     vcall='unplanted'), 'oracle-vcall')
        if vptrs:
            r['case'] = dict(vptrs=vptrs)
        return causes.refine(r, 'oracle-vcall')

    check(vc('c3a10b55', '5e0f7a21') == 'vcall-garbage', 'both call garbage words: vcall-garbage')
    check(vc('03000a10', 'd1e2f3a4') == 'vcall-garbage', 'a buffer address and a wild word: vcall-garbage')
    check(vc('c3a10b55', '00456780') == 'oracle-vcall', 'native calls an exe text address: kept')
    check(vc('08c41233', '00c41233') == 'vcall-stub-alias', 'garbage word in the code-stub range: stub alias')
    check(vc('093a5b17', 'name:93a5b17') == 'vcall-stub-alias', 'garbage word in the named-stub range: stub alias')
    check(vc('093a5b17', 'name:93a5b18') == 'oracle-vcall', 'another named stub is not an alias')
    check(vc('c3a10b55', 'name:__s13_vtpad_01012340_7', {'03000000': '01012340'}) == 'vptr-unpaired',
          'a call through a vtable padding slot: vptr-unpaired')
    check(vc('c3a10b55', '00456780', trace=['name:__s13_vtpad_01012340_7', '00456780']) == 'vptr-unpaired',
          'a padding slot earlier in the native trace: vptr-unpaired')
    check(vc('c3a10b55', 'name:_ZN3Foo3BarEv') == 'native-undefined', 'an unbound C++ name, no vptr: undefined')
    check(vc('c3a10b55', 'name:_ZN3Foo3BarEv', {'03000000': '01012340'}) == 'oracle-vcall',
          'a C++ name reached with a planted vptr (a vtable slot) is kept')

    def err(x, cause='oracle-vcall', addr='00401000'):
        return causes.refine(rec(dict(kind='error', x86=x, arm=None), cause, addr), cause)

    check(err('fault:r:0e0a1b2c') == 'oracle-private-address', 'oracle reads its own private range')
    check(err('fault:x:0e0a1b2c') == 'oracle-private-address', 'oracle executes its own private range')
    check(err('fault:w:0e0a1b20', 'unclassified') == 'oracle-private-address', 'oracle-fault in the private range')
    check(err('fault:r:fffffffe', 'unclassified') == 'ilp32-pointer', 'dword read across 4 GiB: ilp32-pointer')
    check(err('fault:r:00000010', 'unclassified') == 'oracle-fault', 'near-null oracle read stays oracle-fault')
    check(err('fault:x:00000000') == 'oracle-vcall', 'oracle executes at null: kept')
    check(causes.refine(rec(dict(kind='error', x86='fault:r:0e0a1b2c', arm='fault:r:00000004'), 'oracle-vcall'),
                        'oracle-vcall') == 'oracle-vcall', 'both machines fault: no oracle-only rule')
    import report
    r = rec(dict(kind='call-target', index=0, x86='c3a10b55', arm='5e0f7a21', vcall='unplanted'), 'oracle-vcall')
    check(report.reclassify(r) == 'vcall-garbage', 'report.reclassify hands oracle-vcall to the split')
    check(report.reclassify(rec(dict(kind='return', x86=w(1), arm=w(2)), 'uninit-both')) == 'uninit-both',
          'report.reclassify still keeps other probe causes')

    # objects: an empty local callee (a bare ret) and a C++ name the object defines
    saved_cache, saved_env = dict(causes._OBJ_CACHE), os.environ.get('FNVNX_CAUSES_OBJ')
    saved_map = (causes._VFT, causes._LINKSYM, set(causes._BOUND))
    try:
        with tempfile.TemporaryDirectory() as d:
            os.environ['FNVNX_CAUSES_OBJ'] = d
            path = os.path.join(d, 'src_t.cpp.o')
            causes._OBJ_CACHE[path] = {0x00401000: {'_ZN4Stub3RunEv', '__fnv_00402000'},
                                       0x00403000: {'__fnv_00402000', '_ZN3Foo3BarEv'},
                                       '_defined': {'_ZN4Stub3RunEv', '_ZN3Foo3BarEv', '__fnv_00401000'},
                                       '_empty': {'_ZN4Stub3RunEv'}}
            causes._VFT, causes._LINKSYM = set(), {0x00402000: '__fnv_00402000'}
            check(causes.empty_callees('src/t.cpp', 0x00401000) == ['_ZN4Stub3RunEv'], 'empty callee found')
            check(err('fault:r:00000010', 'unclassified') == 'native-empty-callee',
                  'oracle-fault where the native calls an empty stub: native-empty-callee')
            check(err('fault:x:00000000') == 'native-empty-callee', 'oracle-vcall fault likewise')
            check(err('fault:r:00000010', 'unclassified', '00403000') == 'oracle-fault', 'no empty callee: kept')
            r = rec(dict(kind='call-target', index=0, x86='c3a10b55', arm='name:_ZN3Foo3BarEv', vcall='unplanted'),
                    'oracle-vcall', '00403000')
            check(causes.refine(r, 'oracle-vcall') == 'oracle-vcall', 'a name the object defines is not undefined')
            r['divergence']['arm'] = 'name:_ZN3Foo3BazEv'
            check(causes.refine(r, 'oracle-vcall') == 'native-undefined', 'a name nothing defines or binds')
    finally:
        causes._OBJ_CACHE.clear(); causes._OBJ_CACHE.update(saved_cache)
        causes._VFT, causes._LINKSYM = saved_map[0], saved_map[1]
        causes._BOUND.clear(); causes._BOUND.update(saved_map[2])
        if saved_env is None:
            os.environ.pop('FNVNX_CAUSES_OBJ', None)
        else:
            os.environ['FNVNX_CAUSES_OBJ'] = saved_env
    for c in ('vcall-garbage', 'vcall-stub-alias', 'oracle-private-address', 'native-empty-callee'):
        check(c in causes.POLICY, 'policy has %s' % c)
    check(causes.policy('native-empty-callee')[:2] == ('real-bug', 'S1') and
          causes.policy('vcall-garbage')[:2] == ('artifact', 'S13'), 'vcall split classes')


def test_unclassified_shapes():
    """The bucket rules for records no other rule took: canned results, thunks, wild calls, garbage, stubs."""
    def r(div, case, cause='unclassified'):
        x = rec(div, cause)
        x['case'] = case
        return causes.refine(x, cause)
    canned = '5e1f2a77' + '00' * 12            # call 1 returns 0x772a1f5e (made up)
    case = dict(seed=12345, bg='zero', rets=['00' * 16, canned])
    blk = causes._alloc_result(0)              # the block call 0 (an allocator) returned
    check(r(dict(kind='call-args', arg='p0', cls='P', x86=w(0x772a1f5e), arm=w(blk), index=2), case) == 'ctor-this',
          'oracle passes the constructor\'s canned result, native the allocator block: ctor-this')
    check(r(dict(kind='return', x86=w(blk), arm=w(0x772a1f5e)), case) == 'ctor-this',
          'returns the allocator block vs the constructor\'s canned result: ctor-this')
    check(r(dict(kind='mem-write', addr='03000010', ndiff=4, x86=w(0x772a1f5e, 0), arm=w(blk, 0), only_x86=0,
                 only_arm=0), case) == 'ctor-this', 'stores the constructor result vs the block: ctor-this')
    check(r(dict(kind='call-args', arg='p0', cls='P', x86=w(0x772a1f5e), arm=w(blk), index=1), case) ==
          'unclassified', 'the canned result of a later call does not count')
    check(r(dict(kind='call-args', arg='this', cls='P', x86=w(0x772a1f5e), arm=w(0x06effff0), index=2), case) ==
          'canned-this', 'oracle uses an earlier canned eax as this, native its frame temporary: canned-this')
    check(r(dict(kind='call-args', arg='this', cls='P', x86=w(0x772a1f5f), arm=w(0x06effff0), index=2), case) ==
          'unclassified', 'a pointer that is no canned result stays open')
    check(r(dict(kind='call-args', arg='p1', cls='F32', x86=f32(0.5), arm=w(0x772a1f5e), index=2), case) ==
          'native-stale-result', 'native passes the previous call\'s result register: native-stale-result')
    check(r(dict(kind='call-args', arg='p1', cls='F32', x86=f32(0.5), arm=w(0x772a1f5e), index=3), case) ==
          'unclassified', 'a result of an older call is not the stale register')
    # wild jumps into the AArch64 private range on the random background
    check(r(dict(kind='error', x86=None, arm='fault:x:0a123457'), dict(seed=1, bg='rand')) ==
          'native-wild-private', 'random background: native jumps into its private range: native-wild-private')
    check(r(dict(kind='error', x86=None, arm='fault:x:0a123457'), dict(seed=1, bg='zero')) == 'unclassified',
          'the same on the zero background stays open')
    # a stored word with top byte 0x08 normalised as a code stub pointer
    check(r(dict(kind='mem-write', addr='03000013', ndiff=1, x86='11223308aabbccdd', arm='11223300aabbccdd',
                 only_x86=0, only_arm=0), case) == 'stub-normalized', 'only a 0x08 byte cleared: stub-normalized')
    check(r(dict(kind='mem-write', addr='03000013', ndiff=1, x86='11223309aabbccdd', arm='11223300aabbccdd',
                 only_x86=0, only_arm=0), case) == 'unclassified', 'another byte value stays open')
    # planted garbage stored / returned
    gx = sorted(causes._garbage_words(777, 'x86'))[5]
    ga = sorted(causes._garbage_words(777, 'a64'))[5]
    gcase = dict(seed=777, bg='zero')
    check(r(dict(kind='mem-write', addr='03000020', ndiff=4, x86=w(gx, 0), arm=w(ga, 0), only_x86=0, only_arm=0),
            gcase) == 'uninit-both', 'both store their own garbage: uninit-both')
    check(r(dict(kind='return', x86=w(0x1234), arm=w(ga)), gcase) == 'uninit-arm', 'native returns garbage: uninit-arm')
    check(r(dict(kind='return', x86=w(gx), arm=w(0x1234)), gcase) == 'uninit-x86', 'oracle returns garbage: uninit-x86')
    # native object facts (stubbed): this-adjusting thunks and placeholder bodies
    facts, callees = causes._object_facts, causes.native_callees
    try:
        causes._object_facts = lambda src: ({'__fnv_00401000': {0x38}}, {'_ZN1T4zeroEv'})
        causes.native_callees = lambda src, a: {'_ZN1T4zeroEv'}
        check(r(dict(kind='call-args', arg='this', cls='P', x86=w(0x02ffffc8), arm=w(0x03000000), index=0), case) ==
              'thunk-binding', 'this - 0x38 where the native has a _ZThn56_ thunk: thunk-binding')
        check(r(dict(kind='call-args', arg='this', cls='P', x86=w(0x02fffff0), arm=w(0x03000000), index=0), case) ==
              'layout-offset', 'this - 0x10 without such a thunk: layout-offset')
        check(r(dict(kind='return', x86=w(5), arm=w(0)), case) == 'native-zero-stub',
              'native returns 0 and calls a `mov w0, #0; ret` body: native-zero-stub')
        causes._object_facts = lambda src: ({}, set())
        check(r(dict(kind='return', x86=w(5), arm=w(0)), case) == 'unclassified', 'no placeholder body: open')
    finally:
        causes._object_facts, causes.native_callees = facts, callees
    # a probe's cause is never overridden, and only what would stay unclassified is taken
    check(r(dict(kind='call-args', arg='p0', cls='P', x86=w(0x772a1f5e), arm=w(blk), index=2), case, 'call-flow') ==
          'call-flow', 'the bucket rules leave call-flow alone')
    for c in ('ctor-this', 'canned-this', 'native-wild-private', 'stub-normalized', 'thunk-binding',
              'native-stale-result', 'native-zero-stub'):
        check(c in causes.POLICY, 'policy row for %s' % c)


def test_callflow():
    """Call-flow sub-shapes (_rule_callflow) with the native object facts faked."""
    RET = bytes.fromhex('c0035fd6')                    # ret
    MOV0_RET = bytes.fromhex('00008052') + RET           # mov w0, #0; ret
    BL_RET = bytes.fromhex('00000094') + RET             # bl ...; ret

    def cf(div, symbol='?f@@YAXXZ', conv='this:P/4 -> void', args=None, cause='call-flow'):
        r = dict(source='src/t.cpp', address='00401000', symbol=symbol, conv=conv, divergence=div,
                 hint=dict(cause=cause), case=dict(args=args or {}))
        return causes.refine(r, cause)

    def early(tx, ta):
        return dict(kind='call-count', x86=len(tx), arm=len(ta), x86_trace=tx, arm_trace=ta)

    names = ('native_body', 'native_defines', 'native_callees', 'native_reaches', 'crt_entries', 'unbound_callees')
    saved = {n: getattr(causes, n) for n in names}
    bodies = {}
    try:
        causes.native_body = lambda src, n: bodies.get(n)
        causes.native_defines = lambda src, n: n in bodies
        causes.native_callees = lambda src, a: {'__fnv_00401100'}
        causes.native_reaches = lambda src, n: {'__fnv_00401100'}
        causes.crt_entries = lambda: {0x00ec9650}
        causes.unbound_callees = lambda src, a: []
        causes._VFT, causes._LINKSYM = set(), {0x00ec623a: 'sprintf', 0x00ecd291: 'free',
                                               0x00402000: '__fnv_00402000'}
        # the function itself is an empty body
        bodies['__fnv_00401000'] = (MOV0_RET, {'__fnv_00401000'})
        check(cf(early(['00401100'], [])) == 'native-empty-body', 'empty native body, oracle calls: native-empty-body')
        bodies['__fnv_00401000'] = (BL_RET, {'__fnv_00401000'})
        check(cf(early(['00401100'], [])) == 'native-early-exit', 'a body with a call: stays native-early-exit')
        # ... or a decomp-local empty stand-in for an original defined elsewhere
        bodies['_ZN3Foo12FUN_00402000Ev'] = (RET, {'_ZN3Foo12FUN_00402000Ev'})
        causes.native_callees = lambda src, a: {'_ZN3Foo12FUN_00402000Ev'}
        check(cf(early(['00ecd291'], [])) == 'native-empty-body', 'empty local stand-in: native-empty-body')
        bodies['_ZN3Foo12FUN_00402000Ev'] = (RET, {'_ZN3Foo12FUN_00402000Ev', '__fnv_00402000'})
        check(cf(early(['00401100'], [])) == 'native-early-exit', 'the real (empty) definition: no stand-in')
        del bodies['_ZN3Foo12FUN_00402000Ev']
        causes.native_callees = lambda src, a: {'__fnv_00401100'}
        # dynamic initializer without the exit registration
        check(cf(early(['00401100', '00ec658f'], ['00401100']), symbol='??__Eg_x@@YAXXZ') == 'dyninit-no-atexit',
              'oracle registers a destructor, native initializer does not: dyninit-no-atexit')
        causes.native_callees = lambda src, a: {'__fnv_00401100', 'fnvnx_xc_atexit'}
        check(cf(early(['00401100', '00ec658f'], ['00401100']), symbol='??__Eg_x@@YAXXZ') == 'native-early-exit',
              'native initializer registers it: stays native-early-exit')
        causes.native_callees = lambda src, a: {'__fnv_00401100'}
        # deleting destructor called with flags 0
        dd = dict(kind='call-count', x86=1, arm=2, x86_trace=['00401100'], arm_trace=['00401100', '00aa1460'])
        check(cf(dd, symbol='??_GFoo@@UAEPAXI@Z', args={'p0': '00000000'}) == 'deleting-dtor-flags',
              'D0 frees where ??_G got flags 0: deleting-dtor-flags')
        check(cf(dd, symbol='??_GFoo@@UAEPAXI@Z', args={'p0': '01000000'}) == 'native-extra-calls',
              'flags bit 0 set: stays native-extra-calls')
        # a by-value class argument freed by the MSVC callee
        check(cf(early(['00401100', '00ecd291'], ['00401100']), conv='this:P/4, p0:AGG:Str/8 -> void') ==
              'byvalue-arg-dtor', 'oracle frees a by-value argument: byvalue-arg-dtor')
        # the native skips a free
        ct = dict(kind='call-target', index=0, x86='00ecd291', arm='00401300', x86_next='00401300')
        causes.native_reaches = lambda src, n: {'__fnv_00401300'}
        check(cf(ct) == 'native-missing-free', 'oracle frees, native reaches no deallocator: native-missing-free')
        causes.native_reaches = lambda src, n: {'__fnv_00401300', '__fnv_00401030'}
        check(cf(ct) != 'native-missing-free', 'native reaches operator delete: not native-missing-free')
        causes.native_reaches = lambda src, n: {'__fnv_00401100'}
        # call-target shapes
        tgt = lambda x, a: dict(kind='call-target', index=0, x86=x, arm=a)  # noqa: E731
        check(cf(tgt('00401200', '01268238')) == 'native-calls-data', 'native jumps to a data address')
        check(cf(tgt('00ec9650', 'name:asinf')) == 'libc-variant', 'x87 intrinsic vs libm float: libc-variant')
        check(cf(tgt('00ec623a', 'name:__sprintf_chk')) == 'libc-variant', 'sprintf vs __sprintf_chk')
        check(cf(tgt('00ec623a', 'name:__strcpy_chk')) != 'libc-variant', 'sprintf vs __strcpy_chk: not a variant')
        check(cf(tgt('00401200', '9e684216')) == 'native-vcall-oracle-direct', 'native wild call, oracle direct')
        bodies['__fnv_00401100'] = (BL_RET, {'__fnv_00401100'})
        check(cf(tgt('00401100', '00401300')) == 'oracle-callee-unloaded',
              'oracle intercepts a callee the native object runs: oracle-callee-unloaded')
        causes.unbound_callees = lambda src, a: ['_ZN3Bar3getEv']
        check(cf(tgt('00401100', '00401300')) == 'after-native-undefined',
              'an artifact shape never hides an unbound C++ name')
    finally:
        for n, f in saved.items():
            setattr(causes, n, f)
        causes._VFT = causes._LINKSYM = None


if __name__ == '__main__':
    test_rules()
    test_vcall_split()
    test_callflow()
    test_policy()
    test_summary()
    test_object_reader()
    test_handoffs()
    test_cli_effects()
    test_unclassified_shapes()
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)
