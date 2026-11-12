#!/usr/bin/env python3
"""The bridge generator of DISPATCH1: one host function per distinct descriptor, and the exact-lookup table rt/dispatch.c reads.

    bridgegen.py --descs DESCS.tsv --out bridges.cc [--map bridges.tsv]
    bridgegen.py --obj OBJ [--obj OBJ ...] --out bridges.cc [--map bridges.tsv]

DESCS.tsv: one descriptor per line (`#` lines and blank lines ignored). With --obj the descriptors are read from the section `.fnvnx.disp` of each object
(the fact toolchain/dispatch/fnvnx_dispatch.cc writes: one text line per distinct descriptor of the unit); the set is the union over the objects given, i.e.
over every admitted product root's objects.  Output is a pure function of the SET of descriptors (sorted; no clock, no path).

THE DESCRIPTOR (contract: toolchain/dispatch/fnvnx_dispatch.cc)  `conv:ret:arg,arg,...`
  conv   this | fast | stack          ret  v | i | q | f | d | a<N> | a<N>hf | a<N>hd | m<N>
  arg    i | q | f | d | s<N>g | s<N>hf | s<N>hd | s<N>r
A BRIDGE has the call site's AAPCS signature (the C++ types below) and runs the guest function at the exe address the call sequence stored in the
thread-local fnvnx_call_fp (the descriptor does not say which function; one bridge serves every target of its shape):
  AAPCS side:   i = u32 (an integer of 32 bits or less, or a pointer: ILP32)   q = u64   f = float   d = double
                s<N>g = struct of N bytes by value (general registers or stack)   s<N>hf / s<N>hd = struct of N/4 floats / N/8 doubles by value
                s<N>r = u32, the address of the copy the caller made   a<N> / a<N>hf / a<N>hd = the same struct types returned by value
                m<N> = a struct of N bytes with a user-provided copy constructor, returned by value: GCC's C++ ABI returns it through the hidden pointer (x8),
                       as it does for every class that is returned in memory, whatever its size (the plugin says m iff aggregate_value_p)
  x86 side (the MSVC x86 conventions the exe was built with; every value is the memory image of its AAPCS-side value):
    stack words, in this order: [the hidden result pointer, for a result in memory] then the arguments left to right at the lowest address first:
        i 1 word, f 1 word (the bits), q and d 2 words (low first), s<N>g / s<N>h* N bytes rounded up to words (the struct's memory image; the HFA
        classes are an AAPCS notion, on x86 the struct is just bytes), s<N>r the same N bytes, copied from the address the caller passed.
    this:  ecx = the first argument (the object; it must be `i`) and it is not on the stack. fast: the first two arguments of class `i` (a 32-bit or smaller
        integer or pointer, left to right; everything else, whatever its size, is on the stack) go in ecx, edx and are not on the stack. stack: all on the stack.
    result: v nothing; i eax; q edx:eax; f, d ST0 (kind 2: fnvnx_guest_invoke_regs pops it); an aggregate of 1, 2, 4 or 8 bytes in eax / edx:eax (the struct is the
        bytes of eax, edx), any other size through the hidden pointer the bridge passes (address of the local that becomes the AAPCS-side result).
    `ret N`, cdecl or stdcall, needs no information: fnvnx_guest_invoke_regs resets the guest esp itself (rt/enter.cc).
  Not defined, so a BUILD ERROR (the descriptor is listed, nothing is generated): any form outside the grammar; `this` whose first argument is not `i`; a
  hidden result pointer under `fast` (which register MSVC gives it is not a fact this tool has: no guess); sizes the classes cannot hold (s<N>g with N > 16, an
  HFA with more than 4 elements or a size not a multiple of the element, N = 0).
THE TABLE: key = FNV-1a (32 bit) of the descriptor's bytes; all keys must differ (a collision is a build error); minimal perfect hash (tools/phf.py), the lookup
of rt/dispatch.c: slot = ((k ^ g[(k*K1) >> (32-NBITS)]) * K2) >> (32-TBITS).  Every slot of a non-empty set is a bridge; the hash is exact over the set, and a
string outside it cannot reach the lookup (see rt/dispatch.c).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))      # the hash lives in this tool root: a tool imports only from its own root
import phf

M = 0xffffffff


def fnv1a(s):
    h = 2166136261
    for b in s.encode('ascii'): h = ((h ^ b) * 16777619) & M
    return h


def _size(t, i):
    n = t[i:]
    if not n.isdigit() or not n.isascii() or int(n) == 0: raise ValueError('bad size in %r' % t)
    return int(n)


# The element of a homogeneous aggregate, by the part mode the compiler's own backend reports for it (the plugin asks targetm.calls.function_arg /
# function_value, it does not classify types itself): suffix -> (element size in bytes, C++ element type). An element code not in this table is an error.
ELEM = {'hf': (4, 'float'), 'hd': (8, 'double'), 'hq': (16, 'long double'), 'hh': (2, '_Float16'),
        'hv8': (8, 'v8f'), 'hv16': (16, 'v16f')}


def _hsuf(t):
    """the element code at the end of t (`s12hf`, `a8hd`, `s32hv16`) and the rest."""
    for c in sorted(ELEM, key=len, reverse=True):
        if t.endswith(c): return c, t[:-len(c)]
    return None, t


def _hfa(n, e, t):
    if n % e or not 1 <= n // e <= 4: raise ValueError('%r: an HFA of %d bytes of %d-byte elements' % (t, n, e))
    return n // e


def parse(desc):
    """descriptor -> (conv, ret, args); ret = (cls, N, k) with cls in v i q f d a h m (a: general registers, h: homogeneous, k elements of e bytes), args a list of (cls, N, k, e)."""
    p = desc.split(':')
    if len(p) != 3 or p[0] not in ('this', 'fast', 'stack'): raise ValueError('not conv:ret:args with conv this, fast or stack')
    conv, r, a = p
    xmem = r[-1:] == 'x'                  # the x86 callee returns through the hidden pointer whatever the size (a type that is not a POD): the AAPCS side is in registers
    if xmem: r = r[:-1]
    if r in ('v', 'i', 'q', 'f', 'd'): ret = (r, 0, 0, 0)
    elif r[:1] == 'm': ret = ('m', _size(r, 1), 0, 0)
    elif r[:1] == 'a' and _hsuf(r)[0]:
        c, rest = _hsuf(r); n = _size(rest, 1); ret = ('h', n, _hfa(n, ELEM[c][0], r), c)
    elif r[:1] == 'a':
        n = _size(r, 1)
        if n > 16: raise ValueError('%r: a register aggregate of more than 16 bytes' % r)
        ret = ('a', n, 0, 0)
    else: raise ValueError('bad return class %r' % r)
    args = []
    for t in (a.split(',') if a else []):
        if t in ('i', 'q', 'f', 'd'): args.append((t, 0, 0, 0))
        elif t[:1] == 's' and t[-1] == 'r': args.append(('r', _size(t[:-1], 1), 0, 0))
        elif t[:1] == 's' and _hsuf(t)[0]:
            c, rest = _hsuf(t); n = _size(rest, 1); args.append(('h', n, _hfa(n, ELEM[c][0], t), c))
        elif t[:1] == 's' and t[-1] == 'g':
            n = _size(t[:-1], 1)
            if n > 16: raise ValueError('%r: a general aggregate of more than 16 bytes' % t)
            args.append(('g', n, 0, 0))
        else: raise ValueError('bad argument class %r' % t)
    if conv == 'this' and (not args or args[0][0] != 'i'): raise ValueError('this-call whose first argument is not `i`')
    if xmem and ret[0] not in ('a', 'h'): raise ValueError('%r: x on a result that is not an aggregate in registers' % desc)
    reg_ret = ret[0] in ('v', 'i', 'q', 'f', 'd') or (ret[0] in ('a', 'h') and ret[1] in (1, 2, 4, 8) and not xmem)
    # documented (Agner Fog, Calling conventions, table 7, Microsoft 32 bit): a result too big or of a size that is no power of two (PS) has its hidden pointer on the stack
    # even under __fastcall; a non-POD result (P) has it "in a register if fastcall", which register the document does not say: that case has no proof yet
    if not reg_ret and conv == 'fast' and (xmem or ret[0] == 'm'): raise ValueError('a non-POD result under fast: the register of the hidden pointer is not documented')
    return conv, ret, args, reg_ret


def emit(desc, name):
    """C++ text of the bridge for `desc`."""
    conv, ret, args, reg_ret = parse(desc)
    cls, rn, rk, re_ = ret
    rt = {'v': 'void', 'i': 'u32', 'q': 'u64', 'f': 'float', 'd': 'double', 'a': 'G%d' % rn, 'm': 'M%d' % rn,
          'h': 'H%s_%d' % (re_, rk)}[cls]
    ps, body, w = [], [], 0          # w: stack word index
    ecx = edx = '0'
    nregs = 0
    stack = []                       # (cls, n, name)
    if not reg_ret: stack.append(('hid', 0, 'hid'))
    for i, (c, n, k, e) in enumerate(args):
        a = 'a%d' % i
        pt = {'i': 'u32', 'q': 'u64', 'f': 'float', 'd': 'double', 'r': 'u32', 'g': 'G%d' % n, 'h': 'H%s_%d' % (e, k) if c == 'h' else ''}[c]
        ps.append('%s %s' % (pt, a))
        if conv == 'this' and i == 0: ecx = a
        elif conv == 'fast' and c == 'i' and nregs < 2:
            if nregs == 0: ecx = a
            else: edx = a
            nregs += 1
        else: stack.append((c, n, a))
    for c, n, a in stack:
        if c == 'hid': body.append('args[%d] = (u32)(unsigned long)&res;' % w); w += 1
        elif c == 'i': body.append('args[%d] = %s;' % (w, a)); w += 1
        elif c == 'f': body.append('args[%d] = fbits(%s);' % (w, a)); w += 1
        elif c == 'q': body.append('args[%d] = (u32)%s; args[%d] = (u32)(%s >> 32);' % (w, a, w + 1, a)); w += 2
        elif c == 'd': body.append('{ u64 b = dbits(%s); args[%d] = (u32)b; args[%d] = (u32)(b >> 32); }' % (a, w, w + 1)); w += 2
        elif c in ('g', 'h'): body.append('put(args + %d, &%s, %d);' % (w, a, n)); w += (n + 3) // 4
        elif c == 'r': body.append('put(args + %d, (const void *)%s, %d);' % (w, a, n)); w += (n + 3) // 4
    kind = 2 if cls in ('f', 'd') else 1 if (cls == 'q' or (cls in ('a', 'h') and rn == 8)) else 0
    pre = ['%s res;' % rt] if cls == 'm' or (cls in ('a', 'h') and not reg_ret) else []
    post = {'v': '', 'i': 'return out.eax;', 'q': 'return (u64)out.eax | ((u64)out.edx << 32);', 'f': 'return (float)out.st0;', 'd': 'return out.st0;'}.get(cls)
    if post is None:
        if reg_ret: pre, post = ['%s res;' % rt], 'u32 rw[2] = { out.eax, out.edx }; cp(&res, rw, %d); return res;' % rn
        else: post = 'return res;'
    return ('static %s %s(%s)\n{\n    u32 fp = fnvnx_call_fp;\n    u32 args[%d];\n    struct fnvnx_guest_ret out;\n%s\n%s\n    fnvnx_guest_invoke_regs(fp, %s, %s, %d, args, %d, &out);\n    %s\n}\n'
            % (rt, name, ', '.join(ps) or 'void', max(w, 1), ''.join('    %s\n' % x for x in pre), ''.join('    %s\n' % x for x in body), ecx, edx, w, kind, post))


HEAD = '''/* Generated by lift/tools/bridgegen.py: the bridges of DISPATCH1 and the table rt/dispatch.c looks them up in. A pure function of the set of descriptors. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef float v8f __attribute__((vector_size(8)));
typedef float v16f __attribute__((vector_size(16)));
struct fnvnx_guest_ret { u32 eax, edx; double st0; };
extern "C" {
void fnvnx_guest_invoke_regs(u32 addr, u32 ecx, u32 edx, u32 nargs, const u32 *args, u32 kind, struct fnvnx_guest_ret *out);
extern __thread u32 fnvnx_call_fp;
}
/* byte copies the optimizer must not turn into calls of memcpy (the bridges are freestanding): the empty asm is in the loop */
static inline void cp(void *d, const void *s, u32 n) { u8 *a = (u8 *)d; const u8 *b = (const u8 *)s; for (u32 i = 0; i < n; i++) { a[i] = b[i]; __asm__ volatile("" ::: "memory"); } }
/* n bytes into the stack words at w, the last word's unused bytes 0 */
static inline void put(u32 *w, const void *s, u32 n) { if (n & 3) w[n >> 2] = 0; cp(w, s, n); }
static inline u32 fbits(float f) { union { float f; u32 u; } t; t.f = f; return t.u; }
static inline u64 dbits(double f) { union { double f; u64 u; } t; t.f = f; return t.u; }
'''


def types(descs):
    G, H, M_ = set(), set(), set()
    for d in descs:
        conv, ret, args, _ = parse(d)
        for c, n, k, e in [ret] + args:
            if c in ('a', 'g'): G.add(n)
            elif c == 'm': M_.add(n)
            elif c == 'h': H.add((e, k))
    out = ['typedef struct { u8 b[%d]; } G%d;' % (n, n) for n in sorted(G)]
    out += ['typedef struct { %s v[%d]; } H%s_%d;' % (ELEM[e][1], k, e, k) for e, k in sorted(H)]
    out += ['struct M%d { u8 b[%d]; M%d() {} M%d(const M%d &o) { cp(b, o.b, %d); } };' % (n, n, n, n, n, n) for n in sorted(M_)]
    return '\n'.join(out) + '\n'


def generate(descs):
    """set of descriptors -> (C++ text, [(descriptor, symbol, key)]); ValueError listing every bad descriptor."""
    descs = sorted(set(descs))
    bad = []
    for d in descs:
        try: parse(d)
        except ValueError as e: bad.append('%s: %s' % (d, e))
    keys = {}
    for d in descs:
        if fnv1a(d) in keys and keys[fnv1a(d)] != d: bad.append('%s: FNV-1a collides with %s' % (d, keys[fnv1a(d)]))
        keys[fnv1a(d)] = d
    if bad: raise ValueError('\n'.join(bad))
    names = {d: 'fnvnx_bridge_%04d' % i for i, d in enumerate(descs)}
    h = phf.build([fnv1a(d) for d in descs])
    t = [HEAD, types(descs)] + [emit(d, names[d]) for d in descs]
    rows = ['    { 0x%08xu, "%s", (void (*)())%s },' % (a, keys[a], names[keys[a]]) if a is not None else '    { 0, 0, 0 },' for a in h['slots']]
    t += ['struct fnvnx_bridge_row { u32 key; const char *desc; void (*fn)(); };',
          'extern "C" {', 'extern const struct fnvnx_bridge_row fnvnx_bridge_table[] = {'] + rows + ['};',
          'extern const u16 fnvnx_bridge_g[] = { %s };' % ', '.join(map(str, h['g'])),
          'extern const u32 fnvnx_bridge_phf_params[4] = { %d, %d, %d, %d };' % (h['k1'], h['k2'], h['nbits'], h['tbits']), '}']
    return '\n'.join(t) + '\n', [(d, names[d], fnv1a(d)) for d in descs]


def read_obj(path):
    from elftools.elf.elffile import ELFFile
    with open(path, 'rb') as fh:
        s = ELFFile(fh).get_section_by_name('.fnvnx.disp')
        data = s.data() if s is not None else b''
    if data and data[-1:] != b'\n': raise ValueError('%s: .fnvnx.disp: no final LF' % path)
    return [x for x in data.decode('ascii').split('\n') if x]


def main(argv):
    descs, objs, out, mp, i = set(), [], None, None, 0
    while i < len(argv):
        o = argv[i]
        if o == '--descs':
            for ln in open(argv[i + 1]):
                ln = ln.rstrip('\n')
                if ln.strip() and ln[0] != '#': descs.add(ln)
        elif o == '--obj': objs.append(argv[i + 1])
        elif o == '--out': out = argv[i + 1]
        elif o == '--map': mp = argv[i + 1]
        else: sys.exit('usage: bridgegen.py (--descs F | --obj O ...) --out bridges.cc [--map bridges.tsv]')
        i += 2
    if out is None: sys.exit('bridgegen: --out is required')
    try:
        for p in sorted(objs): descs.update(read_obj(p))
        text, rows = generate(descs)
    except ValueError as e: sys.exit('bridgegen: a descriptor has no bridge:\n' + str(e))
    with open(out, 'w', newline='') as f: f.write(text)
    if mp:
        with open(mp, 'w', newline='') as f: f.write(''.join('%s\t%s\t%08x\n' % r for r in rows))
    print('bridgegen: %d descriptors, %d bridges' % (len(descs), len(rows)))


if __name__ == '__main__': main(sys.argv[1:])
