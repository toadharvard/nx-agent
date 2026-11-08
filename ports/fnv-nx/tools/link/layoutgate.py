#!/usr/bin/env python3
"""layoutgate.py - the LAYOUT gate (orchestrator, 2026-11-08): every class / struct of the prepared tree laid out
by MSVC's rules (i686) and by the native compiler (GCC AArch64 ILP32, compile.sh's flags), compared field by field.

    python3 tools/link/layoutgate.py --tree <prepared tree> [--units list.txt] [--boot boot_units.txt]
                                     [-o reports/layoutgate/<head>.tsv] [--jobs N] [--msvc clang|cl]

MSVC side: `clang --target=i686-pc-windows-msvc -fdump-record-layouts-complete` over the unit as the native compile
sees it (the same shim and includes, `_MSC_VER` undefined, `__aarch64__` / `__ILP32__` defined for the shim's guard),
so the two sides lay out the same declarations and only the layout rules differ. clang implements MSVC's record
layout (vfptr first, base placement, bitfield units, no EBO for a later empty base, ...). `--msvc cl` (a farm with
the equivalence-gate toolchain) is the reference: cl 15 /d1reportAllClassLayout - not wired yet.
Native side: a probe unit that includes the unit and, per record the MSVC dump names, emits
    extern "C" const unsigned __fnvnx_lg_<i>[] = {sizeof(R), alignof(R), offsetof(R, f1), ..., base offsets...};
compiled with layout.py's flags plus -fno-access-control (private members); the constants are read back from the
object (layout.symbol_words). A record or member the probe cannot name is dropped (layout.py's retry loop: the
failing lines go, at most 8 rounds); bitfields and unnamed members are compared through the size only.

Rows (COLUMNS): unit, record, what (size / align / field <name> / base <name>), msvc, native, cause, boot.
The cause of a record is that of its first differing member, in order of offset (later members shift with it):
ebo (an empty base / FNVNX_EBO filler), bitfield, long-double, wchar, bool, enum, align8 (an 8-byte scalar:
double, long long, __int64, __m64), memfnptr (a pointer to member function: 8 bytes natively, 4 under MSVC),
vfpad / vfpad-src (MSVC pads a record's own vfptr to the record's alignment > 4; -src: the members' offset names
say the exe has no padding, see vfpad()), vptr (the record's or a base's vfptr), pack (a record MSVC packs below its
members' alignment), or other. A record identical on both sides gives no row. The summary line (stderr and the
.summary file next to the TSV) counts differing records and fields, boot set and the rest, unique by record name; an EBO2 base (size 0 natively,
1 under MSVC, nothing else differs) is counted apart, as ebo2 bases (the member check covers it).
"""
import argparse
import collections
import concurrent.futures
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import layout   # noqa: E402
import objcache  # noqa: E402

PORT = layout.PORT
COLUMNS = ['unit', 'record', 'what', 'msvc', 'native', 'cause', 'boot']
CLANG = os.environ.get('CLANG', 'clang++')


Rec = collections.namedtuple('Rec', 'name size align fields bases vptr packed')
# fields: [(offset or None for a bitfield, name, type)]; bases: [(offset, name, kind)]


_SYSDIRS = None


def native_system_dirs():
    """the native compiler's C++ and C system include dirs (not GCC's own builtin headers: clang has its own), for the
    MSVC side as -idirafter: the msvc target has no headers of its own, and a missing <string.h> / <cstddef> is a fatal
    error after which clang stops instantiating templates (a template base dropped out of every record using it)"""
    global _SYSDIRS
    if _SYSDIRS is None:
        dirs = []                     # published whole: the gate's threads read _SYSDIRS concurrently
        try:
            r = subprocess.run([layout.CXX, '-mabi=ilp32', '-xc++', '-E', '-v', '-'], input='', capture_output=True,
                               text=True, timeout=60)
            txt = r.stderr.split('#include <...> search starts here:')[1].split('End of search list.')[0]
            for d in txt.split():
                d = os.path.normpath(d)
                if re.search(r'/gcc(?:-cross)?/[^/]+/\d+/include$', d):
                    continue
                dirs += ['-idirafter', d]
        except (OSError, IndexError, subprocess.TimeoutExpired):
            pass
        _SYSDIRS = dirs
    return _SYSDIRS


