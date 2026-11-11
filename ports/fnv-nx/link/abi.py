#!/usr/bin/env python3
"""abi - gate `abi`: a reference and the definition it binds to have one calling contract on x86 and on AArch64 (L2 SOURCE, ABI1).

    abi.py --obj obj.json                                         writes $GATE_OUT/abi.json, exit 0 iff green
    abi.py --decl REF_MSVC_NAME DEF_MSVC_NAME                     prints the verdict of one pair (exit 0 iff equal contracts)

RULE.  The Itanium symbol binds a reference in one object to the definition in another by name alone, and Itanium names carry neither the calling
convention nor the return type.  The MSVC names the plugin writes into `.fnvnx.sympair` do.  For a symbol S with a definition row (the
canonical definition: the first `def` row in object order) every other row of S (each `ref`, each further `def`) must have the same CONTRACT:
  contract(name) = ( x86 locations: the (class, location) of every parameter, `this` first, a hidden result pointer for an aggregate result;
                     x86 stack bytes the callee pops (symbolic over the classes);  x86 result location;
                     AAPCS64 locations of the same parameters;  AAPCS64 result location )
  parameter classes (abi_msvcname types): I8 B8(bool) I16 I32(int, long, enum) I64 P(pointer, reference, function pointer) MP(pointer to
  member function) F32 F64 F80 AGG:<qualified name> (class, struct, union by value: identified by its MSVC qualified name, sizes unknown).
  x86 (MSVC): thiscall `this` in ecx; fastcall the first two integer/pointer parameters of at most 4 bytes in ecx, edx; everything else on
  the stack in 4-byte words (8 for 64-bit values; an aggregate of unknown size keeps all later offsets symbolic); stdcall, thiscall, fastcall
  callee pops, cdecl caller pops.  AAPCS64: integers and pointers in x0..x7, floating point in v0..v7, then 8-byte slots; MP takes two registers.
Certified iff the two contracts are equal; the verdict names the first differing component (cc/this, parameter count, parameter class,
result, pop, variadic, aggregate, undecoded name).  Equal contracts mean every argument and the result travel in the same place on both machines,
so no bridge exists to be generated: where the legacy model produced `bridge` / `manual` / `x86-inconsistent`, the pair is uncertified.
UNIVERSE.  Every row that is a ref or a non-canonical def of a symbol that has a definition row somewhere in obj.json and is a function-kind row
(func, ctor.*, dtor.*) with an MSVC name, or an `unnamed:` row; item id `object:symbol`.  An `unnamed:` row, or a row of a symbol whose canonical
definition has no MSVC name, is uncertified (no contract can be compared).  Symbols with no definition row at all are linkstrict's business (class 3).  Rows of kind
thunk/vtable/gcc/internal/data are outside (no function contract of the exe).
PROOF.  Parameters are placed by the calling convention as a function of the ordered class list, so equal class lists under equal conventions
give equal locations; the classes are read from the MSVC grammar (abi_msvcname), where equal types have equal text.  A by-value aggregate is
the same type iff its qualified name is equal (provided by gate odr: equal `.fnvnx.layout` across all units per qualified name), so equal names pass the same bytes whatever the size.  A pair with differing aggregates,
a variadic side, a bool/char result difference (GCC reads bit 0 of a bool) or an unparsable name is not proven and is uncertified.
INPUT FACTS.  The MSVC names are the plugin's `.fnvnx.sympair` names, verified against cl's symbol tables by gate names; the same
aggregate name is the same type in two units: provided by gate odr (equal `.fnvnx.layout` across all units per name).  Rule: MSVC returns every
aggregate through a hidden pointer (small POD returned in eax:edx is not modelled: equal aggregate names make that irrelevant, differing ones
are uncertified anyway).  Dropped from the legacy tool: the bridge generator (`emit_bridge`, `sequentialize`: a repair), `bool_from_int`,
`ret_carry`, narrow-extension moves, `varargs_check` with `va_reads` from a run (`vacall`), `ecx_unused` from the pristine object, the
source-text type spelling (`classify` by regex over declaration text), and `describe_shape` strings.
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import abi_msvcname
import gatefile
import abi_native as N

NAME = 'abi'
FUNC_KINDS = frozenset(('func', 'ctor.c1', 'ctor.c2', 'dtor.d2', 'dtor.d1', 'dtor.d0', 'dtor.sdd', 'dtor.sdd.thunk'))
CLASS = {'void': 'VOID', 'bool': 'B8', 'char': 'I8', 'signed char': 'I8', 'unsigned char': 'I8', '__int8': 'I8', 'unsigned __int8': 'I8',
         'short': 'I16', 'unsigned short': 'I16', '__int16': 'I16', 'unsigned __int16': 'I16', 'wchar_t': 'I16', 'char16_t': 'I16',
         'int': 'I32', 'unsigned int': 'I32', 'long': 'I32', 'unsigned long': 'I32', '__int32': 'I32', 'unsigned __int32': 'I32',
         'char32_t': 'I32', '__int64': 'I64', 'unsigned __int64': 'I64', 'float': 'F32', 'double': 'F64', 'long double': 'F80'}
X86_SIZE = {'B8': 4, 'I8': 4, 'I16': 4, 'I32': 4, 'P': 4, 'MP': 4, 'I64': 8, 'F32': 4, 'F64': 8, 'F80': 8}
GPR = ('B8', 'I8', 'I16', 'I32', 'I64', 'P', 'MP')
FPR = ('F32', 'F64', 'F80')
CALLEE_POP = ('stdcall', 'thiscall', 'fastcall', 'pascal')


def qtext(q):
    return '::'.join(x if isinstance(x, str) else repr(x) for x in q)


def classify(t):
    k = t[0]
    if k == 'b':
        if t[1] not in CLASS:
            raise ValueError('builtin type %s has no x86/AAPCS64 class in this model' % t[1])
        return CLASS[t[1]]
    if k == 'ptr':
        return 'MP' if t[3][0] == 'memfn' else 'P'
    if k == 'tag':
        return 'I32' if t[1] == 'enum' else 'AGG:' + qtext(t[2])
    raise ValueError('type %s as a parameter or result' % k)


def _is_agg(c):
    return c.startswith('AGG:')


def parameters(d):
    """[(label, class)] in passing order (this first, then a hidden result pointer for an aggregate result, MSVC's order for members)."""
    ret = 'VOID' if d.ret is None else classify(d.ret)
    out = [('this', 'P')] if d.member else []
    if _is_agg(ret):
        out.append(('sret', 'P'))
    out += [('p%d' % i, classify(t)) for i, t in enumerate(d.params)]
    return out, ret


def x86_locations(d, ps):
    out, off, regs = [], (0, ()), 0
    for lab, c in ps:
        if d.cc == 'thiscall' and lab == 'this':
            out.append((c, 'ecx'))
        elif d.cc == 'fastcall' and regs < 2 and c in ('B8', 'I8', 'I16', 'I32', 'P') and lab != 'sret':
            out.append((c, ('ecx', 'edx')[regs]))
            regs += 1
        else:
            out.append((c, ('stk', off)))
            off = (0, off[1] + ((off[0], c),)) if _is_agg(c) else (off[0] + X86_SIZE[c], off[1])
    return out


def a64_locations(ps):
    out, g, v, s, sym = [], 0, 0, 0, None
    for _, c in ps:
        if sym is not None:
            out.append((c, ('sym', sym)))
            sym += (c,)
        elif _is_agg(c):
            out.append((c, ('agg', g, v, s)))
            sym = (c, g, v, s)
        elif c == 'MP' and g <= 6:
            out.append((c, ('x2', g)))
            g += 2
        elif c in FPR and v < 8:
            out.append((c, ('v', v)))
            v += 1
        elif c in GPR and c != 'MP' and g < 8:
            out.append((c, ('x', g)))
            g += 1
        else:
            al = 16 if c == 'F80' else 8
            s = (s + al - 1) // al * al
            out.append((c, ('stk', s)))
            s += al
            if c == 'MP':
                g = 8
    return out


def ret_x86(c):
    return 'none' if c == 'VOID' else 'eax' if c in GPR and c != 'I64' else 'edx:eax' if c == 'I64' else 'st0' if c in FPR else ('sret', c)


def ret_a64(c):
    return 'none' if c == 'VOID' else 'x0' if c in GPR else 'v0' if c in FPR else ('sret', c)


Contract = collections.namedtuple('Contract', 'cc this x86 pop ret_x86 a64 ret_a64 varargs ret')


def contract(name):
    d = abi_msvcname.parse(name)
    if d.kind != 'function':
        raise ValueError('%s: not a function name' % name)
    ps, ret = parameters(d)
    x = x86_locations(d, ps)
    stack = tuple(c for c, loc in x if isinstance(loc, tuple))
    return Contract(d.cc, d.member, tuple(x), stack if d.cc in CALLEE_POP else (), ret_x86(ret), tuple(a64_locations(ps)), ret_a64(ret), d.varargs, ret)


def compare(ref, dfn):
    """None when the two MSVC names have equal contracts, else the first differing component."""
    try:
        r, d = contract(ref), contract(dfn)
    except ValueError as e:
        return 'undecoded name: %s' % e
    if r.varargs != d.varargs:
        return 'variadic on one side only'
    if r.this != d.this:
        return 'cc/this: member on one side only'
    if len(r.x86) != len(d.x86):
        return 'parameter count %d vs %d' % (len(r.x86), len(d.x86))
    for i, ((rc, _), (dc, _)) in enumerate(zip(r.x86, d.x86)):
        if rc != dc:
            return 'aggregate parameter %d differs (%s vs %s)' % (i, rc, dc) if _is_agg(rc) or _is_agg(dc) else 'parameter %d class %s vs %s' % (i, rc, dc)
    if r.ret != d.ret:
        return 'result class %s vs %s' % (r.ret, d.ret)
    if r.x86 != d.x86:
        return 'x86 parameter locations differ (cc %s vs %s)' % (r.cc, d.cc)
    if r.pop != d.pop:
        return 'callee pops differ (cc %s vs %s)' % (r.cc, d.cc)
    if r.a64 != d.a64 or r.ret_a64 != d.ret_a64 or r.ret_x86 != d.ret_x86:
        return 'AAPCS64 locations differ'
    return None


def check(obj):
    """-> (universe ids, {id: reason})"""
    rows = collections.defaultdict(list)       # symbol -> [(object, ms name, kind, def|ref)]
    for k in sorted(k for k in obj if k[1] == 'sympair'):
        for sym, ms, kind, df in obj[k]:
            rows[sym].append((k[0], ms, kind, df))
    universe, red = [], {}
    for sym, rs in sorted(rows.items()):
        defs = [r for r in rs if r[3] == 'def']
        if not defs:
            continue
        canon = defs[0]
        for r in rs:
            unnamed = r[2].startswith('unnamed:')
            if r is canon or not (unnamed or (r[2] in FUNC_KINDS and r[1])):
                continue
            i = '%s:%s' % (r[0], sym)
            universe.append(i)
            if unnamed:
                red[i] = 'the row has no MSVC name (%s)' % r[2]
            elif canon[2] not in FUNC_KINDS or not canon[1]:
                red[i] = 'definition in %s has no MSVC name (%s)' % (canon[0], canon[2])
            elif r[2] != canon[2]:
                red[i] = 'kind %s vs the definition\'s %s' % (r[2], canon[2])
            else:
                why = compare(r[1], canon[1])
                if why:
                    red[i] = '%s [%s vs %s]' % (why, r[1], canon[1])
    return sorted(universe), red


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--obj')
    ap.add_argument('--decl', nargs=2, metavar=('REF', 'DEF'))
    a = ap.parse_args(argv)
    if a.decl:
        why = compare(*a.decl)
        print('equal contracts' if why is None else why)
        return 0 if why is None else 1
    if not a.obj:
        ap.error('--obj or --decl')
    try:
        uni, red = check(N.load_store(a.obj, 'obj'))
    except (ValueError, OSError) as e:
        print('abi: %s' % e, file=sys.stderr)
        return 2
    gatefile.write(NAME, len(uni), len(uni) - len(red), ['%s [%s]' % (i, r) for i, r in sorted(red.items())], 'references and further definitions of symbols with a definition')
    print('abi: %d items, %d uncertified' % (len(uni), len(red)))
    return 1 if red else 0


if __name__ == '__main__':
    sys.exit(main())
