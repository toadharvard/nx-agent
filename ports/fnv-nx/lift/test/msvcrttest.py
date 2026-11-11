#!/usr/bin/env python3
"""Test of the msvcrt guest entries of the lifter runtime (rt/msvcrt_eh.cc, rt/msvcrt_fp.cc): _except_handler3, __CxxFrameHandler, _CxxThrowException, _XcptFilter,
?terminate@@YAXXZ, ??1type_info@@UAE@XZ, _setjmp3, longjmp, _controlfp, _clearfp, _fpclass, _finite, _isnan.
    msvcrttest.py [--work DIR] [--only NAME]
Hand-assembled x86 guest functions (synthetic image at 0x401000, MSVC-shaped frames: SEH3 registration + scope table, C++ registration + FuncInfo) are lifted with
fnvlift, linked with the runtime (rt/seh.cc, rt/enter.cc, rt/unhandled.cc, the two msvcrt files, the generated import entries of the bind-l3 rows) as AArch64 ILP32 and
run under Unicorn from a native `test_main` (the harness of test/imptest.py). There is no x86 oracle for msvcrt.dll: every expected value is derived by hand from
the MSVC / Wine 9.0 semantics and stated at its case. `struct cpu` has no `msvcrt` field yet (rt/msvcrt.h): the runtime files are compiled from a copy of rt/ in the
work directory whose fnvl.h gets `u32 msvcrt[6];` appended to struct cpu unless the real one has it; the lifted guest code does not touch it.
Trace convention: a guest event EV(k) appends the hex digit k to the word TR; a case that ends in a return gives TR in eax. Output words O0.. hold values a case stores.
Prints one line per case; exit status 1 on any difference."""
import argparse, os, re, shutil, struct, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
for p in ('bench', 'tools', 'test', ''): sys.path.insert(0, os.path.join(LIFT, p))
import fnvlift as L, instest, phf, impgen
from keystone import Ks, KS_ARCH_X86, KS_MODE_32
from unicorn import Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_CPACR_EL1
from elftools.elf.elffile import ELFFile

BASE, SLOT, MAINMAX, CODE_END, IAT, DATA, GSTACK, HSTACK, STOP, TEXT, TEB = 0x401000, 0x300, 0x180, 0x420000, 0x421000, 0x430000, 0x60000000, 0x70000000, 0x7e000000, 0xE0000000, 0x50000000
M = 0xffffffff
RUNTIME = ['msvcrt_eh.cc', 'msvcrt_fp.cc']
ROWS = [  # dll, name, conv, ret, args, native symbol, bind: the l3 rows of bridge.tsv for msvcrt.dll
    ('_except_handler3', 'cdecl', 'i', 'iiii'), ('__CxxFrameHandler', 'cdecl', 'i', 'iiii'), ('_CxxThrowException', 'stdcall', 'i', 'ii'), ('_XcptFilter', 'cdecl', 'i', 'ii'),
    ('?terminate@@YAXXZ', 'cdecl', 'i', ''), ('??1type_info@@UAE@XZ', 'thiscall', 'i', 'i'), ('_setjmp3', 'cdecl', 'i', 'ii'), ('longjmp', 'cdecl', 'i', 'ii'),
    ('_controlfp', 'cdecl', 'i', 'ii'), ('_clearfp', 'cdecl', 'i', ''), ('_fpclass', 'cdecl', 'i', 'd'), ('_finite', 'cdecl', 'i', 'd'), ('_isnan', 'cdecl', 'i', 'd')]
SYM = {'_except_handler3': 'except_handler3', '__CxxFrameHandler': 'CxxFrameHandler', '_CxxThrowException': 'CxxThrowException', '_XcptFilter': 'XcptFilter',
       '?terminate@@YAXXZ': 'terminate', '??1type_info@@UAE@XZ': 'type_info_dtor', '_setjmp3': 'setjmp3', 'longjmp': 'longjmp', '_controlfp': 'controlfp',
       '_clearfp': 'clearfp', '_fpclass': 'fpclass', '_finite': 'finite', '_isnan': 'isnan'}
BRIDGE = [('msvcrt.dll', n, cv, rt, ar, 'fnvl_msvcrt_' + SYM[n], 'l3') for n, cv, rt, ar in ROWS]
KEY = {r[1]: 0x00100000 + 16 * i for i, r in enumerate(sorted(BRIDGE, key=lambda r: (r[0], r[1])))}      # the key rule of tools/impgen.py
SLOTS = {r[1]: IAT + 4 * i for i, r in enumerate(BRIDGE)}
EH_KEYS = {0x000fffd0: 'fnvl_msvcrt_eh_nested_catch', 0x000fffe0: 'fnvl_msvcrt_eh_nested_collide'}      # rt/msvcrt.h: the runtime's own registrations
NATIVE = r'''
typedef unsigned int u32;
u32 fnvnx_lift_enter(u32 eip, u32 esp, u32 teb);
static void *slot; void **fnvl_host_tls_slot(void) { return &slot; }
unsigned long long fnvl_host_ticks_ns(void) { return 0; }
void *memcpy(void *d, const void *s, unsigned long n) { char *a = d; const char *b = s; while (n--) *a++ = *b++; return d; }
void *memset(void *d, int v, unsigned long n) { char *a = d; while (n--) *a++ = (char)v; return d; }
u32 g_free_arg, g_free_n, g_ue_code, g_ue_n, g_ue_addr;
void t_free(void *p) { g_free_arg = (u32)(unsigned long)p; g_free_n++; }
void t_abort(void) { for (;;) ; }
u32 UnhandledExceptionFilter(u32 p) { g_ue_code = *(u32 *)(unsigned long)(*(u32 *)(unsigned long)p); g_ue_addr = *(u32 *)(unsigned long)(*(u32 *)(unsigned long)p + 12); g_ue_n++; return 1; }
void ExitProcess(u32 code) { for (;;) ; }
void fnvnx_win32_set_raise_backend(void (*f)(u32, u32, u32, const u32 *)) { }
void *fnvnx_win32_lookup_export(const char *dll, const char *name) { return name[0] == 'f' ? (void *)t_free : name[0] == 'a' ? (void *)t_abort : 0; }
u32 test_main(u32 eip, u32 esp, u32 teb) { return fnvnx_lift_enter(eip, esp, teb); }
'''


class Fake:
    def __init__(s, code): s.d = code; s.secs = [('.text', BASE, len(code), 0, len(code))]; s.rset = set(); s.taken = []; s.base = BASE
    def bytes(s, a, n): return s.d[a - BASE:a - BASE + n]
    def u32(s, a): return struct.unpack_from('<I', s.d, a - BASE)[0]