def msvc_flags(tree):
    sse = os.environ.get('SSE2NEON') or os.path.join(PORT, 'ext', 'sse2neon')
    return ['--target=i686-pc-windows-msvc', '-std=gnu++17', '-fsyntax-only', '-fms-extensions', '-fshort-wchar',
            '-fsigned-char', '-U_MSC_VER', '-U_WIN32', '-U_WIN64', '-U_M_IX86', '-U_M_IX86_FP',
            '-fms-compatibility-version=0',   # the native compile's branches: no Windows target macros
            '-D__aarch64__', '-D__ILP32__',
            # fnvnx_compat.h's sized operator delete block includes <cstddef>, which the msvc target lacks: the fatal
            # error stops clang instantiating templates, and a template base dropped out of its derived records
            # (frondengine _Vector_val 4 bytes instead of 12)
            '-D__cpp_sized_deallocation=201309L',
            '-Wno-everything', '-ferror-limit=0',
            # the SIMD types as MSVC defines them (__m64 8/8, __m128 16/16, ...): the shim's SSE headers are
            # sse2neon (AArch64) and stay out by their guards; their function bodies do not affect records
            '-include', os.path.join(HERE, 'layoutgate_msvc', 'fnvnx_lg_msvc_types.h'),
            '-DSSE2NEON_H', '-DFNVNX_SSE2NEON_H', '-DFNVNX_MMX_H',
            '-include', os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h'),
            '-include', os.path.join(HERE, 'layoutgate_msvc', 'fnvnx_lg_msvc_post.h'),   # the shim's fillers off
            '-I' + os.path.join(PORT, 'shim', 'include'), '-I' + sse, '-I' + os.path.join(tree, 'include'),
            '-Xclang', '-fdump-record-layouts-complete'] + native_system_dirs()


_LINE = re.compile(r'^\s*(\d+)(?::(\d+-\d+|-))?\s*\|( *)(.*)$')
_SIZE = re.compile(r'\[sizeof=(\d+), (?:dsize=\d+, )?align=(\d+)')


def parse_dump(text):
    """{record name: Rec} from clang's -fdump-record-layouts(-complete) output (MSVC target)."""
    out = {}
    for block in text.split('*** Dumping AST Record Layout')[1:]:
        lines = block.splitlines()
        name, fields, bases, vptr, size, align = None, [], [], None, None, None
        for ln in lines:
            m = _LINE.match(ln)
            if m:
                off, bit, ind, body = int(m.group(1)), m.group(2), len(m.group(3)), m.group(4).strip()
                if name is None:
                    name = re.sub(r'\s*\(empty\)$', '', re.sub(r'^(?:struct|class|union)\s+', '', body))
                    if body.startswith('union'): name = None; break   # unions: members overlap by design
                    continue
                if ind != 3:
                    continue                                          # nested members of a member / base
                if body.endswith('vftable pointer)') or body.endswith('vfptr)'):
                    vptr = off; continue
                bm = re.match(r'^(?:struct|class)\s+(.+?)\s+\((primary base|base|virtual base)\)$', body)
                if bm:
                    bases.append((off, bm.group(1), bm.group(2))); continue
                if body.endswith('vbtable pointer)'):
                    continue
                fm = re.match(r'^(.*?)\s*\b([A-Za-z_]\w*)$', body)
                if fm:
                    if bit == '-':
                        fields.append((None, '', body))              # a zero-size member (the EBO filler)
                    else:
                        fields.append((None if bit is not None else off, fm.group(2), fm.group(1)))
                else:
                    fields.append((None, '', body))                  # unnamed member
                continue
            sm = _SIZE.search(ln)
            if sm and size is None:
                size, align = int(sm.group(1)), int(sm.group(2))
        if not name or size is None or '(anonymous' in name or '(lambda' in name or 'unnamed' in name:
            continue
        if name.startswith(('__NS', 'fnvnx_', '__fnvnx', 'std::', '__gnu')):
            continue                                                  # the shim's and the compiler's own records
        nat = max([layout_natural(t) for _, _, t in fields] + [4 if vptr is not None or bases else 1])
        out.setdefault(name, Rec(name, size, align, fields, bases, vptr, align < nat))
    return out


