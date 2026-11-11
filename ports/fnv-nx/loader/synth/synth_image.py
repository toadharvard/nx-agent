#!/usr/bin/env python3
"""Rebuild as much of FalloutNV.exe 1.4.0.525's data image (0x00fdf000..0x02100000) as the decomp itself defines,
without the exe, and say exactly what is still missing.

usage: synth_image.py --units build/units.json --objdir OBJDIR [--imports platform/win32/imports.tsv]
                      [--out synth.exe] [--report report.json] [--summary summary.txt] [--missing missing.tsv]
                      [--conflicts conflicts.tsv] [--state-out state.bin] [--cache phase1.pickle]
                      [--addrmap S2/out/addrmap.tsv --checks checks.json --violations violations.tsv]

OBJDIR holds the MSVC 2008 objects of the pristine decomp, u<index>.obj for units.json index <index> (stream
S11, tools/equiv). The decomp is byte-identical to the exe's code, so the objects also carry the data its units
define: string literals, float constants, vftables, RTTI, EH tables, initialised statics. units.json gives the
original address of every name a unit references; that places a data symbol at its original address. Sources,
in order of trust:

  object/unit    the unit's own units.json name of a symbol its object defines
  object/global  a decorated name some other unit binds to exactly one address
  name           __real@<hex>, __xmm@<hex> and ??_C@ literals short enough that the name spells every byte

Relocations inside placed data are resolved through the same names (vftable slots -> function addresses,
pointer tables -> literals); the four bytes of an unresolved one stay unknown. Non-COMDAT data is placed per
symbol (the decomp's units do not reproduce the original objects' section layout: anchors in one section
disagree), the size from the decorated type when it fixes it, a $SG literal through its terminator, otherwise up
to the last non-zero word, with the rest up to the next symbol as soft zero padding. Overlapping claims: better
rank, then more providing units; equal rank and support with different bytes: contested (unknown, written as 0).

The report carries coverage per region and kind (coverage(): known bytes from one object / identical copies /
several agreeing or disagreeing items, by rank; assumed, guessed and unknown bytes; the same for every address
units.json references), the boot globals of J-0057 (explain()), and with --checks the self-consistency checks of
synth_checks.py (pointers, vftable slots, RTTI chains, overlaps, $SG strings; violations by kind and owner).

Output (all derived from the decomp: local only, never commit):
  --out       PE32 shell: .text (virtual, no bytes), .rdata, .data(+.bss), .tls at the inferred original
              boundaries, plus a synthetic import directory (section .fnvsyn at the top of the window) built from
              S4's imports.tsv so S3's loader binds the IAT; maps with loader/ (fnvnx_pe_open..fnvnx_image_map).
  --report    JSON with every number (classes by section / subsystem, consistency, probes, boundaries).
  --missing   TSV of the referenced data addresses nobody provides, grouped by the first function that uses
              them in static startup order (initializers in address order, then WinMain's call tree).
"""
import argparse
import array
import bisect
import collections
import json
import os
import pickle
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import coffobj  # noqa: E402
import msvcname  # noqa: E402
from mkdataprobes import probes as data_probes  # noqa: E402

WIN_LO, WIN_HI = 0x00fdf000, 0x02100000
IMAGE_BASE = 0x00400000
TEXT_LO, TEXT_HI = 0x00401000, 0x00fdf000
INIT_LO, INIT_HI = 0x00f34eb0, 0x00fc7a70        # .text$yc: the dynamic initializers (STARTUP.md)
WINMAIN = 0x0086a850
SYN_VA = 0x020f0000                             # synthetic loader structures (top of the window)
PAGE = 0x1000

ST_NONE, ST_SOFT, ST_UNK, ST_HARD = 0, 1, 2, 3
TRUST = {'unit': 0, 'global': 1, 'name': 2, 'spill': 3}
SG_SHIFT_OK = set()          # units whose $SG numbering was matched (filled by phase 1)
PHASE1_VERSION = 3           # bump when Item or the object scan changes: older --cache pickles are rescanned
# addresses whose bytes the boot depends on before WinMain's own initialisation (J-0057): explained in the report
BOOT_GLOBALS = (('g_memoryManager', 0x011f6238, 4), ('allocator holder (NiMemManager)', 0x011f6080, 4),
                ('init options list', 0x011f6118, 4), ('BSNiAllocator COL word', 0x010a2524, 4),
                ('BSNiAllocator vftable', 0x010a2528, 56), ('run-once flag of FUN_00aa3050', 0x011f6229, 1))


def page_up(v):
    return (v + PAGE - 1) & ~(PAGE - 1)


def page_down(v):
    return v & ~(PAGE - 1)


def family(secname, writable, bss):
    if secname.startswith('.tls'):
        return 'tls'
    if bss:
        return 'bss'
    if secname.startswith('.CRT'):
        return 'crt'
    if secname.startswith('.xdata'):
        return 'eh'
    if secname.startswith('.rdata$r'):
        return 'rtti'
    return 'data' if writable else 'rdata'


def sym_kind(name):
    """what a data symbol is, from its name"""
    for p, k in (('??_C@', 'literal'), ('$SG', 'literal'), ('__real@', 'real'), ('__xmm@', 'real'),
                 ('??_7', 'vftable'), ('??_R', 'rtti'), ('__ehfuncinfo$', 'eh'), ('__unwindtable$', 'eh'),
                 ('__tryblocktable$', 'eh'), ('__catchsym$', 'eh'), ('__TI', 'eh'), ('__CT', 'eh'),
                 ('__sehtable$', 'eh'), ('??_8', 'vbtable'), ('__imp_', 'import')):
        if name.startswith(p):
            return k
    return 'var'


def subsystem(source):
    p = source.split('/')
    return p[1] if len(p) > 2 else '(root)'


# --------------------------------------------------------------------------------------------- names

class Names:
    """decorated name -> original address, per unit (units.json) and globally where unambiguous"""

    def __init__(self, units):
        self.unit = []
        self.sources = [u['source'] for u in units]
        glob = collections.defaultdict(set)
        self.funcs = {}
        self.defnames = {}         # address -> every symbol a unit defines there (definitions and fold aliases)
        for i, u in enumerate(units):
            m = {}
            for n, v in u.get('symbols', {}).items():
                m[n] = int(v, 16)
            for f in u['functions']:
                a = int(f['address'], 16)
                m[f['symbol']] = a
                for al in f.get('fold_aliases', []):
                    m[al] = a
                self.funcs.setdefault(a, (i, f['symbol'], f['size']))
                self.defnames.setdefault(a, set()).update([f['symbol']] + list(f.get('fold_aliases', [])))
            self.unit.append(m)
            for n, a in m.items():
                glob[n].add(a)
        self.glob = {n: (next(iter(s)) if len(s) == 1 else None) for n, s in glob.items()}
        self.func_starts = sorted(self.funcs)
        self.shift = {}

    def sg_shift(self, i, coff, stats):
        """MSVC numbers string literals of /GF-less units $SG<n> by an internal symbol counter; the objects of
        the build at hand can count differently from the build that wrote units.json (S11's force-included header
        declares templates: +43 in every C++ unit). The shift is the delta that maps the most of the unit's
        units.json $SG names onto $SG symbols the object defines; names are looked up through it."""
        mine = [int(n[3:]) for n in self.unit[i] if n.startswith('$SG') and n[3:].isdigit()]
        if not mine:
            return
        have = {int(s.name[3:]) for s in coff.symbols.values() if s.section > 0 and s.name.startswith('$SG')
                and s.name[3:].isdigit()}
        deltas = collections.Counter(b - a for a in mine for b in have if abs(b - a) < 100000)
        if not deltas:
            stats['sg_units_no_overlap'] += 1
            return
        d, n = deltas.most_common(1)[0]
        stats['sg_units'] += 1
        stats['sg_names'] += len(mine)
        stats['sg_names_hit'] += n
        stats['sg_shift_%d' % d] += 1
        if n * 10 >= len(mine) * 9:
            self.shift[i] = d
            stats['sg_units_shift_validated'] += 1

    def lookup(self, i, name, global_ok):
        d = self.shift.get(i)
        if d and name.startswith('$SG') and name[3:].isdigit():
            name = '$SG%d' % (int(name[3:]) - d)
        a = self.unit[i].get(name)
        if a is not None:
            return a, 'unit'
        if global_ok:
            a = self.glob.get(name)
            if a is not None:
                return a, 'global'
        return None, None


def global_ok(sym):
    """may a symbol be looked up in other units' names? decorated C++ names are unique; $SG, $S and plain C
    statics are numbered or named per object"""
    if sym.storage == coffobj.SYM_EXTERNAL:
        return True
    return sym.name.startswith('?') or sym.name.startswith('__real@') or sym.name.startswith('__xmm@')


