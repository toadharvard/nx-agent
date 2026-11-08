#!/usr/bin/env python3
"""seamcheck.py (VCALL check) on hand-made fixtures: tiny x86 and AArch64 ILP32 snippets (assembled with the
installed assemblers when present, else the same bytes written by hand), hand-made vtable pairs. No exe, no link.

    python3 tools/link/test_seamcheck.py

Covers: a matching site; the minus-one mismatch (native slot k = j + 1 against a table without a D1/D0 pair) and the
same shift against a table with the pair (ok); an unalignable function; run collapsing; the anchor rule.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import seamcheck as S    # noqa: E402
import vtables           # noqa: E402

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


# x86: mov eax,[ecx]; call [eax+off]; ret
def x86_vcalls(offs):
    code = b''
    for o in offs:
        code += b'\x8b\x01' + (b'\xff\x50' + bytes([o]))
    return code + b'\xc3'


def assemble_x86(offs, tmp):
    """The same function through `as --32` when available (else the hand-written bytes)."""
    src = '.intel_syntax noprefix\n.text\nf:\n' + ''.join('mov eax,[ecx]\ncall [eax+%d]\n' % o for o in offs) + 'ret\n'
    if shutil.which('as') and shutil.which('objcopy'):
        try:
            sp, op, bp = (os.path.join(tmp, n) for n in ('x.s', 'x.o', 'x.bin'))
            open(sp, 'w').write(src)
            subprocess.run(['as', '--32', '-o', op, sp], check=True, capture_output=True)
            subprocess.run(['objcopy', '-O', 'binary', '-j', '.text', op, bp], check=True, capture_output=True)
            return open(bp, 'rb').read()
        except (subprocess.CalledProcessError, OSError):
            pass
    return x86_vcalls(offs)


def assemble_a64(offs, tmp, calls=()):
    """AArch64 ILP32 object with function f: for each off, `ldr w1,[x0]; ldr w1,[x1,#off]; blr x1`; then `bl <sym>`
    for each symbol of calls. -> (code, {offset: reloc symbol}) read back through vtables.Obj."""
    src = '.text\n.globl f\n.type f,%function\nf:\nstp x29,x30,[sp,#-16]!\n' + \
        ''.join('ldr w1,[x0]\nldr w1,[x1,#%d]\nblr x1\n' % o for o in offs) + \
        ''.join('bl %s\n' % c for c in calls) + 'ldp x29,x30,[sp],#16\nret\n.size f,.-f\n'
    sp, op = os.path.join(tmp, 'n.s'), os.path.join(tmp, 'n.o')
    open(sp, 'w').write(src)
    subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', '-o', op, sp], check=True, capture_output=True)
    return S.native_function(vtables.Obj(op), ['f'], None)


def events(offs_x, offs_n, tmp, calls_n=()):
    base = 0x401000
    code = assemble_x86(offs_x, tmp)
    insns = S.x86_disasm(lambda a, n: code[a - base:a - base + n] or None, base)
    ex = S.x86_events(insns, base)
    nat_code, rels = assemble_a64(offs_n, tmp, calls_n)
    nv = S.a64_events(nat_code, rels, lambda n: None)
    return ex, nv


def pair(cls, native_addrs, exe_slots, c=None, primary=True):
    return S.Pair(0x01000000, cls, '_ZTV' + cls, primary, c, native_addrs, exe_slots)


NEW, CTOR = 0x401800, 0x401900          # operator new and a constructor (addrmap kind C) of the snippets
VFT, VBASE = 0x01100000, 0x01100100     # the constructor's vftable (class Der, derived from Base) and Base's
GLOB, FNPTR, IAT = 0x01200000, 0x01200010, 0x00fdf0ac


def x86_function(src, at, tmp):
    """The intel-syntax x86 source assembled and linked at `at` -> (events, facts) of seamcheck, or None without
    binutils for i386."""
    if not (shutil.which('as') and shutil.which('ld')):
        return None
    sp, op, bp = (os.path.join(tmp, n) for n in ('f.s', 'f.o', 'f.bin'))
    open(sp, 'w').write('.intel_syntax noprefix\n.text\n.globl f\nf:\n' + src + '\n')
    try:
        subprocess.run(['as', '--32', '-o', op, sp], check=True, capture_output=True)
        subprocess.run(['ld', '-m', 'elf_i386', '-Ttext=0x%x' % at, '-e', 'f', '--oformat', 'binary', '-o', bp, op],
                       check=True, capture_output=True)
    except (subprocess.CalledProcessError, OSError):
        return None
    code = open(bp, 'rb').read()
    insns = S.x86_disasm(lambda a, n: code[a - at:a - at + n] or None, at)
    info = {}
    ev = S.x86_events(insns, at, info=info)
    return ev, S._slim(info)


def crossed_pairs(tmp):
    """GCC lays out neighbouring virtual calls in another order (switch arms): pairing by address order crosses
    them. Native = exe + 1 at every site (a table with the D1/D0 pair); repair_crossed re-pairs by target."""
    exe = [0x401100 + 0x100 * i for i in range(8)]
    shifted = S.Pair(0x01000000, '3Bar', '_ZTV3Bar', True, 0, [exe[0], None] + exe[1:], exe)
    judge = S.Judge([shifted])

    def judge_one(s):
        return judge.site(s[4], s[5], '3Bar'), ()
    for xo, no, what in (([8, 12], [16, 12], 'two neighbours (vfn_8c / vfn_90 kind)'),
                         ([8, 20, 16], [12, 20, 24], 'a switch: exe 2/5/4 -> native 3/6/5')):
        ex, nv = events(xo, no, tmp)
        st, pairs, _ = S.align(ex, nv)
        sites = [('f', 0x401000, a[-1], b[-1], a[2] // 4, b[2] // 4, False, None, None, None, False) for a, b in pairs]
        judged = [judge_one(x) for x in sites]
        before = sum(1 for x in judged if x[0][0] != 'ok')
        n = S.repair_crossed(sites, judged, judge_one)
        # (align's delta rule re-matches the three-call switch already; the two neighbours need the target cross)
        check(st == 'aligned' and before + n in (0 + 0, 2 + 1) and all(x[0][0] == 'ok' for x in judged) and
              all(s[5] == s[4] + 1 for s in sites), 'crossed pair repaired: %s (%s)' % (what, [(s[4], s[5]) for s in sites]))
    # the function's delta mode re-matches the calls GCC moved (R2's 008aa98c switch, 00901d46 rotation)
    for xo, no, want, what in (([8, 12, 24, 20], [12, 16, 24, 28], [(2, 3), (3, 4), (6, 7), (5, 6)], 'switch arms'),
                               ([8, 36, 12, 16], [12, 16, 40, 20], [(2, 3), (9, 10), (3, 4), (4, 5)], 'a rotation'),
                               ([8, 12, 16], [12, 16, 24], [(2, 3), (3, 4), (4, 6)], 'an off-mode site with no '
                                                                                   'other partner stays')):
        ex, nv = events(xo, no, tmp)
        st, pairs, note = S.align(ex, nv)
        got = [(a[2] // 4, b[2] // 4) for a, b in pairs]
        check(st == 'aligned' and got == want, 'mode repair: %s (%s, %s)' % (what, got, note))
    # a real mismatch is not "repaired": the targets do not cross
    ex, nv = events([8, 12], [8, 12], tmp)
    _, pairs, _ = S.align(ex, nv)
    sites = [('f', 0x401000, a[-1], b[-1], a[2] // 4, b[2] // 4, False, None, None, None, False) for a, b in pairs]
    judged = [judge_one(x) for x in sites]
    check(S.repair_crossed(sites, judged, judge_one) == 0 and all(x[0][0] == 'mismatch' for x in judged),
          'uncrossed minus-one sites stay mismatches')


def receiver_inference(tmp):
    """Infer (sources a-d), site_set, param_types and the native GOT globals on hand-made snippets."""
    from exevft import Vft
    fns = {
        # (a) new + constructor, the result is the receiver of slot 2
        0x402000: 'push 0x14\ncall 0x%x\nadd esp, 4\nmov ecx, eax\ncall 0x%x\nmov esi, eax\nmov eax, [esi]\n'
                  'call dword ptr [eax+8]\nret' % (NEW, CTOR),
        # (b) a global set from a constructed object, read back through a getter
        0x402100: 'push 0x14\ncall 0x%x\nadd esp, 4\nmov ecx, eax\ncall 0x%x\nmov dword ptr [0x%x], eax\nret'
                  % (NEW, CTOR, GLOB),
        0x402200: 'mov eax, dword ptr [0x%x]\nret' % GLOB,
        0x402300: 'call 0x402200\nmov ecx, eax\nmov edx, [ecx]\ncall dword ptr [edx+4]\nret',
        # (b) an import's result kept in a function pointer; the object it makes is an external (COM) object
        0x402400: 'mov esi, dword ptr [0x%x]\npush 0\ncall esi\nmov dword ptr [0x%x], eax\nret' % (IAT, FNPTR),
        0x402500: 'call dword ptr [0x%x]\nmov esi, eax\nmov eax, [esi]\npush esi\ncall dword ptr [eax+8]\nret' % FNPTR,
        # (b) an argument: the only caller passes a constructed object; ebp is a general register here
        0x402600: 'push 0x14\ncall 0x%x\nadd esp, 4\nmov ecx, eax\ncall 0x%x\npush eax\nmov ecx, esi\n'
                  'call 0x402700\nret' % (NEW, CTOR),
        0x402700: 'push ebx\npush ebp\nmov ebp, dword ptr [esp+0xc]\nmov eax, [ebp]\npush ebp\nmov ecx, [eax+0x10]\n'
                  'call ecx\npop ebp\npop ebx\nret 4',
    }
    got = {}
    for a, src in fns.items():
        r = x86_function(src, a, tmp)
        if r is None:
            print('SKIP: receiver inference (no i386 binutils)')
            return
        got[a] = r
    symof = {CTOR: '_ZN3DerC1Ev', 0x402600: '_ZN3Foo3runEv', 0x402700: '_ZN3Bar4takeEP4Base'}
    exev = {VFT: Vft('.?AVDer@@', 0, [('.?AVDer@@', 0), ('.?AVBase@@', 0)], [0x403000 + 0x10 * i for i in range(6)]),
            VBASE: Vft('.?AVBase@@', 0, [('.?AVBase@@', 0)], [0x403000 + 0x10 * i for i in range(6)])}
    der = S.Pair(VFT, '3Der', '_ZTV3Der', True, None, list(exev[VFT].slots), list(exev[VFT].slots))
    base = S.Pair(VBASE, '4Base', '_ZTV4Base', True, None, list(exev[VBASE].slots), list(exev[VBASE].slots))
    judge = S.Judge([der, base])
    finfo = {a: r[1] for a, r in got.items()}

    def infer(on='abcd', fi=finfo):
        return S.Infer(fi, symof, {CTOR: 'C'}, exev, lambda a: VFT if a == CTOR else None, judge, on, (0xfdf000, 0xfdf100))

    def site(a):
        e = [x for x in got[a][0] if x[0] == 'V'][0]
        return e, infer().resolve(a, e[4], e[-1])

    e, (ev, src) = site(0x402000)
    check(ev == frozenset([('dyn', VFT)]) and 'ctor' in src, 'infer (a): new + constructor -> the vftable it stores')
    check(infer('').resolve(0x402000, e[4], e[-1])[0] is None, 'infer: no sources, no class')
    pairs, order = infer().candidates(ev)
    check(pairs == [der] and not order, 'infer: a dyn class is judged by its own pair only')
    check(S.site_set(2, 2, pairs, order, 'ctor')[0] == 'ok', 'site_set: slot 2 = slot 2 on Der: ok')
    v, recv, _, _, note = S.site_set(2, 3, pairs, order, 'ctor')
    check(v == 'mismatch' and recv == 'Der' and 'by-ctor' in note, 'site_set: slot 2 / 3 on Der: mismatch')
    e, (ev, src) = site(0x402300)
    check(ev == frozenset([('dyn', VFT)]) and {'global', 'ret', 'ctor'} <= src,
          'infer (b): getter -> global -> constructed object (%s)' % sorted(src))
    # honesty: one store of an unknown value makes the global unknown
    r = x86_function('mov dword ptr [0x%x], esi\nret' % GLOB, 0x402800, tmp)
    fi = dict(finfo)
    fi[0x402800] = r[1]
    check(infer(fi=fi).resolve(0x402300, e[4], e[-1])[0] is None, 'infer (b): an unknown store -> unknown')
    e, (ev, src) = site(0x402500)
    check(ev == frozenset([('ext',)]), 'infer (b): an import\'s result through a function pointer -> external')
    pairs, order = infer().candidates(ev)
    check(S.site_set(2, 3, pairs, order, 'ext')[0] == 'mismatch' and S.site_set(2, 2, pairs, order, 'ext')[0] == 'ok',
          'site_set: an external object keeps the exe order (2/3 mismatch, 2/2 ok)')
    e, (ev, src) = site(0x402700)
    check(e[4] == ('arg', 1) and e[2] == 0x10, 'x86: ebp without a frame is a register (arg 1, slot 4)')
    check(ev == frozenset([('dyn', VFT)]) and 'arg' in src, 'infer (b): an argument from the only caller')
    # (a+b) a field of an object of a known exe class: Owner's constructor stores a Der at +0x10, Owner's virtual
    # method (a slot of Owner's vftable) calls through [this+0x10]
    vown = 0x01100200
    own = {0x402900: 'push esi\nmov esi, ecx\nmov dword ptr [esi], 0x%x\npush 0x14\ncall 0x%x\nadd esp, 4\n'
                     'mov ecx, eax\ncall 0x%x\nmov [esi+0x10], eax\nmov eax, esi\npop esi\nret' % (vown, NEW, CTOR),
           0x402a00: 'mov ecx, [ecx+0x10]\nmov eax, [ecx]\ncall dword ptr [eax+8]\nret'}
    fo = dict(finfo)
    ev_own = {}
    for a, src in own.items():
        ev_own[a], fo[a] = x86_function(src, a, tmp)
    xv = dict(exev)
    xv[vown] = Vft('.?AVOwner@@', 0, [('.?AVOwner@@', 0)], [0x402a00])
    vfts = {CTOR: VFT, 0x402900: vown}

    def infer_own(fi):
        return S.Infer(fi, symof, {CTOR: 'C', 0x402900: 'C'}, xv, vfts.get, judge, 'abcd', (0xfdf000, 0xfdf100))
    e = [x for x in ev_own[0x402a00] if x[0] == 'V'][0]
    ev, src = infer_own(fo).resolve(0x402a00, e[4], e[-1])
    check(e[4] == ('ld', ('this',), 0x10) and ev == frozenset([('dyn', VFT)]) and 'objfield' in src,
          'infer (a+b): a field of a known exe class -> the class stored there (%s %s)' % (ev, sorted(src)))
    r = x86_function('mov dword ptr [esi], 0x%x\nmov [esi+0x10], edi\nret' % vown, 0x402b00, tmp)
    fo[0x402b00] = r[1]
    ev, src = infer_own(fo).resolve(0x402a00, e[4], e[-1])
    check(ev is None, 'infer (a+b): one unknown store to the field -> unknown')
    # (c) a static type is judged by every class whose RTTI hierarchy holds it
    c = infer().candidates(frozenset([('st', '4Base')]))
    check(c and set(map(id, c[0])) == {id(der), id(base)}, 'infer (c): static Base -> Base and Der')
    c = infer('abd').candidates(frozenset([('st', '4Base')]))
    check(c and c[0] == [base], 'infer: without (c) a static type is its own table')
    v = S.site_set(2, 3, [der, base], [], 'member')
    check(v[0] == 'mismatch' and v[1] == 'Base,Der', 'site_set: the receiver column lists every class')
    # (d) the C++ type of a parameter from the mangled name
    ev, src = infer().native('_ZN3Bar4takeEP4Base', ('arg', 1))
    check(ev == frozenset([('st', '4Base')]) and 'argtype' in src, 'infer (d): parameter type from the mangled name')
    check(S.param_types('_ZN3Foo3barEPKS_RS_') == ['3Foo', '3Foo'], 'param_types: substitutions')
    check(S.param_types('_ZN13MemoryManager4SizeEPKv') == [None], 'param_types: const void *')
    check(S.param_types('_ZN3FooC1Eii') == [None, None] and S.param_types('_Z3foov') == [],
          'param_types: constructor, void')
    check(S.msvc_td('N3foo3BarE') == ['.?AVBar@foo@@', '.?AUBar@foo@@'], 'msvc_td: namespaced')

    # (d) native: a GOT-loaded global function pointer is no virtual call; a global object's vcall is
    if shutil.which('aarch64-linux-gnu-as'):
        src = ('.text\n.globl f\n.type f,%function\nf:\nstp x29,x30,[sp,#-16]!\n'
               'adrp x1, :got:g\nldr w1, [x1, #:got_lo12:g]\nldr w1, [x1]\nblr x1\n'
               'adrp x0, :got:obj\nldr w0, [x0, #:got_lo12:obj]\nldr w1, [x0]\nldr w1, [x1, #8]\nblr x1\n'
               'ldp x29,x30,[sp],#16\nret\n.size f,.-f\n')
        sp, op = os.path.join(tmp, 'g.s'), os.path.join(tmp, 'g.o')
        open(sp, 'w').write(src)
        subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', '-o', op, sp], check=True, capture_output=True)
        rt = {}
        code, rels = S.native_function(vtables.Obj(op), ['f'], None, rt)
        nv = S.a64_events(code, rels, lambda n: {'obj': GLOB}.get(n), rt)
        check([x[0] for x in nv] == ['I', 'V'] and nv[1][4] == ('ga', GLOB) and nv[1][2] == 8,
              'a64: GOT function pointer call is indirect, the global object\'s call virtual (%s)' % (nv,))
        nv = S.a64_events(code, rels, lambda n: None)
        check([x[0] for x in nv] == ['V', 'V'], 'a64: without relocation types, the old reading')


def main():
    if S.capstone is None or not shutil.which('aarch64-linux-gnu-as'):
        print('SKIP: capstone or aarch64-linux-gnu-as missing')
        return 0
    tmp = tempfile.mkdtemp()
    # exe slots (addresses of the functions the exe vftable reaches) and a native table that agrees with it
    exe = [0x401100, 0x401200, 0x401300, 0x401400, 0x401500]
    # no destructor pair: native slot k is exe slot k
    agree = pair('3Foo', list(exe), exe)
    # destructor pair at 0,1: native k = j + 1 after it (the minus-one rule), slot 1 is D0 (no address)
    shifted = pair('3Bar', [exe[0], None, exe[1], exe[2], exe[3], exe[4]], exe, c=0)

    # --- a matching site: exe slot 2 (call [eax+8]) = native slot 2 (ldr [x1,#8])
    ex, nv = events([8], [8], tmp)
    check([e[0] for e in ex] == ['V'] and ex[0][1:3] == (0, 8) and ex[0][3] == 'this', 'x86 event: V voff 0 off 8 on this')
    check([e[0] for e in nv] == ['V'] and nv[0][1:3] == (0, 8) and nv[0][3] == 'this', 'a64 event: V voff 0 off 8 on this')
    status, pairs, note = S.align(ex, nv)
    check(status == 'aligned' and len(pairs) == 1, 'matching site aligns')
    j, k = pairs[0][0][2] // 4, pairs[0][1][2] // 4
    jd = S.Judge([agree])
    v, recv, nt, et, why = jd.site(j, k)
    check((j, k) == (2, 2) and v == 'ok' and nt == et == exe[2], 'matching site: ok (slot 2 = slot 2)')

    # --- the minus-one mismatch: the native code calls slot 3 (j + 1) where the exe calls slot 2
    ex, nv = events([8], [12], tmp)
    status, pairs, _ = S.align(ex, nv)
    j, k = pairs[0][0][2] // 4, pairs[0][1][2] // 4
    check(status == 'aligned' and (j, k) == (2, 3), 'minus-one site aligns as exe slot 2 / native slot 3')
    v, recv, nt, et, why = S.Judge([agree]).site(j, k)
    check(v == 'mismatch' and nt == exe[3] and et == exe[2], 'minus-one against a table without a pair: mismatch')
    v, *_ = S.Judge([shifted]).site(j, k)
    check(v == 'ok', 'minus-one against a table with the D1/D0 pair: ok')
    v, _, _, _, why = S.Judge([agree, shifted]).site(j, k)
    check(v == 'unknown' and 'ambiguous-receiver agree=1 disagree=1' in why, 'both tables: unknown (ambiguous receiver), counts in the note')
    # a this-based site is checked against the class of the member function only
    v, recv, *_ = S.Judge([agree, shifted]).site(j, k, S.method_class('_ZN3Bar3runEv'))
    check(v == 'ok' and recv == 'Bar', 'this-based site: judged by the member function\'s class (Bar: ok)')
    v, recv, *_ = S.Judge([agree, shifted]).site(j, k, S.method_class('_ZN3Foo3runEv'))
    check(v == 'mismatch' and recv == 'Foo', 'this-based site: judged by the member function\'s class (Foo: mismatch)')
    v, *_ = S.Judge([agree]).site(9, 9)
    check(v == 'unknown', 'a slot no table has: unknown')
    # a receiver class whose native table has another name (a shell, a __fnvnx_vmo_ copy) is found by the exe's RTTI class
    sh = S.Pair(0x01000000, '12FUN_00401100', '_ZTV12FUN_00401100', True, 0, [exe[0], None, exe[1], exe[2], exe[3], exe[4]],
                exe, S.xclass_enc('.?AVNiShell@@'))
    check(S.xclass_enc('.?AVNiShell@@') == '7NiShell' and S.xclass_enc('.?AV?$T@H@@') is None, 'xclass_enc: simple names only')
    v, recv, *_ = S.Judge([sh], {'NiShell'}).site(2, 3, '7NiShell')
    check(v == 'ok' and recv == 'NiShell', 'class found by the exe RTTI name: judged against the shell\'s table (minus-one: ok)')
    v, *_ = S.Judge([sh], {'NiShell'}).site(2, 2, '7NiShell')
    check(v == 'mismatch', 'class found by the exe RTTI name: exe-order call against a minus-one table: mismatch')
    # `delete p` (D0, native slot 1) for the exe's `p->DeleteThis()` (exe slot 1, the common `delete this` body)
    dt = S.Judge([shifted], set(), lambda a: a == exe[1])
    v, *_ = dt.site(1, 1, '3Bar')
    check(v == 'ok', 'DeleteThis body at exe slot 1 = native D0 (slot 1): ok')
    v, *_ = S.Judge([shifted], set(), lambda a: False).site(1, 1, '3Bar')
    check(v == 'mismatch', 'slot 1 that is not the `delete this` body: still a mismatch')

    # --- an unalignable function: the exe has two different virtual calls, the native code one
    ex, nv = events([8, 16], [8], tmp)
    status, pairs, note = S.align(ex, nv)
    check(status == 'unaligned' and not pairs, 'unalignable function is skipped (%s)' % note)
    # equal run counts but an inconsistent offset map (the same exe offset maps to two native offsets)
    ex, nv = events([8, 16, 8], [8, 16, 12], tmp)
    status, _, note = S.align(ex, nv)
    check(status == 'unaligned' and note == 'offset-map', 'inconsistent offset map: unaligned')
    # no virtual calls at all
    check(S.align([], [])[0] == 'no-vcalls', 'no calls: no-vcalls')

    # --- runs: GCC repeats a call MSVC emits once (the same key twice in a row)
    ex, nv = events([8, 16], [8, 8, 16], tmp)
    status, pairs, _ = S.align(ex, nv)
    check(status == 'aligned' and [(a[2], b[2]) for a, b in pairs] == [(8, 8), (16, 16)], 'duplicate native call collapses')

    # --- anchors: different run counts, but identical direct-call anchors between the virtual calls
    ex = [('V', 0, 8, None, 1), ('D', 0x500000, None, 2), ('V', 0, 16, None, 3)]
    nv = [('V', 0, 8, None, 1), ('V', 0, 12, None, 5), ('D', 0x500000, 'f', 2), ('V', 0, 16, None, 3)]
    nv = [nv[0], ('D', 0x500000, 'f', 2), nv[3], ('V', 0, 16, None, 9)]
    status, pairs, note = S.align(ex, nv)
    check(status == 'aligned', 'an anchor between equal runs: aligned')

    # --- direct calls with their targets: bl <sym> resolves through the map; noise is dropped
    ex, nv = events([8], [8], tmp, calls_n=('__fnv_00401234', '_Unwind_Resume'))
    ds = [e for e in nv if e[0] == 'D']
    check([d[2] for d in ds] == ['__fnv_00401234', '_Unwind_Resume'], 'native bl events carry their relocation symbols')
    st, p, _ = S.align(ex, nv)
    check(st == 'aligned', '_Unwind_Resume does not disturb the alignment')

    # --- class of a member function symbol
    check(S.method_class('_ZN13IniCollection12FUN_00c44280EP10IniSetting') == '13IniCollection', 'method_class: plain')
    check(S.method_class('_ZN3foo3Bar3bazEv') == 'N3foo3BarE', 'method_class: namespaced')
    check(S.method_class('_Z12FUN_00401020v') is None, 'method_class: free function')

    crossed_pairs(tmp)
    receiver_inference(tmp)

    shutil.rmtree(tmp, ignore_errors=True)
    print('%d failure(s)' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