def layout_natural(t):
    t = t.strip()
    if t.endswith('*') or t.endswith('&'): return 4
    if re.search(r'\b(?:double|long long|__int64|__m64)\b', t): return 8
    if re.search(r'\b(?:int|long|float|unsigned|wchar_t)\b', t): return 4
    if re.search(r'\bshort\b', t): return 2
    return 1


def msvc_layouts(tree, unit):
    r = subprocess.run([CLANG, *msvc_flags(tree), os.path.join(tree, unit)], capture_output=True, text=True,
                       cwd=tree, timeout=900)
    return parse_dump(r.stdout)


class ProbeFailed(Exception):
    pass


def _words(obj):
    """layout.symbol_words of the probe object, None when it is empty or not a readable ELF object"""
    try:
        if os.path.getsize(obj) < 52:
            return None
        return layout.symbol_words(obj, '__fnvnx_lg_')
    except (OSError, ValueError, IndexError, struct.error):
        return None


def probe(tree, workdir, unit, recs):
    """{record: (size, align, {field: off}, {base: off})} from the native compile of a probe of the unit."""
    base = unit.replace('/', '_')
    src, obj = os.path.join(workdir, base + '.lg.cpp'), os.path.join(workdir, base + '.lg.o')
    alive = [r for r in recs if '<' not in r.name or r.name.count('<') == r.name.count('>')]
    res, plain = {}, set()
    for _ in range(10):
        if not alive:
            break
        lines = ['#include "%s"' % os.path.join(tree, unit), '#include <cstddef>', '#include <type_traits>']
        first = len(lines) + 1
        layout_of = []
        for i, r in enumerate(alive):
            # the elaborated name: a function of the record's name hides it (tesobjectrefr.cpp's struct and function
            # FUN_0057a9d0: sizeof(::FUN_0057a9d0) was the function's, 1); the plain name when that fails
            el = r.name not in plain
            q = ('struct ::' if el else '::') + r.name
            vals = ['sizeof(%s)' % q, 'alignof(%s)' % q]
            fs = [f for f in r.fields if f[0] is not None and f[1]]
            vals += ['__builtin_offsetof(%s, %s)' % (q, f[1]) for f in fs]
            bs = [b for b in r.bases if b[2] != 'virtual base']
            vals += ['(unsigned)((char *)static_cast<%s ::%s *>((%s *)0x10000) - (char *)0x10000)'
                     % ('struct' if el else '', b[1], q) for b in bs]
            # the member-function-pointer fields (bit j: field j < 32): 8 bytes natively (Itanium ptr + adj), 4 under
            # MSVC for a single-inheritance class
            vals.append('0u' + ''.join(' | (std::is_member_function_pointer<std::remove_all_extents<decltype(((%s *)0)->%s)>'
                                       '::type>::value ? %du : 0u)' % (q, f[1], 1 << j) for j, f in enumerate(fs[:32])))
            layout_of.append((r, fs, bs))
            lines.append('extern "C" const unsigned __fnvnx_lg_%d[] = {%s};' % (i, ', '.join(vals)))
        open(src, 'w').write('\n'.join(lines) + '\n')
        fl = [f if f != '-O2' else '-O0' for f in layout.flags(tree)] + ['-fno-access-control', '-Wno-invalid-offsetof']
        rr = objcache.Result(*objcache.compile(layout.CXX, fl, src, obj, cwd=tree))
        if rr.returncode == 0:
            words = _words(obj)
            if words is None:
                # an empty / truncated object with exit 0 (af034f14 on sw30: a 0-byte object, struct.error): once
                # more without the object cache, then the unit is probe-failed
                d = subprocess.run([layout.CXX, *fl, src, '-o', obj], cwd=tree, capture_output=True, text=True,
                                   errors='replace')
                words = _words(obj) if d.returncode == 0 else None
                if words is None:
                    size = os.path.getsize(obj) if os.path.exists(obj) else -1
                    raise ProbeFailed('object %d bytes after a direct compile (exit %d): %s'
                                      % (size, d.returncode, (d.stderr or rr.stderr).strip()[-300:].replace('\n', ' | ')))
            for i, (r, fs, bs) in enumerate(layout_of):
                w = words.get('__fnvnx_lg_%d' % i)
                if not w or len(w) < 3 + len(fs) + len(bs):
                    continue
                pmf = w[2 + len(fs) + len(bs)]
                res[r.name] = (w[0], w[1], {f[1]: w[2 + j] for j, f in enumerate(fs)},
                               {b[1]: w[2 + len(fs) + j] for j, b in enumerate(bs)},
                               {f[1] for j, f in enumerate(fs[:32]) if pmf >> j & 1})
            break
        bad = {int(n) - first for n in re.findall(r'%s:(\d+):\d+: error' % re.escape(os.path.basename(src)),
                                                  rr.stderr)}
        bad = {b for b in bad if 0 <= b < len(alive)}
        if not bad:
            break
        retry = {alive[b].name for b in bad if alive[b].name not in plain}
        plain |= retry
        alive = [r for i, r in enumerate(alive) if i not in bad or r.name in retry]
    return res