# ---------------------------------------------------------------- the guest program
EV = lambda k: 'push eax; mov eax, dword ptr [@TR]; shl eax, 4; or eax, %d; mov dword ptr [@TR], eax; pop eax;' % k          # trace digit k (flags clobbered)
RET_TR = 'mov eax, dword ptr [@TR];'
# SEH3 frame (MSVCRT_EXCEPTION_FRAME at ebp-16: Prev, Handler, scopetable, trylevel; ebp = &frame->_ebp; [ebp-0x14] EXCEPTION_POINTERS, [ebp-0x18] saved esp)
SEH = lambda scope: ('push ebp; mov ebp, esp; push -1; push @%s; push dword ptr [@S._except_handler3]; push dword ptr fs:[0]; mov dword ptr fs:[0], esp;'
                     'sub esp, 8; mov dword ptr [ebp-0x18], esp;' % scope)
SEH_X = 'mov ecx, dword ptr [ebp-16]; mov dword ptr fs:[0], ecx; mov esp, ebp; pop ebp; ret'
TL = lambda n: 'mov dword ptr [ebp-4], %d;' % n
# C++ frame (cxx_exception_frame at ebp-12: Prev, Handler = the FuncInfo thunk, trylevel; ebp = &frame->ebp; [ebp-16] saved esp, [ebp-20].. locals / catch variables)
CXX = lambda thunk: ('push ebp; mov ebp, esp; push -1; push @%s; push dword ptr fs:[0]; mov dword ptr fs:[0], esp; sub esp, 0x10; mov dword ptr [ebp-0x10], esp;' % thunk)
CXX_X = 'mov ecx, dword ptr [ebp-12]; mov dword ptr fs:[0], ecx; mov esp, ebp; pop ebp; ret'
THROW = 'call dword ptr [@S._CxxThrowException];'


class Prog:
    """functions (a slot of SLOT bytes each: main chunk at +0, further chunks at +0x100, +0x140, .., or `after` the previous chunk) and data words; @name placeholders"""
    def __init__(s): s.fns = {}; s.order = []; s.data = {}; s.dorder = []; s.addr = {}; s.owns = {}
    def fn(s, name, main, **chunks):
        s.fns[name] = [(None, main)] + list(chunks.items()); s.order.append(name)
    def dat(s, name, words): s.data[name] = words; s.dorder.append(name)
    def layout(s, sizes):
        for i, n in enumerate(s.order):
            a = BASE + SLOT * i; s.addr[n] = a; k = 0; prev_end = a; s.owns[n] = []
            for j, (lab, txt) in enumerate(s.fns[n]):
                after = isinstance(txt, tuple)
                at = a if j == 0 else prev_end if after else a + MAINMAX + 0x40 * k
                if j and not after: k += 1
                if lab: s.addr[n + '.' + lab] = at; s.owns[n].append(at)
                prev_end = at + sizes.get((n, j), 0)
        d = DATA
        for n in s.dorder: s.addr[n] = d; d += 4 * len(s.data[n]) if not isinstance(s.data[n], bytes) else (len(s.data[n]) + 3) // 4 * 4
        s.addr['TR'] = d; s.addr['O'] = d + 4; s.dend = d + 4 + 4 * 32
    def sub(s, txt, final):
        def r(m):
            k = m.group(1)
            if k in s.addr: return '0x%x' % s.addr[k]
            if k.startswith('S.'): return '0x%x' % SLOTS[k[2:]]
            if re.fullmatch(r'O\d+', k): return '0x%x' % (s.addr['O'] + 4 * int(k[1:]))
            if final: raise KeyError(k)
            return '0x401000'
        return re.sub(r'@([\w.?@$]+)', lambda m: r(m), txt)
    def build(s):
        ks = Ks(KS_ARCH_X86, KS_MODE_32); sizes = {}
        s.layout(sizes)
        for final in (False, True):
            code = bytearray(b'\xcc' * (SLOT * len(s.order) + 0x100)); sizes2 = {}
            for n in s.order:
                for j, (lab, txt) in enumerate(s.fns[n]):
                    t = txt[1] if isinstance(txt, tuple) else txt
                    at = s.addr[n] if j == 0 else s.addr[n + '.' + lab]
                    b = bytes(ks.asm(s.sub(t, final), at)[0]); sizes2[(n, j)] = len(b)
                    assert len(b) <= (MAINMAX if j == 0 else 0x40) or isinstance(txt, tuple), (n, lab, len(b))
                    code[at - BASE:at - BASE + len(b)] = b
            if not final: sizes = sizes2; s.layout(sizes)
        s.code = bytes(code)
        words = {}
        for n in s.dorder:
            w = s.data[n]
            words[n] = w if isinstance(w, bytes) else b''.join(struct.pack('<I', (s.addr[x] if isinstance(x, str) else x) & M) for x in w)
        s.dbytes = words
    def data_image(s):
        img = bytearray(s.dend - DATA)
        for n in s.dorder: b = s.dbytes[n]; img[s.addr[n] - DATA:s.addr[n] - DATA + len(b)] = b
        return bytes(img)