# --------------------------------------------------------------------------------------------- phase 1

class Item:
    """bytes one object (or a name) says are at an original address. relocs: (offset, type, target name, offset
    into the target, resolved address or None) of every relocation inside the item (the checks follow them)"""
    __slots__ = ('addr', 'data', 'unk', 'hard', 'unit', 'name', 'fam', 'kind', 'src', 'comdat', 'nrel', 'nunres',
                 'secrel', 'spill_refs', 'copies', 'rank', 'relocs', 'idx', 'units', 'soff', 'exact')

    def __init__(self, addr, data, unk, hard, unit, name, fam, kind, src, comdat):
        self.addr, self.data, self.unk, self.hard = addr, data, unk, hard
        self.unit, self.name, self.fam, self.kind, self.src, self.comdat = unit, name, fam, kind, src, comdat
        self.nrel = self.nunres = 0
        self.secrel = []
        self.spill_refs = []
        self.relocs = []
        self.idx = -1
        self.copies, self.rank, self.units = 1, None, None
        self.soff = 0            # offset of the item's own symbol (a vftable's ??_7 follows its COL word)
        self.exact = True        # False: the hard extent is a guess (no size from the type, not COMDAT)

    @property
    def end(self):
        return self.addr + len(self.data)


def is_secsym(coff, s):
    return s.storage == coffobj.SYM_STATIC and s.naux > 0 and s.value == 0 and s.name == coff.sections[s.section].name


def string_extent(data, last_sig):
    """bytes a string literal ($SG) owns: up to and including its terminator, a NUL after the last character or a
    NUL pair after the last UTF-16 unit (every odd byte zero, at least two characters)"""
    if last_sig < 0:
        return min(len(data), 1)
    if last_sig >= 2 and not last_sig & 1 and not any(data[j] for j in range(1, last_sig + 1, 2)):
        return min(len(data), last_sig + 4)
    return min(len(data), last_sig + 2)


def process_object(i, coff, names, cls, stats, want_code=True):
    """items (placed and unplaced data), code references into the window, call edges"""
    names.sg_shift(i, coff, stats)
    secs = coff.sections
    by_sec = collections.defaultdict(list)
    for s in coff.symbols.values():
        if s.section > 0:
            by_sec[s.section].append(s)
    # ---- data items: symbol extents and their addresses
    placed = {}           # section -> sorted [(start, end, item)]
    items = []
    unplaced = []
    for si in range(1, len(secs)):
        sec = secs[si]
        if not sec.is_data or not sec.size:
            continue
        fam = family(sec.name, sec.writable, sec.is_bss)
        syms = sorted((s for s in by_sec[si] if not is_secsym(coff, s)), key=lambda s: s.value)
        groups = []
        for s in syms:
            if groups and groups[-1][0] == s.value:
                groups[-1][1].append(s)
            else:
                groups.append((s.value, [s]))
        lst = []
        for gi, (v, group) in enumerate(groups):
            start = 0 if (sec.is_comdat and gi == 0) else v
            end = groups[gi + 1][0] if gi + 1 < len(groups) else sec.size
            if end <= start:
                continue
            addr = src = None
            name = group[0].name
            for s in group:
                a, how = names.lookup(i, s.name, global_ok(s))
                if a is not None and (addr is None or TRUST[how] < TRUST[src]):
                    if addr is not None and a - (v - start) != addr:
                        stats['alias_disagree'] += 1
                    addr, src, name = a - (v - start), how, s.name
            # how many bytes the symbol itself owns (the rest is soft padding)
            size_info = None
            for s in group:
                size_info = msvcname.type_size(s.name) or size_info
            it = Item(addr, None, None, end - start, i, name, fam, sym_kind(name), src, sec.is_comdat)
            it.data = (v, start, end, size_info)
            lst.append((start, end, it))
            (items if addr is not None else unplaced).append(it)
        placed[si] = lst
    starts_of = {si: [x[0] for x in lst] for si, lst in placed.items()}

    def local_item(si, off):
        lst = placed.get(si)
        if not lst:
            return None
        k = bisect.bisect_right(starts_of[si], off) - 1
        if k >= 0 and lst[k][0] <= off < lst[k][1]:
            return lst[k]
        return None

    funcs_of = {}

    def code_point(si, off):
        """original address of code section si + off"""
        fl = funcs_of.get(si)
        if fl is None:
            fl = []
            for s in by_sec[si]:
                if is_secsym(coff, s):
                    continue
                a, _ = names.lookup(i, s.name, global_ok(s))
                if a is not None:
                    fl.append((s.value, a))
            fl.sort()
            funcs_of[si] = fl
        k = bisect.bisect_right(fl, (off, 0xffffffff)) - 1
        if k < 0:
            return None
        return fl[k][1] + off - fl[k][0]

    def target(sym, addend):
        """(base address, address of sym+addend, spill item or None)"""
        if sym.section > 0:
            sec = secs[sym.section]
            if is_secsym(coff, sym):
                if sec.is_data:
                    x = local_item(sym.section, addend)
                    if x and x[2].addr is not None:
                        p = x[2].addr + addend - x[0]
                        return p, p, None
                    return None, None, x[2] if x else None
                if sec.is_code:
                    p = code_point(sym.section, addend)
                    return p, p, None
                return None, None, None
            a, _ = names.lookup(i, sym.name, global_ok(sym))
            if a is not None:
                return a, (a + addend) & 0xffffffff, None
            if sec.is_data:
                x = local_item(sym.section, sym.value)
                if x and x[2].addr is not None:
                    p = x[2].addr + sym.value - x[0]
                    return p, (p + addend) & 0xffffffff, None
                return None, None, x[2] if x else None
            if sec.is_code:
                p = code_point(sym.section, sym.value)
                if p is not None:
                    return p, (p + addend) & 0xffffffff, None
            return None, None, None
        if sym.section == 0:
            a, _ = names.lookup(i, sym.name, True)
            if a is not None:
                return a, (a + addend) & 0xffffffff, None
            return None, None, ('ext', sym.name)
        if sym.section == -1:
            return sym.value, (sym.value + addend) & 0xffffffff, None
        return None, None, None

    code_names = {}

    def target_name(sym, addend):
        """(symbol name, offset) a relocation refers to; a section symbol stands for the item (data) or the
        function (code) at its offset"""
        if sym is None:
            return None, 0
        if sym.section > 0 and is_secsym(coff, sym):
            sec = secs[sym.section]
            if sec.is_data:
                x = local_item(sym.section, addend)
                return (x[2].name, addend - x[0]) if x else (sec.name, addend)
            if sec.is_code:
                fl = code_names.get(sym.section)
                if fl is None:
                    fl = sorted((s.value, s.name) for s in by_sec[sym.section] if not is_secsym(coff, s))
                    code_names[sym.section] = fl
                k = bisect.bisect_right(fl, (addend, '￿')) - 1
                return (fl[k][1], addend - fl[k][0]) if k >= 0 else (sec.name, addend)
            return sec.name, addend
        return sym.name, addend

    # ---- fill bytes of every item (placed or not: unplaced ones may be spilled later)
    for si, lst in placed.items():
        sec = secs[si]
        raw = sec.data if sec.data is not None else bytes(sec.size)
        rel = sorted(sec.relocs)
        roffs = [r[0] for r in rel]
        for start, end, it in lst:
            v, _s, _e, size_info = it.data
            data = bytearray(raw[start:end])
            unk = bytearray(end - start)
            last_sig = -1
            k = bisect.bisect_left(roffs, start)
            while k < len(rel) and rel[k][0] < end:
                off, symidx, typ = rel[k]
                k += 1
                o = off - start
                if o + 4 > len(data):
                    continue
                it.nrel += 1
                last_sig = max(last_sig, o + 3)
                tsym = coff.symbols.get(symidx)
                addend = struct.unpack_from('<I', data, o)[0]
                base, point, sp = target(tsym, addend) if tsym else (None, None, None)
                tn, toff = target_name(tsym, addend)
                it.relocs.append((o, typ, tn, toff, point))
                if point is not None and typ == coffobj.REL_DIR32:
                    struct.pack_into('<I', data, o, point)
                elif point is not None and typ == coffobj.REL_DIR32NB:
                    struct.pack_into('<I', data, o, (point - IMAGE_BASE) & 0xffffffff)
                elif point is not None and typ == coffobj.REL_SECREL:
                    it.secrel.append((o, point))
                    struct.pack_into('<I', data, o, 0)
                else:
                    it.nunres += 1
                    struct.pack_into('<I', data, o, 0)
                    unk[o:o + 4] = b'\1\1\1\1'
                    if sp is not None and typ == coffobj.REL_DIR32:
                        it.spill_refs.append((o, sp, addend if not isinstance(sp, tuple) else addend))
            for j in range(len(data) - 1, -1, -1):
                if data[j]:
                    last_sig = max(last_sig, j)
                    break
            # hard extent; exact when the object or the name fixes it (COMDAT, sized type, string terminator),
            # else a guess up to the last non-zero word (inexact claims yield to exact ones, see merge)
            vo = v - start
            exact = True
            if sec.is_comdat:
                hard = len(data)
            elif size_info and size_info[1]:
                hard = min(len(data), vo + size_info[0])
            elif it.name.startswith('$SG'):
                hard = string_extent(data, last_sig)      # through the terminator (J-0062: was the last word)
            else:
                lo = vo + (size_info[0] if size_info else 1)
                hard = min(len(data), max(lo, (last_sig + 4) & ~3))
                exact = False
            if sec.is_bss:
                hard = min(len(data), vo + (size_info[0] if size_info else 1))
            it.data, it.unk, it.hard, it.soff, it.exact = bytes(data), bytes(unk), hard, vo, exact
    for it in items:
        stats['items_' + it.src] += 1
    for it in unplaced:
        stats['unplaced_' + it.fam + '_' + it.kind] += 1

    # ---- code: references into the data window, call graph
    refs, edges, unres = [], [], 0
    if want_code:
        for si in range(1, len(secs)):
            sec = secs[si]
            if not sec.is_code or not sec.data or not sec.relocs:
                continue
            fl = []
            for s in by_sec[si]:
                if is_secsym(coff, s) or s.name.startswith('$'):
                    continue
                a, _ = names.lookup(i, s.name, global_ok(s))
                if a is not None and a in names.funcs:
                    fl.append((s.value, a))
            if not fl:
                continue
            fl.sort()
            fvals = [x[0] for x in fl]
            starts = sizes = None
            for off, symidx, typ in sec.relocs:
                k = bisect.bisect_right(fvals, off) - 1
                if k < 0:
                    continue
                fa = fl[k][1]
                tsym = coff.symbols.get(symidx)
                if tsym is None or off + 4 > len(sec.data):
                    continue
                addend = struct.unpack_from('<i' if typ == coffobj.REL_REL32 else '<I', sec.data, off)[0]
                base, point, _sp = target(tsym, addend)
                if base is None:
                    unres += 1
                    continue
                if typ == coffobj.REL_REL32:
                    if TEXT_LO <= base < TEXT_HI and off >= 1 and (sec.data[off - 1] in (0xe8, 0xe9) or
                                                                   (off >= 2 and sec.data[off - 2] == 0x0f)):
                        edges.append((fa, base))
                    continue
                if WIN_LO <= base < WIN_HI or WIN_LO <= point < WIN_HI:
                    if starts is None:
                        starts, sizes = cls.boundaries(sec.data, 0, len(sec.data))
                    use = cls.classify(sec.data, starts, sizes, off)
                    refs.append((fa, base, point, use))
                elif TEXT_LO <= base < TEXT_HI and typ == coffobj.REL_DIR32:
                    if starts is None:
                        starts, sizes = cls.boundaries(sec.data, 0, len(sec.data))
                    use = cls.classify(sec.data, starts, sizes, off)
                    if use in ('addr', 'arg', 'this'):
                        edges.append((fa, -base))       # address of a function taken (callback): kept apart
    stats['code_refs_unresolved'] += unres
    return items, unplaced, refs, edges