def cause_of(rec, what_name, pmf=()):
    """the cause of a record's difference: the first member, in declaration order up to and including the first
    differing one (what_name; all members for a size-only difference), whose type or kind is a known ABI difference"""
    upto = next((i for i, f in enumerate(rec.fields) if f[1] == what_name), len(rec.fields) - 1)
    for f in rec.fields[:upto + 1]:
        c = 'memfnptr' if f[1] and f[1] in pmf else _cause_type(rec, f)
        if c != 'other': return c
    return 'pack' if rec.packed else 'other'


def _cause_type(rec, f):
    t = f[2]
    if not f[1] and ('fnvnx_int_of_align' in t or '::type' in t): return 'ebo'
    if f[0] is None and f[1]: return 'bitfield'
    if 'long double' in t: return 'long-double'
    if 'wchar_t' in t: return 'wchar'
    if re.search(r'\bbool\b', t): return 'bool'
    if t.startswith('enum '): return 'enum'
    if re.search(r'\b(?:double|long long|__int64|__m64)\b', t): return 'align8'
    return 'other'


# a member named by its exe offset (the decomp convention: m10, unk3c, m_20, m_pad0e, field_1c)
_OFFNAME = re.compile(r'^(?:m_)?(?:unk|field|pad|m)?_?([0-9A-Fa-f]{2,4})$')


def offset_names(rec, foff):
    """(members whose name gives their MSVC offset, ... their native offset): the exe's evidence"""
    em = en = 0
    for off, nm, _ in rec.fields:
        m = _OFFNAME.match(nm or '')
        if off is None or not m or nm not in foff:
            continue
        v = int(m.group(1), 16)
        em += v == off
        en += v == foff[nm]
    return em, en


def vfpad(rec, foff):
    """MSVC pads a record's own vfptr (no base) to the record's alignment: the first member sits at alignof(record)
    (8: a double / __int64 member, 16: __m128 / hkVector4), not at 4 as GCC places it. 'vfpad' when the members'
    offset names agree with MSVC's (or say nothing) - the native layout is to follow; 'vfpad-src' when they agree
    with the native one - the exe has no padding there (the source's own vfptr stands for a 4-aligned base's)."""
    if rec.vptr != 0 or rec.bases or rec.align <= 4:
        return ''
    first = next((f for f in rec.fields if f[0] is not None and f[1]), None)
    if not first or first[0] != rec.align or foff.get(first[1]) != 4:
        return ''
    em, en = offset_names(rec, foff)
    return 'vfpad-src' if en > em else 'vfpad'


