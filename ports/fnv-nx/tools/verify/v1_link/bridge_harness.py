#!/usr/bin/env python3
"""bridge_harness.py - V1-link: run a reference declaration against a definition (through a bridge or directly)
under qemu-aarch64 and compare what the definition receives / the caller gets back with what MSVC x86 would deliver.

Independent of tools/link/abi*.py: the x86 model below was written from the MSVC 2008 oracle (oracle/x86conv.cpp,
docs/VERIFY_V1_LINK.md "x86 facts"), MSVC names are parsed here from llvm-undname text, and the expected bytes come
from an x86 argument image (ecx, edx, stack bytes with a definedness mask), not from AArch64 locations.

A case: ref / def MSVC decorated names, aggregate layouts {type name: Layout-like (size, align, trivial, cls, pod)},
optional bridge asm (its branch target is renamed to the harness callee). Result per case: 'pass', 'FAIL ...',
'skip ...' (shape the harness cannot express), plus notes on bytes x86 leaves undefined (not checked).
"""
import os
import random
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import qemu_ilp32  # noqa: E402

UNDNAME = os.environ.get('UNDNAME', 'llvm-undname')
CCS = ('__cdecl', '__stdcall', '__fastcall', '__thiscall')


class Skip(Exception):
    pass


# ------------------------------------------------------------------------------------------------ MSVC signatures

def undname(names):
    p = subprocess.run([UNDNAME], input='\n'.join(names) + '\n', capture_output=True, text=True)
    out, res, lines = {}, p.stdout.split('\n'), None
    lines = p.stdout.split('\n')
    i = 0
    for n in names:
        while i < len(lines) and lines[i] != n:
            i += 1
        out[n] = lines[i + 1] if i + 1 < len(lines) else None
        i += 2
    return out


def split_top(s, sep=','):
    out, depth, cur = [], 0, ''
    for c in s:
        if c in '(<[':
            depth += 1
        elif c in ')>]':
            depth -= 1
        if c == sep and depth == 0:
            out.append(cur.strip()); cur = ''
        else:
            cur += c
    if cur.strip():
        out.append(cur.strip())
    return out


class Sig:
    def __init__(self, text):
        self.text = text
        s = text
        m = re.match(r'^(public|protected|private): ', s)
        self.access = m.group(1) if m else None
        if m:
            s = s[m.end():]
        self.static = self.virtual = False
        while True:
            if s.startswith('static '):
                self.static = True; s = s[7:]
            elif s.startswith('virtual '):
                self.virtual = True; s = s[8:]
            else:
                break
        depth, at = 0, None
        for i, c in enumerate(s):
            if c in '(<[':
                depth += 1
            elif c in ')>]':
                depth -= 1
            elif depth == 0:
                for cc in CCS:
                    if s.startswith(cc + ' ', i) and (i == 0 or s[i - 1] == ' '):
                        at = (i, cc); break
                if at:
                    break
        if at is None:
            raise Skip('not a function: ' + text)
        self.ret = s[:at[0]].strip() or 'void'
        self.cc = at[1][2:]
        rest = s[at[0] + len(at[1]):].strip()
        depth = 0
        for i, c in enumerate(rest):
            if c == '<':
                depth += 1
            elif c == '>':
                depth -= 1
            elif c == '(' and depth == 0:
                break
        self.name = rest[:i]
        j, depth = i, 0
        for j in range(i, len(rest)):
            if rest[j] == '(':
                depth += 1
            elif rest[j] == ')':
                depth -= 1
                if depth == 0:
                    break
        inner = rest[i + 1:j].strip()
        self.params = [] if inner in ('', 'void') else split_top(inner)
        self.member = self.access is not None and not self.static
        last = self.name.split('::')[-1]
        self.ctor_dtor = last.startswith('~') or (len(self.name.split('::')) > 1 and
                                                  re.sub('<.*', '', last) == re.sub('<.*', '', self.name.split('::')[-2]))
        if self.ctor_dtor:
            self.ret = 'void'


# ------------------------------------------------------------------------------------------------ types

