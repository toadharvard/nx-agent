#!/usr/bin/env python3
"""R4 test: MSVC vftable getters (Havok class registry): exevft.vtable_getter, and fnvlink's aliasing of the
getter's stand-in vtable symbol to the paired native vtable (aliases.ld, vtget.tsv).

    python3 tools/link/test_vtget.py     # (c) needs aarch64-linux-gnu-{g++,ld,nm}

(a) exevft._getter_in on hand-written x86 bytes: the getter shape returns V; a variant that reads a non-vftable word,
    a branch, a changed push count and a bare `ret` give None.
(b) fnvlink.vtget_pick, the pure alias choice (one stand-in, one address point, not paired to another vftable).
(c) end to end on a synthetic exe: class C (native vtable _ZTV1C, the exe's vftable V) and a getter at 0x4011c0
    whose native version builds a stand-in Z and returns its vptr. After resolve + apply, aliases.ld has
    `_ZTV1Z = _ZTV1C + 0;`, vtget.tsv lists the getter as aliased, and the trial link puts _ZTV1Z where _ZTV1C is
    (so the getter's `_ZTV1Z+8` is C's address point); with FNVNX_VTGET=0 the two stay apart.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exevft       # noqa: E402
import layout       # noqa: E402
import test_exevft  # noqa: E402
import fnvlink      # noqa: E402

FNVLINK = os.path.join(HERE, 'fnvlink.py')
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
FAILS = []
V = 0x00fdf080
BASE = 0x004011c0


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)
    return cond


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise SystemExit('FAILED: %s\n%s%s' % (' '.join(cmd), r.stdout, r.stderr))
    return r.stdout


def getter(v, call_to=0x401300, base=BASE, load=b'\x8b\x04\x24', pushes=b'\x33\xc0\x50', mid=b''):
    """push ebp; mov ebp, esp; and esp, -16; sub esp, 0x70; xor eax, eax; push eax; lea ecx, [esp+0x68];
    mov dword ptr [esp+4], v; call ctor; mov eax, [esp]; mov esp, ebp; pop ebp; ret"""
    b = b'\x55\x89\xe5\x83\xe4\xf0\x83\xec\x70' + pushes + b'\x8d\x4c\x24\x68\xc7\x44\x24\x04' + \
        struct.pack('<I', v) + mid
    b += b'\xe8' + struct.pack('<i', call_to - (base + len(b) + 5))
    return b + load + b'\x89\xec\x5d\xc3'


def unit_a():
    vf = {V, 0x00fdf0c0}
    check(exevft._getter_in(getter(V), BASE, vf) == V, 'getter shape returns V')
    check(exevft._getter_in(getter(0x00fdf0c0), BASE, vf) == 0x00fdf0c0, 'another vftable of the set')
    check(exevft._getter_in(getter(0x12345678), BASE, vf) is None, 'a stored word that is no vftable gives None')
    check(exevft._getter_in(getter(V, load=b'\x8b\x44\x24\x08'), BASE, vf) is None,
          'mov eax, [esp+8] (a word that was never stored) gives None')
    check(exevft._getter_in(getter(V, mid=b'\x74\x00'), BASE, vf) is None, 'a branch gives up')
    check(exevft._getter_in(getter(V, pushes=b'\x33\xc0\x50\x50'), BASE, vf) is None,
          'two words pushed (the callee pops both): the load reads another word')
    check(exevft._getter_in(b'\xc3', BASE, vf) is None, 'a bare ret gives None')
    check(exevft._getter_in(b'\xb8' + struct.pack('<I', V) + b'\xc3', BASE, vf) == V, 'mov eax, V; ret')
    check(exevft._getter_in(b'\x90' * 60 + b'\xc3', BASE, vf) is None, 'more than 40 instructions gives up')
    # a saved register pushed before the arguments (0x00dfd800's shape): the callee's own `ret 4` says what it pops
    call = lambda at: b'\xe8' + struct.pack('<i', 0x1000 - (at + 5))   # noqa: E731
    pre = b'\x55\x89\xe5\x83\xe4\xf0\x83\xec\x5c\x56\x31\xf6\x56\x8d\x4c\x24\x28' + \
        b'\xc7\x44\x24\x14' + struct.pack('<I', V)
    code = pre + call(len(pre))
    code += b'\x56\x8d\x4c\x24\x54'
    code += call(len(code))
    code += b'\x8b\x44\x24\x10\x5e\x89\xec\x5d\xc3'
    pops4 = lambda t: (4, False)   # noqa: E731
    check(exevft._getter_in(code, 0, vf, pops4) == V, 'saved register + two ctor calls popping 4: V')
    check(exevft._getter_in(code, 0, vf) is None, 'without the callee info the saved register counts as an argument')
    # the CRT cookie check after the result (0x00d2c8a0): it keeps eax
    code = b'\xb8' + struct.pack('<I', V)
    code += call(len(code))
    code += b'\xc3'
    check(exevft._getter_in(code, 0, vf, lambda t: (0, True)) == V, '__security_check_cookie keeps eax')
    check(exevft._getter_in(code, 0, vf, lambda t: (0, False)) is None, 'any other call clobbers eax')


def unit_b():
    aps = {'_ZTV1Z': [(8, 0)], '_ZTV1M': [(8, 0), (24, -8)], '_ZTV1Q': [(16, 0)]}

    def pick(refs, users=None):
        return fnvlink.vtget_pick(V, ('_ZTV1C', 8), list(refs), aps.get, users or {})
    check(pick({'_ZTV1Z'}) == ('alias', '_ZTV1Z', 0), 'one stand-in at the same point: delta 0')
    check(pick({'_ZTV1Q'}) == ('alias', '_ZTV1Q', -8), 'another address point: delta apN - apZ')
    check(pick({'_ZTV1C'})[0] == 'already the paired vtable', 'the paired vtable itself')
    check(pick(['_ZTV1Q', '_ZTV1Z']) == ('alias', '_ZTV1Z', 0), 'a base then the derived stand-in: the last one (the returned vptr)')
    check(pick(['_ZTV1Z', '_ZTV1C'])[0] == 'already the paired vtable', 'the derived one is the paired vtable: nothing')
    check(pick(['_ZTV1Z', '_ZTV1Q'], {'_ZTV1Q': {0x1234}}) == ('alias', '_ZTV1Z', 0),
          'a second local of a real class (paired elsewhere) after the stand-in: the stand-in')
    check(pick(set())[0] == 'native references no vtable', 'no vtable')
    check(pick({'_ZTV1M'})[0] == 'stand-in has 2 address points', 'a stand-in with two address points')
    check(pick({'_ZTV1X'})[0] == 'stand-in has no words', 'a stand-in with no words')
    check(pick({'_ZTV1Z'}, {'_ZTV1Z': {0x1234}})[0] == 'stand-in is paired to another vftable', 'paired elsewhere')
    check(pick({'_ZTV1Z'}, {'_ZTV1Z': {V}})[0] == 'alias', 'paired only to V is fine')
    check(pick(['_ZTV1Z', '_ZTVN10__cxxabiv117__class_type_infoE'])[0] == 'alias', 'cxxabi vtables are ignored')


HEADER = r'''#pragma once
struct C { virtual int f(); int x; };
struct Z { __attribute__((always_inline)) Z() {} virtual int g() { return 2; } int y; };   // inline only: a weak vtable
void *getter();
'''
SRC = '#include "h.h"\nint C::f() { return 1; }\nC *mk_c() { return new C; }\n' \
      'void *getter() { Z z; return *(void **)&z; }\n'


def nm(elf):
    out = subprocess.run(['aarch64-linux-gnu-nm', elf], capture_output=True, text=True).stdout
    return {l.split()[-1]: int(l.split()[0], 16) for l in out.splitlines() if len(l.split()) == 3}


def e2e():
    with tempfile.TemporaryDirectory() as t:
        t = os.environ.get('FNVNX_TEST_KEEP') or t
        os.makedirs(t, exist_ok=True)
        where = {}

        def build(rd):
            td = rd.put(struct.pack('<II', 0x0101d000, 0) + b'.?AVC@@\0')
            bs = rd.words(td, 0, 0, 0xffffffff, 0, 0x40, 0)
            chd = rd.words(0, 0, 1, rd.words(bs))
            col = rd.words(0, 0, 0, td, chd)
            rd.words(col)
            where['C'] = rd.here()
            rd.words(0x401010)
        exe = os.path.join(t, 'Synthetic.exe')
        test_exevft.make_pe(exe, build, {})
        v = where['C']
        test_exevft.make_pe(exe, build, {BASE: getter(v)})
        check(exevft.vtable_getter(exe, BASE, {v}) == v, 'vtable_getter reads the getter from the PE')
        tree = os.path.join(t, 'tree')
        os.makedirs(os.path.join(tree, 'include'), exist_ok=True)
        open(os.path.join(tree, 'include', 'h.h'), 'w').write(HEADER)
        fl = [f if f != '-O2' else '-O1' for f in layout.flags(tree)] + ['-fno-inline']
        p = os.path.join(tree, 'src', 'a.cpp')
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'w').write(SRC)
        o = os.path.join(t, 'a.o')
        run([CXX, *fl, p, '-o', o], cwd=tree)
        F = lambda a, s: {'address': '0x%08x' % a, 'symbol': s, 'size': 8}   # noqa: E731
        units = {'units': [{'source': 'src/a.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
            F(0x401010, '?f@C@@UAEHXZ'), F(BASE, '?getter@@YAPAXXZ')],
            'symbols': {'??_7C@@6B@': '0x%08x' % v}}]}
        json.dump(units, open(os.path.join(t, 'units.json'), 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            f.write('src/a.cpp\t%s\tok\n' % o)
        res = {}
        for name, env in (('on', {}), ('off', {'FNVNX_VTGET': '0'})):
            out, fixed = os.path.join(t, 'out-' + name), os.path.join(t, name, 'fixed')
            e = dict(os.environ, **env)
            run([sys.executable, FNVLINK, 'resolve', os.path.join(t, 'units.json'), os.path.join(t, 'objects.tsv'),
                 out, '--imports', 'none', '--exe', exe], env=e)
            run([sys.executable, FNVLINK, 'apply', out, fixed], env=e)
            chk = run([sys.executable, FNVLINK, 'check', out, fixed], env=e)
            res[name] = (out, fixed, chk, nm(os.path.join(out, 'trial.elf')))
        out, fixed, chk, sy = res['on']
        al = open(os.path.join(out, 'aliases.ld')).read()
        check('_ZTV1Z = _ZTV1C + 0;' in al, 'aliases.ld has `_ZTV1Z = _ZTV1C + 0;`: %s'
              % [x for x in al.splitlines() if '_ZTV1' in x])
        check('_ZTV1Z' not in open(os.path.join(res['off'][0], 'aliases.ld')).read(), 'FNVNX_VTGET=0: no alias')
        rows = [x.rstrip('\n').split('\t') for x in open(os.path.join(out, 'vtget.tsv')) if not x.startswith('#')]
        check(rows == [['%08x' % BASE, '%08x' % v, '_ZTV1Z', '_ZTV1C', 'aliased']], 'vtget.tsv: %s' % rows)
        check('multiple definitions: 0' in chk, 'trial link: no multiple definitions: %s' % chk.strip())
        check('_ZTV1C' in sy and sy.get('_ZTV1Z') == sy['_ZTV1C'],
              'trial link: _ZTV1Z is _ZTV1C (the getter returns C\'s address point): %s' % {k: hex(x) for k, x in sy.items() if k.startswith('_ZTV1')})
        sy_off = res['off'][3]
        check('_ZTV1Z' in sy_off and sy_off['_ZTV1Z'] != sy_off['_ZTV1C'], 'without the alias the two stay apart')


def main():
    unit_a()
    unit_b()
    e2e()
    print('%d failure(s)' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