def compare(unit, rec, nat, boot):
    """rows for one record: the first differing member decides the cause of the record."""
    size, align, foff, boff, pmf = nat
    diffs = []
    vp = vfpad(rec, foff)
    for off, nm, kind in rec.bases:
        if nm in boff and boff[nm] != off:
            diffs.append((off, 'base ' + nm, off, boff[nm], 'ebo' if kind != 'primary base' else 'vptr'))
    for off, nm, t in rec.fields:
        if off is None or not nm or nm not in foff:
            continue
        if foff[nm] != off:
            diffs.append((off, 'field ' + nm, off, foff[nm], vp or cause_of(rec, nm, pmf)))
    diffs.sort()
    rows = []
    cause = diffs[0][4] if diffs else (vp or cause_of(rec, '', pmf) if size != rec.size else '')
    if size == 0 and rec.size == 1:
        cause = 'ebo'                     # a zero-size native class: the FNVNX_EBO2 filler alone (`char x[0]`)
    if size != rec.size:
        rows.append((unit, rec.name, 'size', rec.size, size, cause, boot))
    if align != rec.align:
        rows.append((unit, rec.name, 'align', rec.align, align,
                     cause or ('pack' if rec.packed or rec.align < align and size == rec.size else 'align8'), boot))
    for _, what, m, n, c in diffs:
        rows.append((unit, rec.name, what, m, n, cause, boot))
    return rows


def run_unit(tree, workdir, unit, boot):
    try:
        return _run_unit(tree, workdir, unit, boot)
    except Exception as e:  # noqa: BLE001 - one unit never stops the run: it is reported as probe-failed
        return unit, [(unit, '-', 'probe-failed', '-', '%s: %s' % (type(e).__name__, str(e)[:400].replace('\t', ' ')),
                       'probe-failed', boot)], 0, 0, []