class Ty:
    """kind: int (size 1/2/4/8), bool, ptr, f32, f64, agg, void."""

    def __init__(self, kind, size, align=None, name=None, lay=None):
        self.kind, self.size, self.align, self.name, self.lay = kind, size, align or min(size, 8), name, lay

    def __repr__(self):
        return '%s%d' % (self.kind, self.size) if self.kind != 'agg' else 'agg(%s,%d)' % (self.name, self.size)


SCALARS = {'bool': ('bool', 1), 'char': ('int', 1), 'signed char': ('int', 1), 'unsigned char': ('int', 1),
           'short': ('int', 2), 'unsigned short': ('int', 2), 'wchar_t': ('int', 2), 'int': ('int', 4),
           'unsigned int': ('int', 4), 'long': ('int', 4), 'unsigned long': ('int', 4), '__int64': ('int', 8),
           'unsigned __int64': ('int', 8), 'float': ('f32', 4), 'double': ('f64', 8), 'long double': ('f64', 8),
           'void': ('void', 0)}


def ty_of(t, layouts):
    t = ' '.join(t.replace('__ptr64', '').split())
    if '::*' in t:
        raise Skip('member pointer: ' + t)
    if t == '...':
        raise Skip('varargs')
    if t.endswith(('*', '&')) or re.search(r'[*&]\s*(const|volatile)\s*$', t) or re.search(r'\(\s*(__\w+\s*)?\*', t):
        return Ty('ptr', 4)
    t2 = re.sub(r'\b(const|volatile)\b', '', t)
    t2 = ' '.join(t2.split())
    if t2.startswith('enum '):
        return Ty('int', 4)
    if t2 in SCALARS:
        k, s = SCALARS[t2]
        return Ty(k, s)
    m = re.match(r'^(class|struct|union) (.*)$', t2)
    if m:
        name = m.group(2)
        lay = layouts.get(name)
        if lay is None:
            raise Skip('no layout for ' + name)
        return Ty('agg', lay.size, lay.align, name, lay)
    raise Skip('type ' + t)


# ------------------------------------------------------------------------------------------------ x86 model (MSVC 2008)

def x86_hidden(sig, rt):
    """MSVC returns aggregates through a hidden buffer for member functions always, for others unless a C++03 POD
    of 1/2/4/8 bytes (oracle: user ctor / private member / base class -> hidden)."""
    return rt.kind == 'agg' and (sig.member or not (rt.lay.pod and rt.size in (1, 2, 4, 8)))


def x86_args(sig, ptys, rt):
    """-> [(label, loc, size)], loc 'ecx' / 'edx' / ('stk', off). labels: 'this', 'sret', 'p<i>'."""
    vals = ([('this', Ty('ptr', 4))] if sig.member else []) + [('p%d' % i, t) for i, t in enumerate(ptys)]
    hidden = x86_hidden(sig, rt)
    out, off = [], 0

    def stk(label, size):
        nonlocal off
        out.append((label, ('stk', off), size)); off += (size + 3) // 4 * 4
    if sig.cc == 'thiscall':
        if not sig.member:
            raise Skip('thiscall non-member')
        out.append(('this', 'ecx', 4))
        if hidden:
            stk('sret', 4)
        for lab, t in vals[1:]:
            stk(lab, t.size)
    elif sig.cc == 'fastcall':
        if sig.member:
            raise Skip('fastcall member')
        regs = ['ecx', 'edx']
        if hidden and not rt.lay.pod:
            # MSVC 2008 cl /O2 (S1b a9fbc1b; V1 tests/v1/test_fastcall_nonpod.py): a non-POD's hidden pointer is ecx
            # and the arguments move one place
            out.append(('sret', regs.pop(0), 4))
        elif hidden:
            stk('sret', 4)          # oracle: a POD's hidden pointer is the first stack word, not ecx
        for lab, t in vals:
            if regs and t.kind in ('int', 'bool', 'ptr') and t.size <= 4:
                out.append((lab, regs.pop(0), t.size))
            else:
                stk(lab, t.size)
    else:
        if sig.member:
            stk('this', 4)
            if hidden:
                stk('sret', 4)      # oracle: [this][sret][args] for __stdcall / __cdecl members
            for lab, t in vals[1:]:
                stk(lab, t.size)
        else:
            if hidden:
                stk('sret', 4)
            for lab, t in vals:
                stk(lab, t.size)
    return out, hidden


