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

    shutil.rmtree(tmp, ignore_errors=True)
    print('%d failure(s)' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