def phase1(units, objdir, names, limit=None, log=print):
    import x86refs
    cls = x86refs.Classifier()
    stats = collections.Counter()
    items, unplaced, refs, edges = [], [], [], []
    have = 0
    t0 = time.time()
    n = len(units) if limit is None else min(limit, len(units))
    for i in range(n):
        p = os.path.join(objdir, 'u%d.obj' % i)
        if not os.path.exists(p):
            stats['objects_missing'] += 1
            continue
        try:
            coff = coffobj.read(p)
        except (ValueError, struct.error) as e:
            stats['objects_bad'] += 1
            log('  u%d: %s' % (i, e))
            continue
        have += 1
        a, b, c, d = process_object(i, coff, names, cls, stats)
        items += a
        unplaced += b
        refs += c
        edges += d
        if have % 500 == 0:
            log('  %d objects, %d items, %d refs (%.0f s)' % (have, len(items), len(refs), time.time() - t0))
    stats['objects'] = have
    return dict(items=items, unplaced=unplaced, refs=refs, edges=edges, stats=stats, sg_shift=dict(names.shift),
                version=PHASE1_VERSION)


# --------------------------------------------------------------------------------------------- phase 2

def name_items(units, names, have_addrs):
    """items from names that spell their bytes, for referenced addresses no object provides"""
    out = []
    seen = set()
    for i, u in enumerate(units):
        for n, v in u.get('symbols', {}).items():
            a = int(v, 16)
            if not WIN_LO <= a < WIN_HI or a in have_addrs or (a, n) in seen:
                continue
            seen.add((a, n))
            b = msvcname.real_bytes(n)
            kind = 'real'
            if b is None:
                r = msvcname.literal_bytes(n)
                if r is None or not r[1]:
                    continue
                b, kind = r[0], 'literal'
            it = Item(a, b, bytes(len(b)), len(b), i, n, 'rdata', kind, 'name', True)
            out.append(it)
    return out


class Image:
    """the window's bytes (val), what is known about each (st: ST_*), and their provenance: how many distinct
    items claim a known value (ncl), whether one disagreed (dis), the winning item (win) and its rank"""

    def __init__(self):
        n = WIN_HI - WIN_LO
        self.val = bytearray(n)
        self.st = bytearray(n)
        self.ncl = bytearray(n)
        self.dis = bytearray(n)
        self.win = array.array('i', [-1]) * n
        self.rank = bytearray(b'\xff') * n

    def word(self, va):
        """the 32-bit value at va when all four bytes are known, else None"""
        o = va - WIN_LO
        if o < 0 or o + 4 > len(self.val) or any(s != ST_HARD for s in self.st[o:o + 4]):
            return None
        return struct.unpack_from('<I', self.val, o)[0]


def name_rank(it):
    """how far the name of an item can be trusted to place it: 0 the name spells the content and the content
    agrees (??_C@, __real@, __xmm@), 1 a decorated C++ name of the unit, 2 one from another unit, 3 a $SG
    number or plain C static (numbered per compile; units.json maps several to one address in /Od units)"""
    n = it.name
    if n.startswith(('__real@', '__xmm@')):
        b = msvcname.real_bytes(n)
        return 0 if b is not None and it.data[:len(b)] == b else 4
    if n.startswith('??_C@'):
        r = msvcname.literal_bytes(n)
        return 0 if r is not None and it.data[:len(r[0])] == r[0] else 4
    if n.startswith('?'):
        return 1 if it.src == 'unit' else 2
    if n.startswith('$SG') and it.unit in SG_SHIFT_OK:
        return 1          # numbering checked against the unit's own names (Names.sg_shift)
    return 3