def x86_ret_container(sig, rt):
    if rt.kind == 'void':
        return None
    if rt.kind in ('f32', 'f64'):
        return 'st0'
    if rt.kind == 'agg':
        return 'buf' if x86_hidden(sig, rt) else 'edx:eax'
    return 'edx:eax'


# ------------------------------------------------------------------------------------------------ harness C++

def ctype(t, k):
    if t.kind == 'void':
        return 'void'
    if t.kind == 'bool':
        return 'bool'
    if t.kind == 'int':
        return {1: 'unsigned char', 2: 'unsigned short', 4: 'unsigned int', 8: 'unsigned long long'}[t.size]
    if t.kind == 'ptr':
        return 'void *'
    if t.kind == 'f32':
        return 'float'
    if t.kind == 'f64':
        return 'double'
    return 'A%d' % k


def agg_decl(t, k):
    lay = t.lay
    c = lay.cls
    al = 'alignas(%d) ' % lay.align
    if c.startswith('H') and c != 'H?':
        n, w = map(int, c[1:].split('x'))
        el = {4: 'float', 8: 'double', 2: '_Float16', 16: '__attribute__((vector_size(16))) float'}[w]
        if n * w != lay.size:
            raise Skip('HFA with padding')
        return 'struct %sA%d { %s f[%d]; };' % (al, k, el, n)
    if lay.trivial:
        return 'struct %sA%d { unsigned char b[%d]; };' % (al, k, lay.size)
    return ('struct %sA%d { unsigned char b[%d]; A%d() {} A%d(const A%d &o) { __builtin_memcpy(b, o.b, %d); } ~A%d() {} };'
            % (al, k, lay.size, k, k, k, lay.size, k))