def _run_unit(tree, workdir, unit, boot):
    try:
        recs = msvc_layouts(tree, unit)
    except subprocess.TimeoutExpired:
        return unit, [], 0, 0, []
    if not recs:
        return unit, [], 0, 0, []
    nat = probe(tree, workdir, unit, list(recs.values()))
    rows = []
    for name, rec in recs.items():
        if name in nat:
            rows += compare(unit, rec, nat[name], boot)
    # members, for the member check (a pack / ebo record placed inside another record)
    members = [(unit, rec.name, off, nm, re.sub(r'^(?:const\s+|volatile\s+|struct\s+|class\s+)+', '', t).strip(), boot)
               for rec in recs.values() for off, nm, t in rec.fields if off is not None and nm]
    return unit, rows, len(recs), len(nat), members


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tree', required=True)
    ap.add_argument('--units', help='units to measure (tree paths), default build/units.json')
    ap.add_argument('--boot', help='units of the boot set (tree paths), one per line')
    ap.add_argument('-o', '--out', default='layoutgate.tsv')
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--workdir')
    a = ap.parse_args()
    tree = os.path.abspath(a.tree)
    if a.units:
        units = [l.strip() for l in open(a.units) if l.strip()]
    else:
        import json
        units = [u['source'] for u in json.load(open(os.path.join(tree, 'build', 'units.json')))['units']]
    boot = {l.strip() for l in open(a.boot) if l.strip()} if a.boot else set()
    workdir = a.workdir or os.path.join(os.path.dirname(os.path.abspath(a.out)), '.layoutgate')
    os.makedirs(workdir, exist_ok=True)
    allrows, seen, measured = [], {}, [0, 0]
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
        futs = [ex.submit(run_unit, tree, workdir, u, int(u in boot)) for u in units]
        allmembers = []
        for f in concurrent.futures.as_completed(futs):
            unit, rows, nm, nn, mem = f.result()
            measured[0] += nm; measured[1] += nn
            allrows += rows; allmembers += mem
    # the member check: a record whose layout differs only by its alignment under #pragma pack (pack) or by a zero
    # native size (ebo) is harmless as a base; as a member it moves its neighbours when MSVC places it at an offset
    # the native alignment does not allow (pack), or always (ebo: 1 byte under MSVC, 0 natively)
    # per unit: a name is one class in a unit, another elsewhere (hkxnode.cpp's hkReferencedObject member is the real
    # 8-byte class; hkserializeutil.cpp's empty stand-in of that name is the one of native size 0)
    pack = {(r[0], r[1]): int(r[4]) for r in allrows if r[2] == 'align' and int(r[3]) < int(r[4])}
    ebo = {(r[0], r[1]) for r in allrows if r[2] == 'size' and str(r[4]) == '0'}
    for unit, rec, off, nm, t, b in allmembers:
        name = re.sub(r'\s*\[\d+\]$', '', t).strip()
        base = next(((unit, k) for k in (name, name.split('::')[-1].strip()) if (unit, k) in ebo or (unit, k) in pack),
                    (unit, name))
        if base in ebo:
            allrows.append((unit, rec, 'member %s' % nm, off, '%s (native size 0)' % base[1], 'ebo-member', b))
        elif base in pack and off % pack[base]:
            allrows.append((unit, rec, 'member %s' % nm, off, '%s (native align %d)' % (base[1], pack[base]),
                            'pack-member', b))
    allrows.sort(key=lambda r: tuple(str(x) for x in r))
    with open(a.out, 'w') as f:
        f.write('# fnvnx-layoutgate/1\n' + '\t'.join(COLUMNS) + '\n')
        for r in allrows:
            f.write('\t'.join(str(x) for x in r) + '\n')
    # an EBO2 base (only the FNVNX_EBO2 filler: native size 0, MSVC 1, by design - a 1-byte filler would move the
    # derived class's members) differs in its size alone; the member check above covers its use as a member, so the
    # record counts as an ebo2 base, not as a differing record
    ebo2 = {(r[0], r[1]) for r in allrows if r[2] == 'size' and str(r[4]) == '0' and r[5] == 'ebo'}
    ebo2 -= {(r[0], r[1]) for r in allrows if r[2] not in ('size', 'align') and not r[2].startswith('member')}
    recs = collections.defaultdict(set)
    for r in allrows:
        if not r[2].startswith(('member', 'probe-failed')) and (r[0], r[1]) not in ebo2: recs[(r[1], r[6])].add(r[2])
    failed = sorted({r[0] for r in allrows if r[2] == 'probe-failed'})
    rb = {k[0] for k in recs if k[1] == 1}
    rr = {k[0] for k in recs if k[1] == 0} - rb
    fb = sum(1 for r in allrows if r[6] == 1 and r[2].startswith('field'))
    causes = collections.Counter(r[5] for r in allrows if r[2] == 'size' or r[2].startswith('field'))
    mrows = [r for r in allrows if r[2].startswith('member')]
    summary = ('layoutgate: %d units, records msvc %d / probed %d; differing records %d (boot %d, rest %d), '
               'differing field rows %d (boot %d); pack / ebo members at risk %d (boot %d); ebo2 bases %d; causes %s%s' %
               (len(units), measured[0], measured[1], len(rb | rr), len(rb), len(rr),
                sum(1 for r in allrows if r[2].startswith('field')), fb, len(mrows), sum(1 for r in mrows if r[6] == 1),
                len({k[1] for k in ebo2}), dict(causes.most_common()),
                '; probe-failed %d (%s)' % (len(failed), ', '.join(failed[:10]) + (' ...' if len(failed) > 10 else ''))
                if failed else ''))
    print(summary, file=sys.stderr)
    open(a.out + '.summary', 'w').write(summary + '\n')


if __name__ == '__main__':
    main()