def merge(items, report):
    """dedupe identical copies, check every overlap of distinct items, write the byte map: better rank first,
    then more providing units (majority); equal rank and equal support with different bytes: contested
    (left unknown)"""
    groups = collections.OrderedDict()
    for it in items:
        key = (it.addr, it.data, it.unk, it.hard)
        g = groups.get(key)
        if g is None:
            groups[key] = [it]
        else:
            g.append(it)
    distinct = []
    for g in groups.values():
        g.sort(key=lambda x: (TRUST[x.src], x.unit))
        rep = g[0]
        rep.units = sorted({x.unit for x in g})
        rep.copies = len(rep.units)
        rep.rank = min(name_rank(x) for x in g)
        distinct.append(rep)
    report['items'] = len(items)
    report['items_distinct'] = len(distinct)
    report['items_with_copies'] = sum(1 for d in distinct if d.copies > 1)
    report['copies_agreeing'] = sum(len(g) - 1 for g in groups.values())
    report['items_by_rank'] = dict(collections.Counter(d.rank for d in distinct))
    # overlaps between distinct items (pairs), by rank pair
    distinct.sort(key=lambda x: (x.addr, x.unit, x.name))
    for k, it in enumerate(distinct):
        it.idx = k
    active = []
    cons = collections.Counter()
    pairs = collections.Counter()
    conflicts = []
    bss_conflicts = []              # a .bss item over an initialised item's non-zero bytes
    for it in distinct:
        active = [a for a in active if a.end > it.addr]
        for a in active:
            lo, hi = it.addr, min(it.end, a.end)
            agree = diff = 0
            for x in range(lo, hi):
                oi, oa = x - it.addr, x - a.addr
                if oi < it.hard and oa < a.hard and not it.unk[oi] and not a.unk[oa]:
                    if it.data[oi] == a.data[oa]:
                        agree += 1
                    else:
                        diff += 1
            if (a.fam == 'bss') != (it.fam == 'bss'):
                other = it if a.fam == 'bss' else a
                key = 'bss-vs-' + other.fam
                cons[key] += 1
                nz = sum(1 for x in range(lo, hi) if x - other.addr < other.hard and other.data[x - other.addr])
                if nz:
                    cons[key + ':nonzero'] += 1
                    bss_conflicts.append((a, it, hi - lo - nz, nz))
                continue
            if a.fam == 'bss':
                continue
            rp = '%d-%d' % tuple(sorted((a.rank, it.rank)))
            if diff:
                cons['conflict'] += 1
                cons['conflict_bytes'] += diff
                pairs['conflict ' + rp] += 1
                conflicts.append((a, it, agree, diff))
            elif agree:
                cons['agree'] += 1
                cons['agree_bytes'] += agree
                pairs['agree ' + rp] += 1
            else:
                cons['overlap_soft_only'] += 1
        active.append(it)
    report['overlaps'] = dict(cons)
    report['overlaps_by_rank'] = dict(pairs)
    # byte map
    img = Image()
    val, st = img.val, img.st
    owner = {}                      # byte offset -> (rank, copies) of its hard value, only where contested
    best = bytearray(b'\xff') * len(val)     # rank of the hard byte's provider
    sup = array.array('H', bytes(2 * len(val)))   # support (providing units) of the hard byte
    ncl = img.ncl                   # distinct items with a known value for the byte (saturating at 255)
    dis = img.dis                   # 1: some claimant disagreed with the value written
    win = img.win                   # index (Item.idx) of the item whose value the byte holds, -1 none
    res = collections.Counter()
    order = sorted(distinct, key=lambda x: (x.rank, -x.copies, TRUST[x.src], 0 if x.comdat else 1, x.addr))
    for it in order:
        if it.fam == 'bss':
            continue
        base = it.addr - WIN_LO
        if base < 0 or it.end > WIN_HI:
            continue
        d, u, h = it.data, it.unk, it.hard
        r, c = it.rank, min(it.copies, 65535)
        for k in range(len(d)):
            p = base + k
            if k < h and not u[k]:
                if ncl[p] < 255:
                    ncl[p] += 1
                s = st[p]
                if s == ST_HARD:
                    if val[p] != d[k]:
                        dis[p] = 1
                        if best[p] == r and sup[p] == c:
                            res['contested_bytes'] += 1
                            st[p] = ST_UNK          # nobody wins: unknown, written as 0 like an unresolved
                            val[p] = 0              # relocation (J-0062: kept the first claimant's byte)
                            win[p] = -1
                            owner[p] = (r, c)
                        else:
                            res['lost_bytes'] += 1
                elif p in owner:
                    res['contested_bytes'] += 1     # third claim on a contested byte: stays unknown
                else:
                    val[p] = d[k]
                    st[p] = ST_HARD
                    best[p] = r
                    sup[p] = c
                    win[p] = it.idx
            elif u[k]:
                if st[p] < ST_UNK:
                    st[p] = ST_UNK
            elif st[p] == ST_NONE:
                st[p] = ST_SOFT
    report['conflict_resolution'] = dict(res)
    img.rank = best
    img.bss_conflicts = bss_conflicts
    return img, distinct, conflicts


# --------------------------------------------------------------------------------------------- phase 3