def rand_val(t, rng, edge):
    """Bytes of a value of t. edge: None -> random (the default: distinct values per parameter, so a swapped or
    missing move shows); an int -> index into the edge-value tables (all parameters of a kind get the same edge
    value, which hides swaps: only for extension checks)."""
    if t.kind == 'bool':
        return bytes([rng.getrandbits(1) if edge is None else edge % 2])
    if t.kind == 'int':
        edges = [0, (1 << (8 * t.size)) - 1, 1 << (8 * t.size - 1), (1 << (8 * t.size - 1)) - 1]
        v = edges[edge % 4] if edge is not None and edge < 4 else rng.getrandbits(8 * t.size)
        return v.to_bytes(t.size, 'little')
    if t.kind == 'ptr':
        # distinct 16-byte aligned addresses inside qemu_ilp32's zeroed arena: valid memory, so a bridge that routes
        # a result to the wrong pointer is reported as a wrong result (out buffer / result bytes), not a crash
        return (qemu_ilp32.POOL + (rng.randrange(0, (qemu_ilp32.POOL_SIZE - 4096) // 16) * 16)).to_bytes(4, 'little')
    if t.kind == 'f32':
        edges = [0x80000000, 0x00000001, 0x7f800000, 0x7fc12345, 0x3fc00000]
        return (edges[edge % 5] if edge is not None and edge < 5 else rng.getrandbits(32)).to_bytes(4, 'little')
    if t.kind == 'f64':
        edges = [0x8000000000000000, 0x0000000000000001, 0x7ff0000000000000, 0x7ff8123456789abc, 0x3ff8000000000000]
        return (edges[edge % 5] if edge is not None and edge < 5 else rng.getrandbits(64)).to_bytes(8, 'little')
    if t.kind == 'agg':
        if t.lay.cls.startswith('H'):
            n, w = map(int, t.lay.cls[1:].split('x'))
            if w in (4, 8):
                fmt = '<f' if w == 4 else '<d'
                return b''.join(struct.pack(fmt, rng.uniform(-1e6, 1e6)) for _ in range(n))
        return bytes(rng.getrandbits(8) for _ in range(t.size))
    raise Skip(t.kind)


def cinit(b):
    return '{' + ','.join('0x%02x' % x for x in b) + '}'


def run_case(ref_name, def_name, layouts_ref, layouts_def, bridge_asm, work, seed=1, dem=None, edge=None,
             def_target_re=None):
    """-> (status, details). bridge_asm None: the reference calls the definition directly (pair judged same)."""
    dem = dem or undname([ref_name, def_name])
    rs, ds = Sig(dem[ref_name]), Sig(dem[def_name])
    rp = [ty_of(t, layouts_ref) for t in rs.params]
    dp = [ty_of(t, layouts_def) for t in ds.params]
    rr, dr = ty_of(rs.ret, layouts_ref), ty_of(ds.ret, layouts_def)
    rng = random.Random(seed)
    rx, rhid = x86_args(rs, rp, rr)
    dx, dhid = x86_args(ds, dp, dr)
    # ---- x86 image of the reference call
    regs = {'ecx': [None] * 4, 'edx': [None] * 4}
    stack = {}
    rvals = {}
    rtys = dict([('this', Ty('ptr', 4))] if rs.member else [])
    rtys.update(('p%d' % i, t) for i, t in enumerate(rp))
    for lab, t in rtys.items():
        rvals[lab] = rand_val(t, rng, edge)
    # which reference pointer receives the definition's hidden result (x86 sret-explicit): it gets a real buffer
    def_sret_loc = next((loc for lab, loc, _ in dx if lab == 'sret'), None)
    out_param = None
    for lab, loc, size in rx:
        if lab == 'sret':
            vb = ['SRET'] * 4
        else:
            b = rvals[lab]
            vb = list(b) + [None] * ((size + 3) // 4 * 4 - len(b))
            if loc == def_sret_loc and size == 4 and rtys[lab].kind in ('ptr', 'int', 'f32'):
                # the definition's hidden result pointer is this reference value on x86: give it the bits of a
                # real buffer's address (whatever its declared type), so the bridge's routing can be checked
                out_param = lab
                vb = ['OUT'] * 4
        if loc in regs:
            regs[loc] = (vb + [None] * 4)[:4]
        else:
            for k, x in enumerate(vb):
                stack[loc[1] + k] = x

    def x86_bytes(loc, size):
        if loc in regs:
            return regs[loc][:size]
        return [stack.get(loc[1] + k) for k in range(size)]
    # ---- expectations for each definition value
    dtys = dict([('this', Ty('ptr', 4))] if ds.member else [])
    dtys.update(('p%d' % i, t) for i, t in enumerate(dp))
    expect, notes, def_out = {}, [], None
    def_gets_refsret = None
    for lab, loc, size in dx:
        got = x86_bytes(loc, size)
        if lab == 'sret':
            if all(x == 'SRET' for x in got):
                continue                                   # both hidden: compared through the results
            if all(x == 'OUT' for x in got):
                def_out = out_param; continue
            notes.append('def hidden result pointer not passed (x86 junk)'); continue
        if all(x == 'SRET' for x in got):
            def_gets_refsret = lab                         # explicit pointer = the reference's result buffer
            continue
        if any(x in ('SRET', 'OUT') for x in got):
            notes.append('%s: mixes a result pointer' % lab); continue
        if all(x is None for x in got):
            notes.append('%s not passed (x86 junk)' % lab); continue
        expect[lab] = got
    # ---- results
    rcont, dcont = x86_ret_container(rs, rr), x86_ret_container(ds, dr)
    rv = rand_val(dr, rng, edge) if dr.kind != 'void' else b''
    exp_res = None          # list of bytes / None (no check) for the reference's result
    exp_out = None          # bytes expected in the out buffer
    if def_out is not None:
        exp_out = list(rv)
        if rr.kind == 'ptr':
            exp_res = 'OUTPTR'
    elif def_gets_refsret is not None:
        exp_res = list(rand_pattern(rr.size)) if rr.kind == 'agg' else None
    elif rcont is None:
        pass
    elif dcont is None:
        notes.append('reference reads a result the definition does not produce (x86 junk)')
    elif rcont == 'st0' and dcont == 'st0':
        if rr.kind == dr.kind:
            exp_res = list(rv)
        elif rr.kind == 'f64':   # x87: the float widens exactly
            exp_res = list(struct.pack('<d', struct.unpack('<f', rv)[0]))
        else:
            exp_res = list(f32_round(struct.unpack('<d', rv)[0]))   # fstp dword: rounds, overflow -> inf
    elif 'st0' in (rcont, dcont):
        notes.append('result: %s read from %s (x86-inconsistent, junk)' % (rcont, dcont))
    elif rcont == 'buf' and dcont == 'buf':
        exp_res = list(rv[:rr.size]) + [None] * max(0, rr.size - len(rv))
    elif rcont == 'buf' or dcont == 'buf':
        notes.append('result: buffer vs registers (x86-inconsistent)')
    else:   # edx:eax both
        dbytes = list(rv) + [None] * 8
        if dr.kind in ('int', 'bool') and dr.size < 4:
            dbytes = list(rv) + [None] * 8
        exp_res = dbytes[:rr.size]
    # ---- C++ sources
    decls = []
    k = [0]

    def ct(t):
        if t.kind == 'agg':
            k[0] += 1
            decls.append(agg_decl(t, k[0]))
            return 'A%d' % k[0]
        return ctype(t, 0)
    rpar = ([('this', Ty('ptr', 4))] if rs.member else []) + [('p%d' % i, t) for i, t in enumerate(rp)]
    dpar = ([('this', Ty('ptr', 4))] if ds.member else []) + [('p%d' % i, t) for i, t in enumerate(dp)]
    r_ct = [(lab, ct(t), t) for lab, t in rpar]
    rret_ct = ct(rr)
    d_ct = [(lab, ct(t), t) for lab, t in dpar]
    dret_ct = ct(dr)
    src = ['extern "C" void v1_hex(const void *, unsigned long); extern "C" void v1_puts(const char *);',
           'extern "C" void *memcpy(void *, const void *, unsigned long);'] + decls
    src.append('unsigned char v1_got[%d][64]; unsigned char v1_res[64]; unsigned char v1_outbuf[64];'
               % max(1, len(d_ct)))
    src.append('unsigned char v1_rv[64] = %s;' % cinit(rv + b'\0'))
    # callee (the definition)
    args = ', '.join('%s v_%s' % (c, lab) for lab, c, _ in d_ct)
    body = []
    for i, (lab, c, t) in enumerate(d_ct):
        body.append('memcpy(v1_got[%d], &v_%s, %d);' % (i, lab, t.size if t.kind != 'agg' else t.lay.size))
    if def_gets_refsret is not None:
        body.append('{ unsigned char pat[64] = %s; memcpy((void *)v_%s, pat, %d); }'
                    % (cinit(rand_pattern(max(rr.size, 1))), def_gets_refsret, max(rr.size, 1)))
    if dr.kind != 'void':
        body.append('%s r; memcpy(&r, v1_rv, sizeof r); return r;' % dret_ct)
    src.append('extern "C" __attribute__((noinline)) %s v1_def(%s) { %s }' % (dret_ct, args, ' '.join(body)))
    # caller (the reference)
    args = ', '.join('%s' % c for _, c, _ in r_ct)
    src.append('extern "C" %s v1_ref(%s);' % (rret_ct, args))
    init, call_args = [], []
    for lab, c, t in r_ct:
        if lab == out_param:
            init.append('%s %s_v; { void *pp = v1_outbuf; memcpy(&%s_v, &pp, 4); }' % (c, lab, lab))
        else:
            init.append('%s %s_v; { unsigned char b[] = %s; memcpy(&%s_v, b, sizeof b); }' % (c, lab, cinit(rvals[lab]), lab))
        call_args.append('%s_v' % lab)
    callx = 'v1_ref(%s)' % ', '.join(call_args)
    if rr.kind != 'void':
        callx = '{ %s r = %s; memcpy(v1_res, &r, %d); }' % (rret_ct, callx, rr.size)
    else:
        callx += ';'
    src.append('int main() { %s %s ' % (' '.join(init), callx) +
               ' '.join('v1_puts("G%d "); v1_hex(v1_got[%d], 64); v1_puts("\\n");' % (i, i) for i in range(len(d_ct))) +
               ' v1_puts("R "); v1_hex(v1_res, 64); v1_puts("\\nO "); v1_hex(v1_outbuf, 64); v1_puts("\\nP "); '
               'void *ob = v1_outbuf; v1_hex(&ob, 4); v1_puts("\\n"); return 0; }')
    os.makedirs(work, exist_ok=True)
    cpp = os.path.join(work, 'case.cpp')
    with open(cpp, 'w') as f:
        f.write('\n'.join(src) + '\n')
    asm = os.path.join(work, 'bridge.S')
    extra, ldf = (), ()
    if isinstance(bridge_asm, dict):
        # the bridge exactly as a link built it: {'obj': .text of the link's zz_bridges.o, 'sym': bridge symbol,
        # 'target': the __fnv_ symbol it calls}; the caller's v1_ref is the bridge, its target is v1_def
        extra = (bridge_asm['obj'],) + ((bridge_asm['provide'],) if bridge_asm.get('provide') else ())
        ldf = ('--defsym=v1_ref=' + bridge_asm['sym'], '--defsym=%s=v1_def' % bridge_asm['target'],
               '--unresolved-symbols=ignore-all', '--no-warn-mismatch')
        bridge_asm = 'OBJ'
    with open(asm, 'w') as f:
        if bridge_asm == 'OBJ':
            f.write('    .text\n')
        elif bridge_asm is None:
            f.write('    .text\n    .globl v1_ref\n    .type v1_ref, %function\nv1_ref:\n    b v1_def\n')  # no register touched
        else:
            text = re.sub(r'//.*', '', bridge_asm)
            name = re.search(r'^(\S+):', text, re.M).group(1)
            text = text.replace(name, 'v1_ref')
            text = re.sub(r'\b(b|bl)\s+(__fnv_[0-9a-f]{8}\w*|\w+)\s*$', r'\1 v1_def', text, flags=re.M)
            f.write('    .text\n' + text)
    exe = qemu_ilp32.build([cpp, asm], os.path.join(work, 'case'), extra_objs=extra, ld_flags=ldf)
    code, out = qemu_ilp32.run(exe)
    if code != 0:
        return 'FAIL exit %d' % code, notes
    lines = dict((l.split(' ', 1) + [''])[:2] for l in out.strip().split('\n'))
    fails = []
    for i, (lab, c, t) in enumerate(d_ct):
        if lab not in expect:
            continue
        got = bytes.fromhex(lines['G%d' % i])[:len(expect[lab])]
        exp = expect[lab]
        for j, x in enumerate(exp):
            if isinstance(x, int) and got[j] != x:
                fails.append('%s byte %d: got %02x want %02x (got %s want %s)' % (lab, j, got[j], x, got.hex(),
                             ''.join('%02x' % y if isinstance(y, int) else '..' for y in exp)))
                break
    res = bytes.fromhex(lines['R'])
    if exp_res == 'OUTPTR':
        exp_res = list(bytes.fromhex(lines['P']))
    if exp_res is not None:
        for j, x in enumerate(exp_res):
            if isinstance(x, int) and res[j] != x:
                fails.append('result byte %d: got %s want %s' % (j, res[:len(exp_res)].hex(),
                             ''.join('%02x' % y if isinstance(y, int) else '..' for y in exp_res)))
                break
    if exp_out is not None:
        ob = bytes.fromhex(lines['O'])
        if list(ob[:len(exp_out)]) != exp_out:
            fails.append('out buffer: got %s want %s' % (ob[:len(exp_out)].hex(), bytes(exp_out).hex()))
    return ('FAIL ' + '; '.join(fails)) if fails else 'pass', notes


def f32_round(x):
    try:
        return struct.pack('<f', x)
    except OverflowError:
        return struct.pack('<f', float('inf') if x > 0 else float('-inf'))


def rand_pattern(n):
    return bytes((0x5c + 7 * i) & 0xff for i in range(n))


class Lay:
    def __init__(self, size, align, trivial, cls, pod=None):
        self.size, self.align, self.trivial, self.cls = size, align, trivial, cls
        self.pod = trivial if pod is None else pod