def program():
    P = Prog(); fn, dat = P.fn, P.dat
    zeros = lambda n: [0] * n
    # ---- SEH3 (_except_handler3). Scope table entries are {previousTryLevel, filter, handler}; a null filter = __finally
    # s1: filter returns 1; the filter reads the exception code through [ebp-0x14]. div by zero: STATUS_INTEGER_DIVIDE_BY_ZERO 0xC0000094.
    #     EV1 body, EV2 filter, EV3 except block: TR = 0x123, O0 = 0xC0000094
    dat('SC_s1', [M, 's1f', 's1.x'])
    fn('s1', SEH('SC_s1') + EV(1) + TL(0) + 'xor ecx, ecx; mov eax, 5; xor edx, edx; div ecx;' + EV(9) + 'jmp @s1.r;',
       x='mov esp, dword ptr [ebp-0x18];' + EV(3) + TL(-1) + 'jmp @s1.r;', r=RET_TR + SEH_X)
    fn('s1f', EV(2) + 'mov eax, dword ptr [ebp-0x14]; mov eax, dword ptr [eax]; mov eax, dword ptr [eax]; mov dword ptr [@O0], eax; mov eax, 1; ret')
    # s2: inner filter returns 0 (continue search), the outer one returns 1: EV1 outer, EV2 inner body, EV3 inner filter, EV4 outer filter, EV5 outer except: TR = 0x12345
    dat('SC_s2o', [M, 's2of', 's2o.x']); dat('SC_s2i', [M, 's2if', 's2i.x'])
    fn('s2o', SEH('SC_s2o') + EV(1) + TL(0) + 'call @s2i;' + EV(9) + 'jmp @s2o.r;', x='mov esp, dword ptr [ebp-0x18];' + EV(5) + TL(-1) + 'jmp @s2o.r;', r=RET_TR + SEH_X)
    fn('s2i', SEH('SC_s2i') + EV(2) + TL(0) + 'xor ecx, ecx; mov eax, 5; xor edx, edx; div ecx;' + EV(9) + 'jmp @s2i.r;', x=EV(9) + 'jmp @s2i.r;', r=SEH_X)
    fn('s2of', EV(4) + 'mov eax, 1; ret'); fn('s2if', EV(3) + 'xor eax, eax; ret')
    # s3: the filter returns -1 after it has changed Ecx in the CONTEXT (ContextRecord at [pointers+4], Ecx at +172) from 0 to 3: the faulting `div` runs again: 6 / 3 = 2.
    #     EV1 body, EV2 filter, EV3 after the division: TR = 0x123, O0 = 2
    dat('SC_s3', [M, 's3f', 's3.x'])
    fn('s3', SEH('SC_s3') + EV(1) + TL(0) + 'xor ecx, ecx; mov eax, 6; xor edx, edx; jmp @s3.d;',
       d='div ecx; mov dword ptr [@O0], eax;' + EV(3) + TL(-1) + 'jmp @s3.r;', x=EV(9) + 'jmp @s3.r;', r=RET_TR + SEH_X)
    fn('s3f', EV(2) + 'mov eax, dword ptr [ebp-0x14]; mov eax, dword ptr [eax+4]; mov dword ptr [eax+172], 3; mov eax, -1; ret')
    # s4: nested __try/__finally unwound by an outer __except. Inner frame: level 0 = finally s4ia, level 1 (inside level 0) = finally s4ib, fault at level 1; middle frame: finally s4mf.
    #     Search: no filters but the outer one (EV2, returns 1). Unwind runs inner level 1 (EV3), level 0 (EV4), middle (EV5), then the outer __except block (EV6): TR = 0x123456
    dat('SC_s4o', [M, 's4of', 's4o.x']); dat('SC_s4m', [M, 0, 's4mf']); dat('SC_s4i', [M, 0, 's4ia', 0, 0, 's4ib'])
    fn('s4o', SEH('SC_s4o') + EV(1) + TL(0) + 'call @s4m;' + EV(9) + 'jmp @s4o.r;', x='mov esp, dword ptr [ebp-0x18];' + EV(6) + TL(-1) + 'jmp @s4o.r;', r=RET_TR + SEH_X)
    fn('s4m', SEH('SC_s4m') + TL(0) + 'call @s4i;' + EV(9) + TL(-1) + 'jmp @s4m.r;', r=SEH_X)
    fn('s4i', SEH('SC_s4i') + TL(0) + TL(1) + 'xor ecx, ecx; mov eax, 5; xor edx, edx; div ecx;' + EV(9) + 'jmp @s4i.r;', r=SEH_X)
    fn('s4of', EV(2) + 'mov eax, 1; ret'); fn('s4mf', EV(5) + 'ret'); fn('s4ia', EV(4) + 'ret'); fn('s4ib', EV(3) + 'ret')

    # ---- C++ (__CxxFrameHandler). type_info: {vtable, name, mangled}; thunk: mov eax, FuncInfo; jmp __CxxFrameHandler
    def td(n, mangled): dat(n, [0, 0] + [struct.unpack('<I', (mangled + '\0' * 8)[i:i + 4].encode())[0] for i in (0, 4)])
    td('TD_int', '.H'); td('TD_char', '.D'); td('TD_B', '.?AUB@@'); td('TD_D', '.?AUD@@')
    thunk = lambda n, fi: fn(n, 'mov eax, @%s; jmp dword ptr [@S.__CxxFrameHandler]' % fi)
    # simple type int: CT {flags 1 (simple), type_info, this_offset 0, vbase_descr -1, vbase_offset 0, size 4, copy_ctor 0}; CTA {count, CT..}; ThrowInfo {flags, destructor, custom_handler, CTA}
    dat('CT_int', [1, 'TD_int', 0, M, 0, 4, 0]); dat('CTA_int', [1, 'CT_int']); dat('TI_int', [0, 0, 0, 'CTA_int'])

    def chain(p, thrower, tbs, a_k=None, b=True):
        """p+'a': C++ frame whose FuncInfo has the try blocks `tbs` ([start, end, catch_level, [catchblock words..]]); p+'b': frame with an unwind-map action (EV3) for trylevel 0
        (unless b is False); p+'c': the thrower. a calls b calls c. Events: EV1 in a, EV2 in b before the call, EV3 the unwind action. a_k: the continuation chunk of a (default EV5)"""
        thunk(p + 'ta', p + 'FIa')
        dat(p + 'FIa', [0x19930520, 0, 0, len(tbs), p + 'FIa_tb', 0, 0, 0, 0])
        dat(p + 'FIa_tb', [w for i, (st, en, cl, cbs) in enumerate(tbs) for w in (st, en, cl, len(cbs) // 4, '%sFIa_cb%d' % (p, i))])
        for i, tb in enumerate(tbs): dat('%sFIa_cb%d' % (p, i), tb[3])
        fn(p + 'a', CXX(p + 'ta') + EV(1) + TL(0) + 'call @%sb;' % p + EV(9) + TL(-1) + 'jmp @%sa.r;' % p, r=RET_TR + CXX_X,
           k=a_k if a_k is not None else EV(5) + TL(-1) + 'jmp @%sa.r;' % p)
        if b:
            thunk(p + 'tb', p + 'FIb')
            dat(p + 'FIb', [0x19930520, 1, p + 'FIb_uw', 0, 0, 0, 0, 0, 0]); dat(p + 'FIb_uw', [M, p + 'bd'])
            fn(p + 'b', CXX(p + 'tb') + EV(2) + TL(0) + 'call @%sc;' % p + EV(9) + TL(-1) + CXX_X)
            fn(p + 'bd', EV(3) + 'ret')
        fn(p + 'c', thrower)
    thr_int = ('push ebp; mov ebp, esp; sub esp, 8; mov dword ptr [ebp-4], 42; push @%s; lea eax, [ebp-4]; push eax;' + THROW + EV(9) + 'mov esp, ebp; pop ebp; ret')
    CB = lambda fl, ti, off, h: [fl, ti, off & M, h]
    # cx1: throw int; catch(int) two frames up; the unwind action of the middle frame runs between (EV3); the catch block (EV4) sees the copy (42) in [ebp-20] and returns the continuation (EV5).
    #      TR = 0x12345, O0 = 42
    chain('cx1', thr_int % 'TI_int', [(0, 0, 1, CB(0, 'TD_int', -20, 'cx1ac'))])
    fn('cx1ac', EV(4) + 'mov eax, dword ptr [ebp-20]; mov dword ptr [@O0], eax; mov eax, @cx1a.k; ret')
    # cx2: catch(char) does not match int (".D" != ".H"), catch(...) after it does: EV4 in the catch(...) block, never EV7 (the char block). TR = 0x12345
    chain('cx2', thr_int % 'TI_int', [(0, 0, 1, CB(0, 'TD_char', -20, 'cx2ac1') + CB(0, 0, 0, 'cx2ac2'))])
    fn('cx2ac1', EV(7) + 'mov eax, @cx2a.k; ret'); fn('cx2ac2', EV(4) + 'mov eax, @cx2a.k; ret')
    # cx3: rethrow. cx3b catches int (EV2) and executes `throw;`; the exception goes on to cx3a's catch(int) (EV3); the exception object is destroyed after that catch
    #      (the ThrowInfo destructor, thiscall with this = the object: EV4, O1 = *object = 42, O2 = number of calls = 1); then the continuation (EV5). TR = 0x12345, O0 = 42
    dat('TI_cx3', [0, 'cx3e', 0, 'CTA_int'])
    chain('cx3', thr_int % 'TI_cx3', [(0, 0, 1, CB(0, 'TD_int', -20, 'cx3ac'))], b=False)
    fn('cx3ac', EV(3) + 'mov eax, dword ptr [ebp-20]; mov dword ptr [@O0], eax; mov eax, @cx3a.k; ret')
    fn('cx3e', EV(4) + 'mov eax, dword ptr [ecx]; mov dword ptr [@O1], eax; inc dword ptr [@O2]; ret')
    thunk('cx3tb', 'cx3FIb')
    dat('cx3FIb', [0x19930520, 0, 0, 1, 'cx3FIb_tb', 0, 0, 0, 0]); dat('cx3FIb_tb', [0, 0, 1, 1, 'cx3FIb_cb']); dat('cx3FIb_cb', CB(0, 'TD_int', -20, 'cx3bc'))
    fn('cx3b', CXX('cx3tb') + TL(0) + 'call @cx3c;' + EV(9) + TL(-1) + CXX_X)
    fn('cx3bc', EV(2) + 'push 0; push 0;' + THROW + EV(9) + 'ret')
    # cx5: after a handled exception nothing is being handled: a later `throw;` (try level 2 of the same frame, catch(...)) is std::terminate: the run ends in abort().
    #      TR = 0x1234 at that point (EV1, EV2, EV3 unwind action, EV4 catch block, no EV5), O0 = 42
    chain('cx5', thr_int % 'TI_int', [(0, 0, 1, CB(0, 'TD_int', -20, 'cx5ac')), (2, 2, 3, CB(0, 0, 0, 'cx5ac2'))],
          a_k=TL(2) + 'push 0; push 0;' + THROW + EV(9) + 'jmp @cx5a.r;')
    fn('cx5ac', EV(4) + 'mov eax, dword ptr [ebp-20]; mov dword ptr [@O0], eax; mov eax, @cx5a.k; ret'); fn('cx5ac2', EV(8) + 'mov eax, @cx5a.r; ret')
    # cx6: catch(B&) of a thrown D { int a, b; B { int c, d; } } (CTA {D, B with this_offset 8}): the reference is the base subobject: O0 == O1 + 8, O1 = &object. TR = 0x12345
    dat('CT_D', [0, 'TD_D', 0, M, 0, 16, 0]); dat('CT_B', [0, 'TD_B', 8, M, 0, 8, 'ctorB']); dat('CTA_D', [2, 'CT_D', 'CT_B']); dat('TI_D', [0, 0, 0, 'CTA_D'])
    thr_D = ('push ebp; mov ebp, esp; sub esp, 16; mov dword ptr [ebp-16], 0x11; mov dword ptr [ebp-12], 0x22; mov dword ptr [ebp-8], 0x33; mov dword ptr [ebp-4], 0x44;'
             'lea eax, [ebp-16]; mov dword ptr [@O1], eax; push @TI_D; push eax;' + THROW + EV(9) + 'mov esp, ebp; pop ebp; ret')
    chain('cx6', thr_D, [(0, 0, 1, CB(8, 'TD_B', -20, 'cx6ac'))])
    fn('cx6ac', EV(4) + 'mov eax, dword ptr [ebp-20]; mov dword ptr [@O0], eax; mov eax, @cx6a.k; ret')
    # cx7: catch(B b) by value of a class type: the copy constructor ctorB (thiscall, this = the catch variable at [ebp-24], arguments (source = base subobject, 1)) copies two words
    #      (EV6, before the unwind action EV3: TR = 0x126345), the catch block stores them: O0 = 0x33, O1 = 0x44, and O2 = 1 (the second argument of the constructor)
    chain('cx7', thr_D.replace('mov dword ptr [@O1], eax;', ''), [(0, 0, 1, CB(0, 'TD_B', -24, 'cx7ac'))])
    fn('cx7ac', EV(4) + 'mov eax, dword ptr [ebp-24]; mov dword ptr [@O0], eax; mov eax, dword ptr [ebp-20]; mov dword ptr [@O1], eax; mov eax, @cx7a.k; ret')
    fn('ctorB', 'mov edx, dword ptr [esp+4]; mov eax, dword ptr [edx]; mov dword ptr [ecx], eax; mov eax, dword ptr [edx+4]; mov dword ptr [ecx+4], eax;'
                'mov eax, dword ptr [esp+8]; mov dword ptr [@O2], eax;' + EV(6) + 'mov eax, ecx; ret 8')
    # uh: a throw with no frame on the chain: the last step of the dispatch (UnhandledExceptionFilter, then ExitProcess(0xE06D7363))
    fn('uh', 'push ebp; mov ebp, esp; sub esp, 8; mov dword ptr [ebp-4], 42; push @TI_int; lea eax, [ebp-4]; push eax;' + THROW + 'mov eax, 0x66; mov esp, ebp; pop ebp; ret')
    # tm1: ?terminate@@YAXXZ with the default handler: abort()
    fn('tm1', 'call dword ptr [@S.?terminate@@YAXXZ]; mov eax, 0x66; ret')
    # td1: type_info destructor: free(name) with name = 0x12340000, returns normally (esp balanced): eax = 5
    dat('TIOBJ', [0, 0x12340000, 0])
    fn('td1', 'mov ecx, @TIOBJ; call dword ptr [@S.??1type_info@@UAE@XZ]; mov eax, 5; ret')

    # ---- setjmp / longjmp
    def sjchain(p, rv):
        """pa: _setjmp3(JB, 0) (returns to the label pa.j: EV1 then pb; after a jump EV5, O0 = eax); pb: SEH3 frame with a __finally (EV3) around pc (EV2); pc: longjmp(JB, rv)"""
        dat(p + 'JB', zeros(16)); dat(p + 'SC', [M, 0, p + 'bf'])
        fn(p + 'a', 'push ebp; mov ebp, esp; push 0; push @%sJB; call dword ptr [@S._setjmp3];' % p,
           j=('after', 'add esp, 8; test eax, eax; jnz Lj;' + EV(1) + 'call @%sb;' % p + EV(9) + 'jmp Le; Lj:' + EV(5) + 'mov dword ptr [@O0], eax; Le:' + RET_TR + 'mov esp, ebp; pop ebp; ret'))
        fn(p + 'b', SEH(p + 'SC') + EV(2) + TL(0) + 'call @%sc;' % p + EV(9) + TL(-1) + SEH_X)
        fn(p + 'bf', EV(3) + 'ret')
        fn(p + 'c', 'push %d; push @%sJB; call dword ptr [@S.longjmp];' % (rv, p) + EV(9) + 'ret')
    # sj1: setjmp at fs:[0] == -1, longjmp across two frames (pc, pb) with a __finally in pb: RtlUnwind to -1 runs it (EV3). TR = 0x1235, O0 = 7.
    #      jmp_buf: Registration = TryLevel = -1, Cookie 'VC20', Eip = the label sj1a.j
    sjchain('sj1', 7)
    # sj2: the same with retval 0: setjmp returns 1. TR = 0x1235, O0 = 1
    sjchain('sj2', 0)
    # sj3: _setjmp3(JB, 3, UnwindFunc, TryLevel 0, UnwindData0 0xAAAA) inside an SEH3 frame (a __finally EV6 that must NOT run: the UnwindFunc replaces _local_unwind2);
    #      longjmp(JB, 9) from the same frame: no global unwind (the registration is the head); sj3u(JB) (stdcall, EV2) runs; after the jump EV3. TR = 0x123, O0 = 9, O1 = JB (UnwindFunc argument),
    #      O2 = the frame address (Registration); JB: TryLevel 0, UnwindFunc = sj3u, UnwindData[0] = 0xAAAA
    dat('sj3JB', zeros(16)); dat('sj3SC', [M, 0, 'sj3f'])
    fn('sj3a', SEH('sj3SC') + 'lea eax, [ebp-16]; mov dword ptr [@O2], eax;' + TL(0) + 'push 0xAAAA; push 0; push @sj3u; push 3; push @sj3JB; call dword ptr [@S._setjmp3];',
       j=('after', 'add esp, 20; test eax, eax; jnz Lj;' + EV(1) + 'push 9; push @sj3JB; call dword ptr [@S.longjmp];' + EV(9) + 'jmp Le; Lj:' + EV(3) +
          'mov dword ptr [@O0], eax; Le:' + TL(-1) + RET_TR + SEH_X))
    fn('sj3u', EV(2) + 'mov eax, dword ptr [esp+4]; mov dword ptr [@O1], eax; ret 4'); fn('sj3f', EV(6) + 'ret')

    # ---- _XcptFilter: _XcptFilter(code, &{record, context}) with the handlers of the process's signal table (the harness fills fnvl_msvcrt_sighandlers: SIGSEGV -> xfh1,
    #      SIGFPE -> xfh2, SIGILL -> SIG_IGN). Expected: AV record: -1 and xfh1(11) runs once (O8 = 11; the slot is SIG_DFL afterwards: the second call gives 0);
    #      FPE record (STATUS_FLOAT_DIVIDE_BY_ZERO): -1, xfh2(8, _FPE_ZERODIVIDE 0x83) (O3 = 8, O4 = 0x83); ILLEGAL_INSTRUCTION with SIG_IGN: -1, no handler; breakpoint: 0
    for i, code in enumerate((0xC0000005, 0xC000008E, 0xC000001D, 0x80000003)): dat('XR%d' % i, [code] + zeros(19)); dat('XP%d' % i, ['XR%d' % i, 0])
    call = lambda i, o: 'push @XP%d; push 0; call dword ptr [@S._XcptFilter]; add esp, 8; mov dword ptr [@O%d], eax;' % (i, o)
    fn('xf1', call(0, 0) + call(0, 1) + call(1, 2) + call(2, 5) + call(3, 6) + 'ret')
    fn('xfh1', 'mov eax, dword ptr [esp+4]; mov dword ptr [@O8], eax; inc dword ptr [@O9]; ret')
    fn('xfh2', 'mov eax, dword ptr [esp+4]; mov dword ptr [@O3], eax; mov eax, dword ptr [esp+8]; mov dword ptr [@O4], eax; inc dword ptr [@O9]; ret')

    # ---- floating point control (x87 cw / MXCSR of the guest state)
    C = lambda a, b: 'push %d; push %d; call dword ptr [@S._controlfp]; add esp, 8;' % (b, a)       # _controlfp(newval a, mask b)
    ST = lambda o: 'mov dword ptr [@O%d], eax;' % o
    FI2 = lambda val, o: ('sub esp, 8; mov dword ptr [esp], 0; mov dword ptr [esp+4], 0x%x; fld qword ptr [esp]; fistp dword ptr [esp]; mov eax, dword ptr [esp]; add esp, 8;' % val) + ST(o)
    fn('fp1', C(0, 0) + ST(0)                                   # query: x87 0x27f -> 0x8001f | _PC_53 0x10000; MXCSR 0x1f80 -> 0x8001f; equal, so no _EM_AMBIGUOUS: 0x9001f
       + C(0x200, 0x300) + ST(1)                                              # _RC_UP: x87 0x9021f, MXCSR 0x8021f, together 0x9021f; x87 cw 0xa7f, MXCSR 0x5f80
       + FI2(0x40040000, 2)                                                   # fistp 2.5 with RC = up: 3
       + 'fnstcw word ptr [@O3]; stmxcsr dword ptr [@O4];'                    # 0x0a7f, 0x5f80
       + C(0x100, 0x300) + ST(5) + FI2(0xC0040000, 6)                         # _RC_DOWN: 0x9011f; fistp -2.5 -> -3
       + C(0x300, 0x300) + ST(7) + FI2(0x40040000, 8)                         # _RC_CHOP: 0x9031f; fistp 2.5 -> 2
       + C(0, 0x300) + ST(9) + FI2(0xC0040000, 10)                            # _RC_NEAR: 0x9001f; fistp -2.5 -> -2 (to even)
       + C(0x20000, 0x30000) + ST(11) + 'fnstcw word ptr [@O12];'             # _PC_24: x87 0xa001f (cw 0x007f), MXCSR unchanged 0x8001f: 0xa001f
       + C(0x10000, 0x30000) + ST(13)                                         # _PC_53 back: 0x9001f
       + C(0, 0x80000) + ST(14)                                               # mask _EM_DENORMAL is dropped by _controlfp: no change, 0x9001f
       + 'fldcw word ptr [@O15]; push 0; push 0; call dword ptr [@S._controlfp]; add esp, 8;' + ST(16)    # guest sets x87 RC = chop (cw 0x0c7f): x87 flags 0x8001f|0x300|_PC_24 0x20000 = 0xa031f; MXCSR 0x8001f -> RC differs: _EM_AMBIGUOUS
       + 'ret')                                                               # eax: 0x80000000 | 0xa031f | 0x8001f = 0x800a031f
    # fp2: _clearfp: the guest sets MXCSR sticky IE|PE (0x1f80|0x21), divides 1/0 on the x87 (ZE): returns 0x10|1 (SSE) | 8 (x87) = 0x19; the second call returns 0; MXCSR is 0x1f80, FSW sticky = 0
    dat('MXV', [0x1fa1]); dat('FDV', [0, 0x3ff00000, 0, 0]); dat('FDR', [0, 0])      # 1.0 and 0.0 from memory, the quotient stored (the host compiler cannot fold or drop the division)
    fn('fp2', 'ldmxcsr dword ptr [@MXV]; fld qword ptr [@FDV]; fld qword ptr [@FDV+8]; fdivp st(1), st; fstp qword ptr [@FDR]; fnstsw ax;' + ST(4) + 'call dword ptr [@S._clearfp];' + ST(0) + 'call dword ptr [@S._clearfp];' + ST(1) +
       'stmxcsr dword ptr [@O2]; fnstsw ax; and eax, 0x3f;' + ST(3) + 'ret')
    # fp3: _fpclass of the 10 classes, in the order of the _FPCLASS_ bits: SNAN 1, QNAN 2, NINF 4, NN 8, ND 0x10, NZ 0x20, PZ 0x40, PD 0x80, PN 0x100, PINF 0x200
    vals = [0x7FF0000000000001, 0x7FF8000000000000, 0xFFF0000000000000, 0xBFF0000000000000, 0x8000000000000001, 0x8000000000000000, 0, 1, 0x3FF0000000000000, 0x7FF0000000000000]
    dat('FPT', [w for v in vals for w in (v & M, v >> 32)]); dat('FPO', zeros(10))
    loop = lambda fnm, tab, outp: ('xor esi, esi; L%s: push dword ptr [esi*8+@%s+4]; push dword ptr [esi*8+@%s]; call dword ptr [@S.%s]; add esp, 8; mov dword ptr [esi*4+@%s], eax; inc esi; cmp esi, %d; jb L%s;'
                                   % (outp, tab, tab, fnm, outp, 10 if tab == 'FPT' else 6, outp))
    fn('fp3', 'push esi;' + loop('_fpclass', 'FPT', 'FPO') + 'pop esi; ret')
    # fp4: _finite and _isnan of 1.0, +inf, QNaN, SNaN, -0.0, the smallest denormal: finite 1,0,0,0,1,1; isnan 0,0,1,1,0,0
    vals2 = [0x3FF0000000000000, 0x7FF0000000000000, 0x7FF8000000000000, 0x7FF0000000000001, 0x8000000000000000, 1]
    dat('FQT', [w for v in vals2 for w in (v & M, v >> 32)]); dat('FQO', zeros(6)); dat('FNO', zeros(6))
    fn('fp4', 'push esi;' + loop('_finite', 'FQT', 'FQO') + 'pop esi; push esi;' + loop('_isnan', 'FQT', 'FNO') + 'pop esi; ret')
    return P


ENTRY = {'s2': 's2o', 's4': 's4o', 'cx1': 'cx1a', 'cx2': 'cx2a', 'cx3': 'cx3a', 'cx5': 'cx5a', 'cx6': 'cx6a', 'cx7': 'cx7a', 'sj1': 'sj1a', 'sj2': 'sj2a', 'sj3': 'sj3a'}


def cases(P):
    """(root, what, want eax or None, stop, {data name or O index: expected words})"""
    A = P.addr; O = lambda *w: {'O': list(w)}
    return [
        ('s1', '__except, filter 1: body, filter (reads the code through [ebp-0x14]), __except block', 0x123, 'ret', {'O0': [0xC0000094]}),
        ('s2', 'inner filter 0, outer filter 1: search order, global unwind', 0x12345, 'ret', {}),
        ('s3', 'filter -1 after fixing Ecx in the CONTEXT: the faulting div runs again', 0x123, 'ret', {'O0': [2]}),
        ('s4', 'nested __finally x3 unwound in order by an outer __except', 0x123456, 'ret', {}),
        ('cx1', 'throw int, catch(int) two frames up, unwind-map action between', 0x12345, 'ret', {'O0': [42]}),
        ('cx2', 'catch(char) skipped (type names compared), catch(...) takes it', 0x12345, 'ret', {}),
        ('cx3', 'rethrow from a catch block; object destroyed once after the outer catch', 0x12345, 'ret', {'O0': [42], 'O1': [42], 'O2': [1]}),
        ('cx5', 'throw; with nothing being handled -> terminate -> abort', None, 'abort', {'O0': [42], 'TR': [0x1234]}),
        ('cx6', 'catch(B&): reference to the base subobject (this_offset 8)', 0x12345, 'ret', {}),
        ('cx7', 'catch(B) by value: copy constructor (this, src, 1)', 0x126345, 'ret', {'O0': [0x33], 'O1': [0x44], 'O2': [1]}),
        ('uh', 'throw without a handler: UnhandledExceptionFilter, ExitProcess(0xE06D7363)', None, 'exit', {}),
        ('tm1', '?terminate@@YAXXZ -> abort', None, 'abort', {}),
        ('td1', 'type_info destructor frees the name', 5, 'ret', {}),
        ('sj1', 'setjmp / longjmp over two frames, __finally between, retval 7', 0x1235, 'ret', {'O0': [7]}),
        ('sj2', 'longjmp with retval 0: setjmp returns 1', 0x1235, 'ret', {'O0': [1]}),
        ('sj3', '_setjmp3 with UnwindFunc / TryLevel / UnwindData, longjmp calls the UnwindFunc', 0x123, 'ret', {'O0': [9]}),
        ('xf1', '_XcptFilter with signal handlers', None, 'ret', {'O0': [M], 'O1': [0], 'O2': [M], 'O3': [8], 'O4': [0x83], 'O5': [M], 'O6': [0], 'O8': [11], 'O9': [2]}),
        ('fp1', '_controlfp round trips, rounding mode on fistp, precision, ambiguity', 0x800a031f, 'ret',
         {'O0': [0x9001f], 'O1': [0x9021f], 'O2': [3], 'O3': [0x0a7f], 'O4': [0x5f80], 'O5': [0x9011f], 'O6': [(-3) & M], 'O7': [0x9031f], 'O8': [2], 'O9': [0x9001f], 'O10': [(-2) & M],
          'O11': [0xa001f], 'O12': [0x007f], 'O13': [0x9001f], 'O14': [0x9001f], 'O16': [0x800a031f]}),
        ('fp2', '_clearfp returns and clears the sticky flags of x87 and MXCSR', 0, 'ret', {'O0': [0x19], 'O1': [0], 'O2': [0x1f80], 'O3': [0], 'O4': [0x4]}),
        ('fp3', '_fpclass: all 10 classes', None, 'ret', {'FPO': [1, 2, 4, 8, 0x10, 0x20, 0x40, 0x80, 0x100, 0x200]}),
        ('fp4', '_finite / _isnan', None, 'ret', {'FQO': [1, 0, 0, 0, 1, 1], 'FNO': [0, 0, 1, 1, 0, 0]}),
    ]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--work', default='/home/user/l3work/msvcrttest'); ap.add_argument('--only'); a = ap.parse_args(); w = a.work
    os.makedirs(w, exist_ok=True)
    P = program(); P.build()
    # the runtime: a copy of rt/ whose struct cpu has the `msvcrt` words when the real one does not yet
    rt = w + '/rt'; shutil.rmtree(rt, ignore_errors=True); shutil.copytree(LIFT + '/rt', rt)
    h = open(rt + '/fnvl.h').read()
    if 'msvcrt' not in h:
        assert 'u32 fip, fdp; };' in h; open(rt + '/fnvl.h', 'w').write(h.replace('u32 fip, fdp; };', 'u32 fip, fdp; u32 msvcrt[6]; };', 1))
    # every label a non-local transfer can land on is also an entry of the universe (the exe's EH tables make them `eh-cont` / `seh-handler` entries): the dispatcher serves it
    # from an activation that is not the function's own (an __except block is entered with a deep esp: the block restores esp itself)
    labels = [(P.addr[n], l) for n in P.order for l in P.owns[n]]
    img = Fake(P.code); starts = {P.addr[n] for n in P.order} | {l for _, l in labels}; img.entries = set(starts)
    texts = [L.lift(img, P.addr[n], SLOT, owns=P.owns[n])[0] for n in P.order] + [L.lift(img, a, SLOT, start=l, owns=[l])[0] for a, l in labels]
    flags = [f for f in instest.FLAGS if f != '-DFNVL_FLAGS_IO'] + ['-I', rt]
    CF = ['-mabi=ilp32', '-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-fno-stack-clash-protection', '-w', '-c']
    CXXF = ['aarch64-linux-gnu-g++'] + flags + ['-x', 'c++']; CCF = ['aarch64-linux-gnu-gcc'] + CF
    jobs = [(CXXF, rt + '/seh.cc'), (CXXF, rt + '/enter.cc'), (CXXF, rt + '/unhandled.cc')] + [(CXXF, rt + '/' + f) for f in RUNTIME] + \
           [(CCF, rt + '/rt.c'), (CCF, rt + '/rcptab.c'), (CCF + ['-I', rt], rt + '/x87.c'), (CCF + ['-fno-builtin'], rt + '/fmath.c')]
    objs = []
    for cmd, src in jobs:
        o = '%s/%s.o' % (w, os.path.basename(src).rsplit('.', 1)[0]); objs.append(o)
        r = subprocess.run(cmd + [src, '-o', o], capture_output=True, text=True)
        if r.returncode: sys.exit('compile %s: %s' % (src, r.stderr[:800]))
    rtdef = set()
    for o in objs:
        for l in subprocess.run(['aarch64-linux-gnu-nm', '-g', '--defined-only', o], capture_output=True, text=True).stdout.splitlines():
            if l.split(): rtdef.add(l.split()[-1])
    open(w + '/bridge.tsv', 'w').write('dll\tname\tordinal\tconv\tret\targs\tnative_symbol\tbind\n' + ''.join('%s\t%s\t\t%s\t%s\t%s\t%s\t%s\n' % r for r in BRIDGE))
    open(w + '/need.tsv', 'w').write(''.join('%08x\t%s\t%s\tkey\t%08x\n' % (SLOTS[r[1]], r[0], r[1], KEY[r[1]]) for r in BRIDGE))
    ent, blocked, hit, absent = impgen.generate(w + '/bridge.tsv', w, [w + '/need.tsv'], None, rtdef)
    bad = 0
    ok = len(ent) == len(BRIDGE) and not blocked and not hit and not absent and all(s in rtdef for s in EH_KEYS.values())
    print('rows  %s: %d l3 rows have a body in the runtime objects (blocked %d), the 2 registration entries are defined' % ('equal' if ok else 'DIFF', len(ent), len(blocked))); bad += not ok
    syms = {x: 'L_%08x' % x for x in starts}; syms.update({k: s for k, s, *_ in ent}); syms.update(EH_KEYS)
    assert {n: k for k, s, d, n, b in ent} == {r[1]: KEY[r[1]] for r in BRIDGE}
    open(w + '/guest.cpp', 'w').write(L.PRELUDE + '\n'.join(texts)); open(w + '/native.c', 'w').write(NATIVE)
    hh = phf.build(list(syms)); open(w + '/tab.S', 'w').write(phf.table_asm(syms, hh))
    for cmd, src in [(CXXF, w + '/guest.cpp'), (CXXF, w + '/imp.cpp'), (CCF, w + '/tab.S'), (CCF, w + '/native.c')]:
        o = '%s/%s.o' % (w, os.path.basename(src).rsplit('.', 1)[0]); objs.append(o)
        r = subprocess.run(cmd + [src, '-o', o], capture_output=True, text=True)
        if r.returncode: sys.exit('compile %s: %s' % (src, r.stderr[:800]))
    elf = w + '/msvcrt.elf'
    r = subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64elf32', '-Ttext=0x%x' % TEXT, '-e', 'test_main', '-o', elf] + objs, capture_output=True, text=True)
    if r.returncode: sys.exit('link: ' + r.stderr[:1200])
    with open(elf, 'rb') as fh:
        e = ELFFile(fh); segs = [(p['p_vaddr'], p.data(), p['p_memsz']) for p in e.iter_segments() if p['p_type'] == 'PT_LOAD']
        sym = {x.name: x['st_value'] for x in e.get_section_by_name('.symtab').iter_symbols() if x.name}
    A = P.addr; rdw = lambda uc, ad, n=1: list(struct.unpack('<%dI' % n, bytes(uc.mem_read(ad, 4 * n))))
    for root, what, want, stop, mem in cases(P):
        if a.only and root != a.only: continue
        uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        lo = min(v for v, d, m in segs) & ~0xfff; hi = (max(v + m for v, d, m in segs) + 0xffff) & ~0xfff; uc.mem_map(lo, hi - lo)
        for v, d, m in segs: uc.mem_write(v, d)
        uc.mem_map(0x400000, CODE_END - 0x400000 + 0x20000); uc.mem_write(BASE, P.code); uc.mem_write(IAT, b''.join(struct.pack('<I', KEY[r[1]]) for r in BRIDGE))
        uc.mem_write(DATA, P.data_image())
        uc.mem_map(GSTACK, 0x20000); uc.mem_map(HSTACK, 0x40000); uc.mem_map(STOP, 0x1000); uc.mem_map(TEB, 0x1000); uc.mem_write(TEB, b'\xff\xff\xff\xff')
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20); uc.reg_write(UC_ARM64_REG_SP, HSTACK + 0x3f000); uc.reg_write(UC_ARM64_REG_X30, STOP)
        if root == 'xf1': uc.mem_write(sym['fnvl_msvcrt_sighandlers'] + 4 * 11, struct.pack('<I', A['xfh1'])); uc.mem_write(sym['fnvl_msvcrt_sighandlers'] + 4 * 8, struct.pack('<I', A['xfh2'])); uc.mem_write(sym['fnvl_msvcrt_sighandlers'] + 4 * 4, struct.pack('<I', 1))
        if root == 'fp1': uc.mem_write(A['O'] + 4 * 15, struct.pack('<I', 0x0c7f))
        until = {'ret': STOP, 'abort': sym['t_abort'], 'exit': sym['ExitProcess']}[stop]
        for i, v in enumerate((A[ENTRY.get(root, root)], GSTACK + 0x1ff00, TEB)): uc.reg_write(UC_ARM64_REG_X0 + i, v)
        err = ''
        try: uc.emu_start(sym['test_main'], until, count=6000000)
        except UcError as ex: err = str(ex)
        got = uc.reg_read(UC_ARM64_REG_X0) & M; why = []
        if uc.reg_read(UC_ARM64_REG_PC) != until and not err: why.append('did not reach %s (pc %x)' % (stop, uc.reg_read(UC_ARM64_REG_PC)))
        if err: why.append(err)
        if rdw(uc, sym['g_ue_n'])[0] and stop != 'exit': why.append('unhandled exception %x at %x' % (rdw(uc, sym['g_ue_code'])[0], rdw(uc, sym['g_ue_addr'])[0]))
        if uc.reg_read(UC_ARM64_REG_PC) == sym.get('fnvl_nobody'): why.append('fnvl_nobody: missed address %x' % rdw(uc, uc.reg_read(UC_ARM64_REG_X27) + 248)[0])
        if want is not None and stop == 'ret' and got != want: why.append('eax %x want %x' % (got, want))
        if stop == 'exit' and got != 0xE06D7363: why.append('ExitProcess(%x)' % got)
        if stop == 'exit' and (rdw(uc, sym['g_ue_n'])[0] != 1 or rdw(uc, sym['g_ue_code'])[0] != 0xE06D7363): why.append('UnhandledExceptionFilter n %d code %x' % tuple(rdw(uc, sym['g_ue_n']) + rdw(uc, sym['g_ue_code'])))
        if root == 'td1' and rdw(uc, sym['g_free_arg'])[0] != 0x12340000: why.append('free(%x)' % rdw(uc, sym['g_free_arg'])[0])
        for k, v in mem.items():
            if k == 'TR': base, want_w = A['TR'], v
            elif re.fullmatch(r'O\d+', k): base, want_w = A['O'] + 4 * int(k[1:]), v
            elif k in A and isinstance(v, list): base, want_w = A[k], v
            else: continue
            g = rdw(uc, base, len(want_w))
            if g != want_w: why.append('%s %s want %s' % (k, ['%x' % x for x in g], ['%x' % x for x in want_w]))
        if root == 'cx6':
            o0, o1 = rdw(uc, A['O'], 2)
            if o0 != o1 + 8: why.append('O0 %x want O1 + 8 = %x' % (o0, o1 + 8))
        if root in ('sj1', 'sj3'):
            jb = rdw(uc, A[root + 'JB'], 16)
            if root == 'sj1' and (jb[6] != M or jb[7] != M or jb[8] != 0x56433230 or jb[9] != 0 or jb[5] != A['sj1a.j']): why.append('jmp_buf %s' % ['%x' % x for x in jb])
            if root == 'sj3':
                o2 = rdw(uc, A['O'] + 8)[0]; o1 = rdw(uc, A['O'] + 4)[0]
                if jb[6] != o2 or jb[7] != 0 or jb[8] != 0x56433230 or jb[9] != A['sj3u'] or jb[10] != 0xAAAA or jb[5] != A['sj3a.j'] or o1 != A['sj3JB']: why.append('jmp_buf %s O1 %x' % (['%x' % x for x in jb], o1))
        if stop == 'ret' and root not in ('xf1', 'fp3', 'fp4') and not err and uc.reg_read(UC_ARM64_REG_SP) != HSTACK + 0x3f000: why.append('host sp')
        bad += bool(why)
        print('%-4s %-6s %s' % (root, 'DIFF' if why else 'equal', what + ('' if not why else '  <- ' + '; '.join(why))))
    sys.exit(1 if bad else 0)


from unicorn.arm64_const import UC_ARM64_REG_PC
if __name__ == '__main__': main()