def infer_sections(distinct, imports, report):
    """original section boundaries from where each kind of evidence lands"""
    ev = collections.defaultdict(list)
    for it in distinct:
        if it.src not in ('unit', 'global'):
            continue
        f = {'rdata': 'rdata', 'rtti': 'rdata', 'eh': 'rdata', 'crt': 'rdata', 'data': 'data', 'bss': 'bss',
             'tls': 'tls'}[it.fam]
        ev[f].append(it.addr)
    for a in imports:
        ev['iat'].append(a)
    for k in ev:
        ev[k].sort()
    summ = {}
    for k, v in ev.items():
        n = len(v)
        summ[k] = dict(n=n, min='0x%08x' % v[0], p1='0x%08x' % v[n // 100], p50='0x%08x' % v[n // 2],
                       p99='0x%08x' % v[min(n - 1, n * 99 // 100)], max='0x%08x' % v[-1])
    report['evidence'] = summ
    tls = ev.get('tls', [])
    # TLS: the template is its own section; the densest page run of .tls$ evidence
    tls_lo = page_down(collections.Counter(page_down(a) for a in tls).most_common(1)[0][0]) if tls else None
    tls_hi = page_up(max(a for a in tls if tls_lo <= a < tls_lo + 0x10000) + 1) if tls else None

    def split(lo_list, hi_list, below=None):
        """boundary b minimizing lo evidence >= b plus hi evidence < b (candidates: the evidence itself)"""
        pts = sorted(set([x for x in lo_list] + [x for x in hi_list]))
        lo_s, hi_s = sorted(lo_list), sorted(hi_list)
        best = None
        for b in pts + [pts[-1] + 1] if pts else []:
            wrong = (len(lo_s) - bisect.bisect_left(lo_s, b)) + bisect.bisect_left(hi_s, b)
            if best is None or wrong < best[0]:
                best = (wrong, b)
        return best

    rdata = [a for a in ev.get('rdata', []) + ev.get('iat', []) if tls_lo is None or a < tls_lo]
    data_side = [a for a in ev.get('data', []) + ev.get('bss', []) if tls_lo is None or a < tls_lo]
    w1, b1 = split(rdata, data_side)
    lo_ok = [a for a in rdata if a < b1]
    hi_ok = [a for a in data_side if a >= b1]
    # MSVC lays sections out back to back (each starts on the page after the previous one ends), so .rdata
    # runs up to the page where the first .data evidence lies
    data_lo = max(page_down(min(hi_ok)), page_up(max(lo_ok) + 1))
    rdata_hi = data_lo
    data = [a for a in ev.get('data', []) if a >= data_lo and (tls_lo is None or a < tls_lo)]
    bss = [a for a in ev.get('bss', []) if a >= data_lo and (tls_lo is None or a < tls_lo)]
    w2, b2 = split(data, bss)
    data_ok = [a for a in data if a < b2]
    bss_ok = [a for a in bss if a >= b2]
    raw_hi = max(data_ok) + 1
    bss_lo = min(bss_ok)
    sec = dict(rdata=(WIN_LO, rdata_hi), data=(data_lo, tls_lo if tls_lo else page_up(max(bss_ok) + 1)),
               data_raw_hi=raw_hi, bss_lo_evidence=bss_lo, tls=(tls_lo, tls_hi) if tls_lo else None)
    report['sections'] = {
        'rdata': ['0x%08x' % sec['rdata'][0], '0x%08x' % sec['rdata'][1]],
        'rdata_last_evidence': '0x%08x' % max(lo_ok),
        'data_first_evidence': '0x%08x' % min(hi_ok),
        'rdata_data_misplaced_evidence': w1,
        'data': ['0x%08x' % sec['data'][0], '0x%08x' % sec['data'][1]],
        'data_raw_last_evidence': '0x%08x' % (raw_hi - 1),
        'bss_first_evidence': '0x%08x' % bss_lo,
        'data_bss_misplaced_evidence': w2,
        'data_bss_misplaced': ['0x%08x' % a for a in sorted([a for a in data if a >= b2] + [a for a in bss if a < b2])][:50],
        'tls': ['0x%08x' % tls_lo, '0x%08x' % tls_hi] if tls_lo else None,
    }
    return sec


def region_of(sec, a):
    if sec['tls'] and sec['tls'][0] <= a < sec['tls'][1]:
        return 'tls'
    lo, hi = sec['rdata']
    if lo <= a < hi:
        return 'rdata'
    lo, hi = sec['data']
    if lo <= a < hi:
        # the .data/.bss split lies between the last initialised and the first uninitialised evidence
        if a < sec['data_raw_hi']:
            return 'data'
        if a >= sec['bss_lo_evidence']:
            return 'bss'
        return 'data?bss'
    return 'outside'


# --------------------------------------------------------------------------------------------- phase 4/5

def startup_order(names, edges):
    """static startup order: every initializer in address order with its call tree (preorder), then WinMain's"""
    calls = collections.defaultdict(list)
    for a, b in edges:
        if b > 0:
            calls[a].append(b)
    for a in calls:
        calls[a] = sorted(set(calls[a]), key=calls[a].index)
    order = {}
    roots = [a for a in names.func_starts if INIT_LO <= a < INIT_HI]
    phase = {}
    for rlist, ph in ((roots, 'init'), ([WINMAIN], 'winmain')):
        for r in rlist:
            if r in order:
                continue
            stack = [r]
            while stack:
                f = stack.pop()
                if f in order:
                    continue
                order[f] = len(order)
                phase[f] = ph
                for c in reversed(calls.get(f, ())):
                    if c not in order:
                        stack.append(c)
    return order, phase, len(roots)


def classify(units, names, img, sec, ph1, imports, order, phase, report, missing_out=None):
    refs = ph1['refs']
    # referenced data addresses (units.json) with their names and referencing units
    ref = collections.defaultdict(lambda: [set(), set()])
    for i, u in enumerate(units):
        for n, v in u.get('symbols', {}).items():
            a = int(v, 16)
            if WIN_LO <= a < WIN_HI and a not in names.funcs:
                ref[a][0].add(n)
                ref[a][1].add(i)
    # uses by code
    init_w = set()
    users = collections.defaultdict(set)
    for fa, base, point, use in refs:
        for a in {base, point}:
            if a in ref:
                users[a].add((fa, use))
                if INIT_LO <= fa < INIT_HI and use in ('store', 'rmw', 'this'):
                    init_w.add(a)
    val, st = img.val, img.st
    cls = {}
    size_of = {}
    for a, (ns, us) in ref.items():
        size = 1
        for n in ns:
            t = msvcname.type_size(n)
            if t and t[1]:
                size = max(size, t[0])
        size = min(size, WIN_HI - a)
        size_of[a] = size
        reg = region_of(sec, a)
        sts = st[a - WIN_LO:a - WIN_LO + size]
        if any(n.startswith('__imp_') for n in ns) or a in imports:
            c = 'import' if a in imports else 'import-unnamed'
        elif reg == 'outside':
            c = 'outside'
        elif all(s == ST_HARD for s in sts):
            c = 'object'
        elif any(s in (ST_HARD, ST_UNK) for s in sts):
            c = 'object-partial'
        elif a in init_w:
            c = 'init'
        elif reg == 'bss':
            c = 'bss'
        elif reg == 'data?bss':
            c = 'bss?'
        else:
            c = 'missing'
        cls[a] = (c, reg)
    # aggregates
    by_reg = collections.defaultdict(collections.Counter)
    for a, (c, reg) in cls.items():
        by_reg[reg][c] += 1
        by_reg['all'][c] += 1
    report['classes_by_section'] = {k: dict(v) for k, v in sorted(by_reg.items())}
    by_sub = collections.defaultdict(collections.Counter)
    for a, (c, reg) in cls.items():
        for s in {subsystem(units[i]['source']) for i in ref[a][1]}:
            by_sub[s][c] += 1
    report['classes_by_subsystem'] = {k: dict(v) for k, v in sorted(by_sub.items(), key=lambda kv: -sum(kv[1].values()))}
    # missing by first consumer in startup order
    miss = [a for a, (c, reg) in cls.items() if c in ('missing', 'object-partial')]
    groups = collections.defaultdict(list)
    for a in miss:
        best = None
        for fa, use in users.get(a, ()):
            o = order.get(fa)
            if o is not None and (best is None or o < best[0]):
                best = (o, fa, use)
        key = best[1] if best else None
        groups[key].append((a, best[2] if best else '-'))
    reached = {k: v for k, v in groups.items() if k is not None}
    tree_w = set()
    for fa, base, point, use in refs:
        if phase.get(fa) == 'init' and use in ('store', 'rmw', 'this'):
            tree_w.add(base)
    first = collections.Counter()
    for k, v in reached.items():
        first[phase[k]] += len([x for x in v if cls[x[0]][0] == 'missing'])
    report['missing'] = {
        'missing': sum(1 for a in miss if cls[a][0] == 'missing'),
        'partial': sum(1 for a in miss if cls[a][0] == 'object-partial'),
        'missing_first_used_in_init_tree': first['init'],
        'missing_first_used_in_winmain_tree': first['winmain'],
        'missing_not_reached_statically': len([x for x in groups.get(None, []) if cls[x[0]][0] == 'missing']),
        'consumer_functions': len(reached),
        'missing_written_somewhere_in_init_tree': sum(1 for a in miss if cls[a][0] == 'missing' and a in tree_w),
        'missing_by_use_of_first_consumer': dict(collections.Counter(
            u for k, v in reached.items() for a, u in v if cls[a][0] == 'missing')),
    }
    top = sorted(reached.items(), key=lambda kv: order[kv[0]])
    report['missing']['first_consumers_in_order'] = [
        dict(func='0x%08x' % f, phase=phase[f], order=order[f], missing=len(v)) for f, v in top[:40]]
    report['missing']['largest_consumers'] = [
        dict(func='0x%08x' % f, phase=phase[f], order=order[f], missing=len(v))
        for f, v in sorted(reached.items(), key=lambda kv: -len(kv[1]))[:25]]
    if missing_out:
        with open(missing_out, 'w') as f:
            f.write('# consumer\tphase\torder\tconsumer_name\taddress\tclass\tsection\tuse\tnames\n')
            for fa, v in top + ([(None, groups[None])] if None in groups else []):
                fname = names.funcs[fa][1] if fa in names.funcs else '-'
                for a, use in sorted(v):
                    f.write('%s\t%s\t%s\t%s\t0x%08x\t%s\t%s\t%s\t%s\n' % (
                        '0x%08x' % fa if fa else '-', phase.get(fa, '-'), order.get(fa, '-'), fname, a, cls[a][0],
                        cls[a][1], use, ','.join(sorted(ref[a][0])[:4])))
    return cls, size_of, ref


def byte_coverage(img, sec, report):
    st = img.st
    out = {}
    regions = [('rdata', sec['rdata'][0], sec['rdata'][1]),
               ('data', sec['data'][0], sec['data_raw_hi']),
               ('bss', sec['bss_lo_evidence'], sec['data'][1])]
    if sec['tls']:
        regions.append(('tls', sec['tls'][0], sec['tls'][1]))
    for name, lo, hi in regions:
        seg = st[lo - WIN_LO:hi - WIN_LO]
        c = collections.Counter(seg)
        out[name] = dict(range=['0x%08x' % lo, '0x%08x' % hi], bytes=hi - lo, hard=c[ST_HARD], unknown_reloc=c[ST_UNK],
                         soft_zero=c[ST_SOFT], none=c[ST_NONE])
    report['bytes'] = out


PROV_NAMES = ('one-object', 'copies-agree', 'merged-agree', 'merged-disagree')


def item_kind(it):
    """coverage kind of an item: what the region tables call vftables, RTTI, string pools, ..."""
    if it.fam == 'tls':
        return 'tls-vars'
    if it.fam == 'crt':
        return 'crt-tables'
    if it.kind == 'literal':
        return 'strings-sg' if it.name.startswith('$SG') else 'strings-pool'
    if it.kind == 'rtti' or it.fam == 'rtti':
        return 'rtti'
    if it.kind in ('vftable', 'vbtable', 'real', 'eh'):
        return {'vftable': 'vftables', 'vbtable': 'vbtables', 'real': 'reals', 'eh': 'eh'}[it.kind]
    if it.fam == 'eh':
        return 'eh'
    return 'vars'


def addr_kind(names_at):
    kinds = {sym_kind(n) for n in names_at}
    for k, label in (('import', 'imports'), ('vftable', 'vftables'), ('rtti', 'rtti'), ('real', 'reals'),
                     ('eh', 'eh'), ('vbtable', 'vbtables')):
        if k in kinds:
            return label
    if 'literal' in kinds:
        return 'strings-sg' if all(n.startswith('$SG') for n in names_at if sym_kind(n) == 'literal') \
            else 'strings-pool'
    return 'vars'


def coverage(img, distinct, sec, iat_range, cls, size_of, ref, report):
    """bytes and referenced addresses per region (.rdata without the IAT, IAT, .data, .bss, .tls) and per kind
    (vftables, RTTI, string pools, ...). Known bytes by provenance: exactly one object, identical copies from
    several objects, several distinct items agreeing, several disagreeing (decided by rank or majority); by the
    rank of the provider (0 content names, 1 the unit's decorated name, 2 another unit's, 3 a plain per-unit name:
    weak_name); inexact_extent: known bytes of a variable whose size neither its type nor a COMDAT fixes (owned
    up to its last non-zero word). Assumed: soft zero padding, .bss zero, IAT words the loader binds. Guessed:
    bytes no object states (soft zero padding, items made from a name alone). Unknown: unresolved relocation or
    contested."""
    import numpy as np
    st = np.frombuffer(bytes(img.st), np.uint8)
    ncl = np.frombuffer(bytes(img.ncl), np.uint8)
    dis = np.frombuffer(bytes(img.dis), np.uint8)
    rank = np.frombuffer(bytes(img.rank), np.uint8)
    win = np.frombuffer(img.win.tobytes(), np.int32)
    copies = np.array([it.copies for it in distinct] + [0], np.int32)
    fromname = np.array([it.src == 'name' for it in distinct] + [False], bool)
    inexact = np.array([not it.exact for it in distinct] + [False], bool)
    hard = st == ST_HARD
    w = np.where(win >= 0, win, len(distinct))
    prov = np.full(len(st), 255, np.uint8)
    one = hard & (win >= 0) & (ncl <= 1)
    prov[one & (copies[w] <= 1)] = 0
    prov[one & (copies[w] > 1)] = 1
    prov[hard & (win >= 0) & (ncl > 1) & (dis == 0)] = 2
    prov[hard & (win >= 0) & (ncl > 1) & (dis != 0)] = 3
    weak = hard & (win >= 0) & (rank == 3)
    named = hard & (win >= 0) & fromname[w]

    def count(mask, total_bytes):
        m = mask
        out = dict(bytes=int(total_bytes), known=int((m & hard & (win >= 0)).sum()))
        for k, n in enumerate(PROV_NAMES):
            out[n] = int((m & (prov == k)).sum())
        out['by_rank'] = {str(r): int((m & hard & (win >= 0) & (rank == r)).sum()) for r in range(4)}
        out['weak_name'] = int((m & weak).sum())
        out['name_only'] = int((m & named).sum())
        out['inexact_extent'] = int((m & hard & (win >= 0) & inexact[w]).sum())
        out['unknown'] = int((m & (st == ST_UNK)).sum())
        out['soft_zero'] = int((m & (st == ST_SOFT)).sum())
        out['loader_bound'] = int((m & hard & (win < 0)).sum())
        out['none'] = int((m & (st == ST_NONE)).sum())
        out['guessed'] = out['soft_zero'] + out['name_only']
        return out

    regions = [('rdata', sec['rdata'][0], sec['rdata'][1]), ('data', sec['data'][0], sec['data_raw_hi']),
               ('data?bss', sec['data_raw_hi'], sec['bss_lo_evidence']),
               ('bss', sec['bss_lo_evidence'], sec['data'][1])]
    if sec['tls']:
        regions.append(('tls', sec['tls'][0], sec['tls'][1]))
    out = {}
    for name, lo, hi in regions:
        m = np.zeros(len(st), bool)
        m[lo - WIN_LO:hi - WIN_LO] = True
        if name == 'rdata' and iat_range:
            m[iat_range[0] - WIN_LO:iat_range[1] - WIN_LO] = False
        out[name] = count(m, m.sum())
        if name == 'bss':
            out[name]['zero_by_section'] = out[name].pop('none')
    if iat_range:
        m = np.zeros(len(st), bool)
        m[iat_range[0] - WIN_LO:iat_range[1] - WIN_LO] = True
        out['iat'] = count(m, m.sum())
    kinds = collections.defaultdict(lambda: np.zeros(len(st), bool))
    for it in distinct:
        if it.fam == 'bss' or it.addr < WIN_LO or it.end > WIN_HI:
            continue
        kinds[item_kind(it)][it.addr - WIN_LO:it.addr - WIN_LO + it.hard] = True
    by_kind = {k: count(m, m.sum()) for k, m in sorted(kinds.items())}
    report['coverage_bytes'] = dict(regions=out, kinds=by_kind)
    # referenced addresses (units.json): what the image defines there, and from how many objects
    reg_rows = collections.defaultdict(collections.Counter)
    kind_rows = collections.defaultdict(collections.Counter)
    for a, (c, reg) in cls.items():
        if iat_range and iat_range[0] <= a < iat_range[1]:
            reg = 'iat'
        size = size_of.get(a, 1)
        o = a - WIN_LO
        rows = (reg_rows[reg], kind_rows[addr_kind(ref[a][0])], reg_rows['all'])
        if c == 'object':
            p = prov[o:o + size]
            p = p[p != 255]
            if not len(p):
                tag = 'defined:loader-bound'
            else:
                tag = 'defined:' + PROV_NAMES[int(p.max())]
            sl = slice(o, o + size)
            known = hard[sl] & (win[sl] >= 0)
            r = int(rank[sl][known].max()) if known.any() else None
            extra = ['defined', tag] + (['defined:rank%d' % r] if r is not None else []) + \
                (['defined:weak-name'] if weak[sl].any() else []) + \
                (['defined:inexact'] if (known & inexact[w[sl]]).any() else []) + \
                (['defined:name-only'] if named[sl].any() else [])
        else:
            extra = [c]
        for row in rows:
            row['referenced'] += 1
            for e in extra:
                row[e] += 1
    report['coverage_addresses'] = dict(regions={k: dict(v) for k, v in sorted(reg_rows.items())},
                                        kinds={k: dict(v) for k, v in sorted(kind_rows.items())})


def explain(img, distinct, sec, iat_range, ref, addrs, cls=None, refs=(), names=None):
    """what the image holds at a few addresses (state per byte: - none, s soft zero, U unknown, K known), who put
    it there, how classify() sees the address, and which functions use it (code relocations of the objects)"""
    want = {a for _, a, _ in addrs}
    users = collections.defaultdict(set)
    for fa, base, point, use in refs:
        if base in want:
            users[base].add((fa, use))
    out = []
    for label, a, size in addrs:
        o = a - WIN_LO
        sts = ''.join('-sUK'[s] for s in img.st[o:o + size])
        provs = sorted({img.win[o + k] for k in range(size) if img.win[o + k] >= 0} |
                       {it.idx for it in distinct if it.addr < a + size and a < it.addr + len(it.data)})
        fname = (lambda f: names.funcs[f][1] if names and f in names.funcs else '-')
        out.append(dict(label=label, addr='0x%08x' % a, size=size, region=region_of(sec, a),
                        state=sts, value=bytes(img.val[o:o + size]).hex(),
                        cls=cls.get(a, ('unreferenced', '-'))[0] if cls is not None else None,
                        users=sorted('0x%08x %s %s' % (f, use, fname(f)[:48]) for f, use in users[a])[:40],
                        names=sorted(ref[a][0])[:8] if a in ref else [],
                        providers=[dict(name=distinct[k].name, unit=distinct[k].unit, units=distinct[k].copies,
                                        addr='0x%08x' % distinct[k].addr, rank=distinct[k].rank,
                                        relocs=[(o2, t, n, off, None if p is None else '0x%08x' % p)
                                                for o2, t, n, off, p in distinct[k].relocs][:20])
                                   for k in provs]))
    return out


def check_probes(units, img, report):
    """S3's data probes (loader/tools/mkdataprobes.py: literal names, RTTI names) against the synthetic bytes.
    mkdataprobes keeps the first unit's spelling of an address; where units spell one address differently
    (struct/class keys of a TypeDescriptor), a match with another unit's spelling is counted apart"""
    from mkdataprobes import decode_literal
    pr = data_probes({'units': units})
    variants = collections.defaultdict(set)
    for u in units:
        for name, v in u.get('symbols', {}).items():
            va = int(v, 16)
            if name.startswith('??_C@_0'):
                b = decode_literal(name)
                if b and len(b) >= 4:
                    variants[va].add(b)
            elif name.startswith('??_R0') and name.endswith('@8'):
                variants[va + 8].add(('.' + name[5:-2]).encode('latin-1'))
    res = collections.Counter()
    bad = []
    for va, b in pr:
        if not WIN_LO <= va < WIN_HI - len(b):
            res['outside'] += 1
            continue
        o = va - WIN_LO
        sts = img.st[o:o + len(b)]
        if len(variants[va]) > 1:
            res['probes_with_disagreeing_units'] += 1
        if all(s == ST_HARD for s in sts):
            have = bytes(img.val[o:o + len(b)])
            if have == b:
                res['match'] += 1
            elif any(have[:len(x)] == x for x in variants[va]):
                res['match_other_units_spelling'] += 1
            else:
                res['mismatch'] += 1
                bad.append('0x%08x' % va)
        elif any(s == ST_HARD for s in sts):
            n = sum(1 for k, s in enumerate(sts) if s == ST_HARD and img.val[o + k] != b[k])
            res['partial_mismatch' if n else 'partial_match'] += 1
        else:
            res['uncovered'] += 1
    report['probes'] = dict(res, total=len(pr), mismatches=bad[:30])


def check_vftables(distinct, names, report):
    slots = collections.Counter()
    for it in distinct:
        if it.kind != 'vftable' or it.src not in ('unit', 'global'):
            continue
        for k in range(4, len(it.data) - 3, 4):
            if it.unk[k]:
                slots['unresolved'] += 1
                continue
            v = struct.unpack_from('<I', it.data, k)[0]
            if v in names.funcs:
                slots['function_start'] += 1
            elif TEXT_LO <= v < TEXT_HI:
                slots['text_other'] += 1
            else:
                slots['not_code'] += 1
        slots['col_word_unresolved' if it.unk[0] else 'col_word_resolved'] += 1
    report['vftable_slots'] = dict(slots)


# --------------------------------------------------------------------------------------------- PE writer

STATE_CODES = {0: 'missing', 1: 'soft-zero', 2: 'unknown', 3: 'known', 4: 'bss', 5: 'iat', 6: 'no-section'}


def write_state(path, img, sec, iat_range):
    """what the synthetic image knows about each byte of the window (for tests/s14: classify what the guest
    reads); bss = zero is the original value, iat = bound by the loader"""
    st = bytearray(img.st)
    lo, hi = sec['bss_lo_evidence'], sec['data'][1]
    st[lo - WIN_LO:hi - WIN_LO] = b'\4' * (hi - lo)
    if iat_range:
        st[iat_range[0] - WIN_LO:iat_range[1] - WIN_LO] = b'\5' * (iat_range[1] - iat_range[0])
    ends = [sec['rdata'], sec['data']] + ([sec['tls']] if sec['tls'] else [])
    for x in range(WIN_LO, WIN_HI, PAGE):
        if not any(a <= x < b for a, b in ends):
            st[x - WIN_LO:x - WIN_LO + PAGE] = b'\6' * PAGE
    with open(path, 'wb') as f:
        f.write(bytes(st))


def read_imports(path):
    """iat slot -> (dll, api) from S4's imports.tsv"""
    out = {}
    if not path or not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            p = line.rstrip('\n').split('\t')
            dll, api = p[1].rstrip('?'), p[2]
            if dll.startswith('(') or not api or ' ' in api or '?' in api:
                continue            # not an IAT slot (decomp mislabel) / slot whose name S4 could not settle
            out[int(p[0], 16)] = (dll, api)
    return out


def build_import_section(imports, base_va):
    """descriptors + INT + hint/name + DLL names for the IAT slots in imports (one descriptor per run of
    consecutive slots of one DLL); returns (bytes, import dir rva, size)"""
    runs = []
    for a in sorted(imports):
        dll, api = imports[a]
        if runs and runs[-1][0] == dll and runs[-1][2] + 4 == a:
            runs[-1][1].append(api)
            runs[-1][2] = a
        else:
            runs.append([dll, [api], a, a])
    nd = len(runs)
    buf = bytearray(20 * (nd + 1))
    strings = bytearray()
    ints = bytearray()
    desc = []
    for dll, apis, last, first in runs:
        int_off = len(ints)
        for api in apis:
            hn = len(strings)
            strings += b'\0\0' + api.encode() + b'\0'
            if len(strings) & 1:
                strings += b'\0'
            ints += struct.pack('<I', hn)          # patched below
        ints += b'\0\0\0\0'
        dn = len(strings)
        strings += (dll + '.dll').encode() + b'\0'
        desc.append((int_off, len(apis), dn, first))
    int_base = len(buf)
    str_base = int_base + len(ints)
    ints = bytearray(ints)
    for int_off, n, dn, first in desc:
        for k in range(n):
            hn = struct.unpack_from('<I', ints, int_off + 4 * k)[0]
            struct.pack_into('<I', ints, int_off + 4 * k, base_va - IMAGE_BASE + str_base + hn)
    for d, (int_off, n, dn, first) in enumerate(desc):
        struct.pack_into('<IIIII', buf, 20 * d, base_va - IMAGE_BASE + int_base + int_off, 0, 0,
                         base_va - IMAGE_BASE + str_base + dn, first - IMAGE_BASE)
    out = bytes(buf + ints + strings)
    return out, base_va - IMAGE_BASE, 20 * (nd + 1), len(runs)


def write_pe(path, img, sec, imports, iat_range, tls_items):
    fa = 0x200
    secs = []
    # (name, va, vsize, raw bytes, characteristics)
    secs.append(('.text', TEXT_LO, TEXT_HI - TEXT_LO, b'', 0x60000020))
    r0, r1 = sec['rdata']
    secs.append(('.rdata', r0, r1 - r0, bytes(img.val[r0 - WIN_LO:r1 - WIN_LO]), 0x40000040))
    d0, d1 = sec['data']
    raw_hi = min(d1, (sec['data_raw_hi'] + fa - 1) & ~(fa - 1))
    secs.append(('.data', d0, d1 - d0, bytes(img.val[d0 - WIN_LO:raw_hi - WIN_LO]), 0xC0000040))
    if sec['tls']:
        t0, t1 = sec['tls']
        secs.append(('.tls', t0, t1 - t0, bytes(img.val[t0 - WIN_LO:t1 - WIN_LO]), 0xC0000040))
    imp, imp_rva, imp_size, nruns = build_import_section(imports, SYN_VA)
    secs.append(('.fnvsyn', SYN_VA, page_up(len(imp)), imp, 0x40000040))
    nsec = len(secs)
    hdr_size = (0x40 + 4 + 20 + 224 + 40 * nsec + fa - 1) & ~(fa - 1)
    out = bytearray(hdr_size)
    out[0:2] = b'MZ'
    struct.pack_into('<I', out, 0x3c, 0x40)
    out[0x40:0x44] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', out, 0x44, 0x14c, nsec, 0x4b43b8d5, 0, 0, 224, 0x0102 | 0x0020)
    opt = 0x58
    size_of_image = WIN_HI - IMAGE_BASE
    struct.pack_into('<HBBIIIIII', out, opt, 0x10b, 9, 0, 0, 0, 0, 0, 0x1000, 0xBDF000)
    struct.pack_into('<IIIHHHHHHIIIIHHIIIIII', out, opt + 28, IMAGE_BASE, 0x1000, fa, 5, 0, 0, 0, 5, 0, 0,
                     size_of_image, hdr_size, 0, 2, 0x8000, 0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    dirs = [(0, 0)] * 16
    dirs[1] = (imp_rva, imp_size)
    if iat_range:
        dirs[12] = (iat_range[0] - IMAGE_BASE, iat_range[1] - iat_range[0])
    for k, (rva, size) in enumerate(dirs):
        struct.pack_into('<II', out, opt + 96 + 8 * k, rva, size)
    sh = opt + 224
    raw_off = hdr_size
    blobs = []
    for k, (name, va, vsize, raw, ch) in enumerate(secs):
        rawsize = (len(raw) + fa - 1) & ~(fa - 1) if raw else 0
        struct.pack_into('<8sIIIIIIHHI', out, sh + 40 * k, name.encode(), vsize, va - IMAGE_BASE, rawsize,
                         raw_off if rawsize else 0, 0, 0, 0, 0, ch)
        if rawsize:
            blobs.append(raw + bytes(rawsize - len(raw)))
            raw_off += rawsize
    with open(path, 'wb') as f:
        f.write(out)
        for b in blobs:
            f.write(b)
    return dict(sections=[(n, '0x%08x' % va, vs, len(r)) for n, va, vs, r, _ in secs], import_runs=nruns,
                import_slots=len(imports), file_bytes=raw_off)


# --------------------------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--units', required=True)
    ap.add_argument('--objdir', required=True)
    ap.add_argument('--imports', default=os.path.join(HERE, '..', '..', 'platform', 'win32', 'imports.tsv'))
    ap.add_argument('--out')
    ap.add_argument('--report')
    ap.add_argument('--summary')
    ap.add_argument('--missing')
    ap.add_argument('--conflicts')
    ap.add_argument('--state-out', help='per-byte provenance of the window (one byte each, codes in STATE_CODES)')
    ap.add_argument('--cache', help='pickle of the object scan (reused when present)')
    ap.add_argument('--limit', type=int, help='first N units only (tests)')
    ap.add_argument('--addrmap', help="S2's out/addrmap.tsv: code addresses the pointer and vftable checks accept "
                                      '(default: the functions units.json defines)')
    ap.add_argument('--checks', help='self-consistency checks (synth_checks.py) as JSON')
    ap.add_argument('--violations', help='every check violation as TSV (check, kind, owner, where, detail)')
    ap.add_argument('--hints-out', help='S3 vftable hints for the COL-less vftables the checks find (needs the checks)')
    a = ap.parse_args(argv)
    t0 = time.time()
    with open(a.units) as f:
        units = json.load(f)['units']
    names = Names(units)
    print('units %d, names %d, functions %d' % (len(units), len(names.glob), len(names.funcs)))
    ph1 = None
    if a.cache and os.path.exists(a.cache):
        try:
            with open(a.cache, 'rb') as f:
                ph1 = pickle.load(f)
        except (pickle.UnpicklingError, AttributeError, TypeError, EOFError):
            ph1 = None
        if ph1 is not None and ph1.get('version') != PHASE1_VERSION:
            ph1 = None
        print('object scan from cache %s' % a.cache if ph1 else 'cache %s is stale: rescanning' % a.cache)
    if ph1 is None:
        ph1 = phase1(units, a.objdir, names, a.limit)
        if a.cache:
            with open(a.cache, 'wb') as f:
                pickle.dump(ph1, f, protocol=pickle.HIGHEST_PROTOCOL)
    print('objects %d: %d items placed, %d unplaced, %d code refs, %d edges (%.0f s)' % (
        ph1['stats']['objects'], len(ph1['items']), len(ph1['unplaced']), len(ph1['refs']), len(ph1['edges']),
        time.time() - t0))
    report = {'stats': dict(ph1['stats'])}
    SG_SHIFT_OK.update(ph1.get('sg_shift', {}))
    imports = read_imports(a.imports)
    items = [it for it in ph1['items'] if WIN_LO <= it.addr and it.end <= WIN_HI]
    report['items_outside_window'] = len(ph1['items']) - len(items)
    have = set(it.addr for it in items)
    nitems = name_items(units, names, have)
    report['name_items'] = len(nitems)
    img, distinct, conflicts = merge(items + nitems, report)
    sec = infer_sections(distinct, imports, report)
    # TLS SECREL fields now that the template start is known
    if sec['tls']:
        for it in distinct:
            for o, p in it.secrel:
                if it.fam == 'bss':
                    continue
                k = it.addr - WIN_LO + o
                struct.pack_into('<I', img.val, k, (p - sec['tls'][0]) & 0xffffffff)
    iat = sorted(imports)
    iat_range = (iat[0], iat[-1] + 4) if iat else None
    for s in iat:
        if img.st[s - WIN_LO] == ST_NONE:
            img.st[s - WIN_LO:s - WIN_LO + 4] = bytes([ST_HARD]) * 4      # bound by the loader at load time
    order, phase, nroots = startup_order(names, ph1['edges'])
    report['startup'] = dict(initializer_roots=nroots, functions_reached=len(order),
                             reached_in_init=sum(1 for v in phase.values() if v == 'init'),
                             reached_in_winmain=sum(1 for v in phase.values() if v == 'winmain'))
    cls, size_of, ref = classify(units, names, img, sec, ph1, set(imports), order, phase, report, a.missing)
    byte_coverage(img, sec, report)
    coverage(img, distinct, sec, iat_range, cls, size_of, ref, report)
    report['boot_globals'] = explain(img, distinct, sec, iat_range, ref, BOOT_GLOBALS, cls, ph1['refs'], names)
    check_probes(units, img, report)
    check_vftables(distinct, names, report)
    if a.checks or a.violations or a.hints_out:
        import synth_checks
        codemap = synth_checks.load_addrmap(a.addrmap) if a.addrmap else {f: 'F' for f in names.funcs}
        checks, results = synth_checks.run_all(img, distinct, ph1['items'] + ph1['unplaced'], sec, names,
                                               conflicts, codemap)
        checks['addrmap'] = a.addrmap or 'units.json functions'
        report['checks'] = {k: checks[k] for k in ('violations_by_kind', 'violations_by_owner')}
        if a.checks:
            with open(a.checks, 'w') as f:
                json.dump(checks, f, indent=1)
        if a.violations:
            synth_checks.write_violations(a.violations, results)
        if a.hints_out:
            report['hints'] = synth_checks.write_hints(a.hints_out, results)
    report['conflicts'] = [dict(addr='0x%08x' % c[0].addr, a=c[0].name, a_unit=c[0].unit, a_src=c[0].src,
                                b_addr='0x%08x' % c[1].addr, b=c[1].name, b_unit=c[1].unit, b_src=c[1].src,
                                agree=c[2], differ=c[3]) for c in conflicts[:200]]
    if a.conflicts:
        with open(a.conflicts, 'w') as f:
            f.write('# addr_a\tname_a\tunit_a\tsrc_a\taddr_b\tname_b\tunit_b\tsrc_b\tagree\tdiffer\n')
            for x, y, ag, df in conflicts:
                f.write('0x%08x\t%s\t%d\t%s\t0x%08x\t%s\t%d\t%s\t%d\t%d\n' % (x.addr, x.name, x.unit, x.src, y.addr,
                                                                            y.name, y.unit, y.src, ag, df))
    if a.state_out:
        write_state(a.state_out, img, sec, iat_range)
    if a.out:
        report['pe'] = write_pe(a.out, img, sec, imports, iat_range, None)
    if a.report:
        with open(a.report, 'w') as f:
            json.dump(report, f, indent=1)
    if a.summary:
        with open(a.summary, 'w') as f:
            f.write(format_summary(report))
    print(format_summary(report))
    print('done in %.0f s' % (time.time() - t0))
    return 0


def format_summary(r):
    out = []
    w = out.append
    s = r['sections']
    w('sections: rdata %s..%s  data %s..%s (initialised up to %s, bss from %s)  tls %s' % (
        s['rdata'][0], s['rdata'][1], s['data'][0], s['data'][1], s['data_raw_last_evidence'],
        s['bss_first_evidence'], s['tls']))
    w('  misplaced evidence: rdata/data %d, data/bss %d' % (s['rdata_data_misplaced_evidence'],
                                                           s['data_bss_misplaced_evidence']))
    w('items: %d placed (%d distinct, %d agreeing copies), %d from names; overlaps %s' % (
        r['items'], r['items_distinct'], r['copies_agreeing'], r['name_items'], r['overlaps']))
    w('bytes:')
    for k, v in r['bytes'].items():
        w('  %-6s %s..%s %9d: hard %d, unresolved-reloc %d, soft %d, none %d' % (
            k, v['range'][0], v['range'][1], v['bytes'], v['hard'], v['unknown_reloc'], v['soft_zero'], v['none']))
    w('referenced data addresses by section/class:')
    for k, v in r['classes_by_section'].items():
        w('  %-9s %6d  %s' % (k, sum(v.values()), ', '.join('%s %d' % kv for kv in sorted(v.items(), key=lambda x: -x[1]))))
    w('by subsystem:')
    for k, v in r['classes_by_subsystem'].items():
        w('  %-18s %6d  %s' % (k, sum(v.values()), ', '.join('%s %d' % kv for kv in sorted(v.items(), key=lambda x: -x[1]))))
    m = r['missing']
    w('missing: %d (+%d partial); first used in the init tree %d, in WinMain\'s %d, not reached statically %d; '
      '%d consumer functions' % (m['missing'], m['partial'], m['missing_first_used_in_init_tree'],
                                 m['missing_first_used_in_winmain_tree'], m['missing_not_reached_statically'],
                                 m['consumer_functions']))
    w('startup: %s' % r['startup'])
    w('probes: %s' % {k: v for k, v in r['probes'].items() if k != 'mismatches'})
    w('vftable slots: %s' % r['vftable_slots'])
    if 'coverage_bytes' in r:
        w('coverage, bytes (known = one-object + copies-agree + merged-agree + merged-disagree; rank 0 content '
          'names, 1 unit names, 2 other units, 3 per-compile names):')
        for group in ('regions', 'kinds'):
            for k, v in r['coverage_bytes'][group].items():
                w('  %-12s %9d: known %d (one %d, copies %d, agree %d, disagree %d; rank %s; weak-name %d, '
                  'inexact %d), unknown %d, guessed %d (soft %d, name-only %d), loader %d, %s %d' % (
                      k, v['bytes'], v['known'], v['one-object'], v['copies-agree'], v['merged-agree'],
                      v['merged-disagree'], '/'.join(str(v['by_rank'][x]) for x in '0123'), v['weak_name'],
                      v['inexact_extent'], v['unknown'], v['guessed'], v['soft_zero'], v['name_only'],
                      v['loader_bound'], 'zero' if 'zero_by_section' in v else 'none',
                      v.get('zero_by_section', v.get('none', 0))))
        w('coverage, referenced addresses:')
        for group in ('regions', 'kinds'):
            for k, v in r['coverage_addresses'][group].items():
                w('  %-12s %6d  %s' % (k, v['referenced'], ', '.join(
                    '%s %d' % kv for kv in sorted(v.items(), key=lambda x: (-x[1], x[0])) if kv[0] != 'referenced')))
    for g in r.get('boot_globals', []):
        writers = [u for u in g['users'] if u.split(' ')[1] in ('store', 'rmw', 'this')]
        w('boot global %-32s %s %-8s %-8s state %s value %s by %s; %d users, writers %s' % (
            g['label'], g['addr'], g['region'], g['cls'], g['state'][:16], g['value'][:16] or '-',
            ', '.join('%s u%d' % (p['name'][:40], p['unit']) for p in g['providers']) or '-', len(g['users']),
            '; '.join(writers[:4]) or '-'))
    if 'checks' in r:
        w('check violations: %s' % {k: v['count'] for k, v in r['checks']['violations_by_kind'].items()})
    if 'pe' in r:
        w('pe: %s' % r['pe'])
    return '\n'.join(out) + '\n'


if __name__ == '__main__':
    sys.modules.setdefault('synth_image', sys.modules[__name__])
    Item.__module__ = 'synth_image'
    sys.exit(main())
