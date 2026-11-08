#!/usr/bin/env python3
"""classlayout.py - one exe-derived decision per RTTI vftable, and the static gate over every native table
(orchestrator 2026-11-08 12:10: one source of truth from the exe, read by prepare and by the link).

    python3 classlayout.py <FalloutNV.exe> -o <out>/classlayout.tsv [--link <link root>] [--gate <out>/classgate.tsv]

1. classlayout.tsv (format fnvnx-classlayout/1, the columns S1-rules proposed on 2026-11-08 12:10, plus two):
     class  vftable  kind  dtor_slot  reason  file  qualified  evidence  slots
   one row per RTTI vftable of the exe (exevft.scan: the COL's TypeDescriptor, its offset and hierarchy).
     kind  com        IUnknown (or a COM_CLASS-named class) anywhere in the hierarchy descriptor: exe order, always;
           minus-one  the deleting destructor (exevft.deleting_dtor) is exe slot dtor_slot = k, the first such slot:
                      MSVC places it where the class declares its virtual destructor, as GCC places D1 / D0, so
                      native slots 0..k-1 = exe 0..k-1, D1 / D0 at k / k+1, exe slot i > k at native i + 1 (D26;
                      k = 4 for the TESForm family; evidence says whether the slots before k are trivial);
           exe-order  no deleting destructor where D26 can place one (dtor_slot -1): native slot i = exe slot i.
   A secondary vftable (COL offset > 0) follows its adjustor thunks (`sub ecx, n; jmp X` / `add ecx, -n; jmp X`)
   to the destructor. The class-wide decision is the primary's; secondaries carry their own row (prepare only needs
   the rows of a class to agree, the link reads each vftable's own row).

2. The gate (--link): for every RTTI vftable that the link's addrmap pairs with a native table (kind V), the native
   table must be transform(exe table) slot by slot, transform being the row's kind: each native slot must name the
   native function compiled from the exe function in the corresponding exe slot (`__fnv_<a>`, a flags stub
   `__fnvnx_vflag_<a>_<f>` into a, or a name the address map / refs.tsv binds to a). classgate.tsv lists every
   disagreeing slot; the summary line is the number to drive to 0, like the trap count:
     tables  gated  ok  mismatch-tables  mismatch-slots  (+ per-cause counts)
   Causes: `wrong` (the slot names another exe function), `shift` (it names the exe function of a neighbouring
   slot: the layout's order is wrong), `trap` (a trap stub for a slot the exe has), `pure`, `null`, `unmapped` (a
   name nothing binds to an exe address: reported, not counted as a mismatch), `short` (the native table ends
   before the exe's).

Exe-derived data: the output stays in the link's out/ or the -o directory (fnv-nx-build recon/classlayout for
prepare), never in this repository. Names and addresses only.
"""
import argparse
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exevft  # noqa: E402

FORMAT = 'fnvnx-classlayout/1'
COLUMNS = ['class', 'vftable', 'kind', 'dtor_slot', 'reason', 'file', 'qualified', 'evidence', 'slots']
MAX_DTOR_SLOT = 8
COM_TD = re.compile(r'^\.\?A[VU](?:IUnknown|ID3D\w*|IDirect\w*|IDXGI\w*|IXAudio\w*|IDispatch|IClassFactory)@@$')
RET = (0xc3, 0xc2)


# ------------------------------------------------------------------------------------------------- the exe side

def _code(exe, addr, n=0x40):
    pe, data = exevft._text(exe)
    try:
        off = pe.rva_to_off(addr - pe.image_base, 16)
    except Exception:
        return b''
    return b'' if off is None else data[off:off + n]


def trivial(exe, addr):
    """A placeholder-like slot: a `ret` / `ret n` within 16 bytes and no call before it (nullsub, return-constant)."""
    b = _code(exe, addr, 16)
    for i, x in enumerate(b):
        if x == 0xe8:   # call rel32
            return False
        if x in RET:
            return True
    return False


def adjustor_target(exe, addr):
    """X of an adjustor thunk `sub ecx, imm8 / imm32; jmp X` (or `add ecx, -n; jmp X`), else None."""
    b = _code(exe, addr, 12)
    i = None
    if b[:2] in (b'\x83\xe9', b'\x83\xc1'):
        i = 3
    elif b[:2] in (b'\x81\xe9', b'\x81\xc1'):
        i = 6
    if i is None or len(b) < i + 5:
        return None
    if b[i] == 0xe9:
        rel = int.from_bytes(b[i + 1:i + 5], 'little', signed=True)
        return addr + i + 5 + rel
    if b[i] == 0xeb:
        rel = int.from_bytes(b[i + 1:i + 2], 'little', signed=True)
        return addr + i + 2 + rel
    return None


def is_deldtor(exe, addr):
    """(True, evidence) when addr is MSVC's deleting destructor, directly or through an adjustor thunk."""
    if exevft.deleting_dtor(exe, addr):
        return True, 'exe-deldtor'
    t = adjustor_target(exe, addr)
    if t and exevft.deleting_dtor(exe, t):
        return True, 'adjustor->%08x' % t
    return False, ''


def simple_name(td):
    """'.?AVFoo@@' -> 'Foo'; '.?AVInner@Outer@@' -> 'Inner' (qualified 'Outer::Inner'); '.?AV?$T@...' -> 'T'."""
    s = td[4:] if td.startswith(('.?AV', '.?AU')) else td
    if s.startswith('?$'):
        nm = s[2:].split('@', 1)[0]
        return nm, nm
    parts = [p for p in s.rstrip('@').split('@') if p]
    if not parts:
        return s, s
    return parts[0], '::'.join(reversed(parts))


def decide(exe, v):
    """(kind, dtor_slot, reason, evidence) of one exe vftable."""
    if any(COM_TD.match(td or '') for td, _ in (v.bases or [])):
        return 'com', -1, 'rtti-com', ''
    sl = v.slots
    for k in range(len(sl)):
        ok, ev = is_deldtor(exe, sl[k])
        if ok:
            # MSVC puts the deleting destructor where the class declares its virtual destructor, GCC its D1 / D0
            # pair at the same place: minus-one from k, whatever the slots before k are (TESForm: 4, after two
            # nullsubs and two real functions)
            pre = '' if k == 0 else (' after-trivial' if all(trivial(exe, a) for a in sl[:k]) else ' after-real')
            return 'minus-one', k, 'exe-deldtor-slot%d' % k, '%s@%d%s' % (ev, k, pre)
    return 'exe-order', -1, 'no-deleting-dtor', ''


def build(exe, exev=None):
    """[row dict] for every RTTI vftable, primaries first per class."""
    exev = exev if exev is not None else exevft.scan(exe)
    rows = []
    for va in sorted(exev):
        v = exev[va]
        kind, k, why, ev = decide(exe, v)
        if v.off:
            why = 'secondary off %d %s' % (v.off, why)   # S1's loader skips these: the class decision is the primary's
        cls, q = simple_name(v.td)
        rows.append(dict(cls=cls, vftable=va, kind=kind, dtor_slot=k, reason=why, file='',
                         qualified=q if q != cls else '', evidence=ev, slots=len(v.slots), off=v.off))
    return rows


def write_layout(rows, path):
    with open(path, 'w') as f:
        f.write('# %s\n# %s\n' % (FORMAT, '\t'.join(COLUMNS)))
        for r in rows:
            f.write('\t'.join([r['cls'], '%08x' % r['vftable'], r['kind'], str(r['dtor_slot']), r['reason'],
                               r['file'], r['qualified'], r['evidence'], str(r['slots'])]) + '\n')


def load_layout(path):
    """{vftable: row dict} from a classlayout.tsv."""
    out = {}
    for line in open(path):
        if line.startswith('#') or not line.strip():
            continue
        p = line.rstrip('\n').split('\t')
        out[int(p[1], 16)] = dict(cls=p[0], vftable=int(p[1], 16), kind=p[2], dtor_slot=int(p[3]), reason=p[4],
                                  file=p[5] if len(p) > 5 else '')
    return out


def expected(row, exe_slots):
    """Native slot list the decision implies: [exe address, or ('dtor', a) for D1 / D0]."""
    if row['kind'] != 'minus-one':
        return list(exe_slots)
    k = row['dtor_slot']
    if k < 0 or k >= len(exe_slots):
        return list(exe_slots)
    a = exe_slots[k]
    return list(exe_slots[:k]) + [('dtor', a), ('dtor', a)] + list(exe_slots[k + 1:])


# ------------------------------------------------------------------------------------------------ the native side

ADDR_RX = re.compile(r'^__fnv_([0-9a-f]{8})(?:_D[0-2])?$')
FLAG_RX = re.compile(r'^__fnvnx_vflag[0-9a-z]*_([0-9a-f]{8})_\d+$')
OTHER_ADDR_RX = re.compile(r'^__fnvnx_(?:vgdel|vfree|d0del|cthis|vmo|vadjd?|msvc)_([0-9a-f]{8})')   # vadj / vadjd: the exe slot's adjustor; msvc: its secondary-base entry


def native_names(link):
    """{native symbol: exe address} from the link's addrmap (both name columns) and refs.tsv code rows."""
    m = {}
    am = os.path.join(link, 'out', 'addrmap.tsv')
    for line in open(am):
        p = line.rstrip('\n').split('\t')
        if len(p) < 4 or p[1] not in ('F', 'C', 'X', 'G'):
            continue
        try:
            a = int(p[0], 16)
        except ValueError:
            continue
        for nm in (p[2], p[3]):
            if nm:
                m.setdefault(nm, a)
    rf = os.path.join(link, 'out', 'refs.tsv')
    if os.path.isfile(rf):
        for line in open(rf):
            p = line.rstrip('\n').split('\t')
            if len(p) > 4 and p[2] in ('code', 'vslot-exe') and re.fullmatch(r'[0-9a-f]{8}', p[4]):
                m.setdefault(p[1], int(p[4], 16))
    return m


def sym_addr(sym, names):
    """(exe address or None, category) of one native slot symbol."""
    if not sym:
        return None, 'null'
    if sym.startswith('.reloc'):
        sym = sym.split(':', 1)[1]
    if sym == '__cxa_pure_virtual':
        return None, 'pure'
    if sym.startswith('__fnvnx_vtrap_') or sym.startswith('__fnvnx_trap'):
        return None, 'trap'
    for rx in (ADDR_RX, FLAG_RX, OTHER_ADDR_RX):
        mm = rx.match(sym)
        if mm:
            return int(mm.group(1), 16), 'addr'
    if sym in names:
        return names[sym], 'addr'
    return None, 'unmapped'


def native_tables(link, exev=None, names=None):
    """{exe vftable: [(object path, group symbol, address point byte offset, source)]}: every native table that stands
    for an exe vftable, not only the address map's choice. Sources: the addrmap's V rows; out/vtext.tsv (each copy
    the link wrote, with its exe vftables); else, for the strong copies in zz_bridges.o that no V row names, the exe
    vftable that shares most slot targets (at least half of the first 32 named slots, measurement only). Test case
    #1 (S5, link 8111f58a): NiDX9Renderer 010ee4bc's V row names the sister copy fnv_00e75a70, while the unit's
    objects get _ZTV12FUN_00e754a0, which lost exe slot 1."""
    import vtables
    out = collections.defaultdict(list)
    seen = set()
    for line in open(os.path.join(link, 'out', 'addrmap.tsv')):
        p = line.rstrip('\n').split('\t')
        if len(p) < 7 or p[1] != 'V':
            continue
        try:
            t = (os.path.join(link, 'fixed', p[4]), p[2], int(p[6] or 8))
            out[int(p[0], 16)].append(t + ('V',))
            seen.add((t[0], t[1]))
        except ValueError:
            continue
    br = os.path.join(link, 'fixed', 'zz_bridges.o')
    vt = os.path.join(link, 'out', 'vtext.tsv')
    if os.path.isfile(vt):
        for line in open(vt):
            if line.startswith('#'):
                continue
            p = line.rstrip('\n').split('\t')
            if len(p) < 5:
                continue
            for v in p[4].split():
                if (br, p[0]) not in seen:
                    out[int(v, 16)].append((br, p[0], 8, 'vtext'))
            seen.add((br, p[0]))
        return out
    if exev is None or names is None or not os.path.isfile(br):
        return out
    by_addr = collections.defaultdict(set)
    for va, v in exev.items():
        for a in v.slots[:32]:
            by_addr[a].add(va)
    o = vtables.Obj(br)
    for z in o.defs:
        if not z.startswith('_ZTV') or (br, z) in seen:
            continue
        try:
            ents = o.ents(z)
            n = vtables.slots_at(ents, 8)
        except (KeyError, ValueError, IndexError):
            continue
        addrs = [sym_addr(e.sym, names)[0] for e in ents[2:2 + n][:32]]
        addrs = [a for a in addrs if a is not None]
        if len(addrs) < 2:
            continue
        votes = collections.Counter(v for a in set(addrs) for v in by_addr.get(a, ()))
        if votes:
            v, c = votes.most_common(1)[0]
            if c * 2 >= len(set(addrs)):
                out[v].append((br, z, 8, 'bridges-match'))
    return out


def _asciiish(a):
    b = a.to_bytes(4, 'little')
    return sum(32 <= x < 127 for x in b) >= 2 and all(x == 0 or 32 <= x < 127 for x in b)


_RELOCS = {}


def exe_relocs(exe):
    """The exe's base-relocation targets (VAs whose word the loader relocates): a vftable slot always has one."""
    if exe not in _RELOCS:
        try:
            import fnvnx_pe
            pe = exevft._text(exe)[0]
            img = fnvnx_pe.Image(pe)
            img.load_relocs()
            _RELOCS[exe] = set(img.relocs)
        except Exception:
            _RELOCS[exe] = None
    return _RELOCS[exe]


def real_slots(exe, va, slots):
    """The exe slots of the vftable at va up to the first word without a base relocation (R4: 010cb798's word 4 =
    0x00500000 is data past the table's end, not ASCII); all slots when the relocations cannot be read."""
    rel = exe_relocs(exe) if exe else None
    if not rel:
        return slots
    for i in range(len(slots)):
        if va + 4 * i not in rel:
            return slots[:i]
    return slots


def exe_len(slots, known):
    """The exe slots up to the table's real end: the scan runs to the next COL pointer, so a table followed by
    strings reads them as .text words (R2 / R4: 00700074, 00504942 'tp', 'IBP'). Stop at the first word that is no
    function the address map knows and looks like ASCII."""
    for i, a in enumerate(slots):
        if a not in known and _asciiish(a):
            return slots[:i]
    return slots


DTOR_RX = re.compile(r'D[012]Ev$|_D[012]$')
_alias = {}


def dtor_alias(exe, a):
    """Addresses a native D1 / D0 may be bound to for the deleting destructor at a: a itself, its destroy body (the
    ??_G's `call X`), and an adjustor thunk's target."""
    if a not in _alias:
        s = {a}
        t = adjustor_target(exe, a)
        if t:
            s.add(t)
        for x in list(s):
            c = exevft.deleting_dtor_callee(exe, x)
            if c:
                s.add(c)
        _alias[a] = s
    return _alias[a]


def gate(link, exe, layout, exev):
    """-> (summary Counter, [bad slot rows])."""
    import vtables
    names = native_names(link)
    known = set(names.values())
    tabs = native_tables(link, exev, names)
    st = collections.Counter()
    bad = []
    objs = {}
    purecall = {a for a in (0x00ec60fa,)}
    st['tables'] = len(layout)
    pairs = [(va, t) for va in sorted(layout) for t in tabs.get(va, [])]
    st['ungated-no-native-table'] = sum(1 for va in layout if not tabs.get(va) or va not in exev)
    bad_v = set()
    for va, (path, z, ap, src) in pairs:
        row = layout[va]
        if va not in exev:
            continue
        st['native-tables-' + src] += 1
        try:
            o = objs.get(path)
            if o is None:
                if len(objs) > 64:
                    objs.clear()
                o = objs[path] = vtables.Obj(path)
            ents = o.ents(z)
            n = vtables.slots_at(ents, ap)
            nat = ents[ap // 4: ap // 4 + n]
        except (OSError, KeyError, ValueError, IndexError):
            st['ungated-unreadable'] += 1
            continue
        st['gated'] += 1
        sl_ = exe_len(real_slots(exe, va, exev[va].slots), known)
        if len(sl_) < len(exev[va].slots):
            st['exe-slots-past-end'] += len(exev[va].slots) - len(sl_)
        exp = expected(row, sl_)
        nb = len(bad)
        tab_bad = 0
        for j, want in enumerate(exp):
            if j >= len(nat):
                wa_ = want[1] if isinstance(want, tuple) else want
                bad.append((va, row['cls'], row['kind'], j, '', '%08x' % wa_, 'short'))   # one row per missing slot
                st['slot-short'] += 1
                tab_bad += 1
                continue
            sym = nat[j].sym
            a, cat = sym_addr(sym, names)
            wa = want[1] if isinstance(want, tuple) else want
            if cat == 'addr' and a == wa:
                continue
            if isinstance(want, tuple) and sym and (DTOR_RX.search(sym) or (cat == 'addr' and a in dtor_alias(exe, wa))):
                continue   # D1 / D0 of the class (Itanium names, or bound to the ??_G's destroy body)
            if cat == 'pure' and wa in purecall:
                continue
            if cat == 'unmapped':
                st['slot-unmapped'] += 1
                continue
            if cat == 'addr':
                near = [i for i, x in enumerate(exp) if (x[1] if isinstance(x, tuple) else x) == a]
                cat = 'shift' if any(abs(i - j) <= 2 for i in near) else 'wrong'
            bad.append((va, row['cls'], row['kind'], j, sym or '', '%08x' % wa, cat))
            st['slot-' + cat] += 1
            tab_bad += 1
        bad[nb:] = [b + (z, src) for b in bad[nb:]]
        if tab_bad:
            st['mismatch-native-tables'] += 1
            st['mismatch-slots'] += tab_bad
            st['mismatch-tables-' + row['kind']] += 1
            bad_v.add(va)
        else:
            st['ok-native-tables'] += 1
    st['mismatch-tables'] = len(bad_v)
    st['ok'] = len({va for va, _ in pairs if va in exev}) - len(bad_v)
    return st, bad


def native_of(link):
    """{exe address: native symbol} from the addrmap's F / C / X / G rows (the Itanium name where it has one)."""
    m = {}
    for line in open(os.path.join(link, 'out', 'addrmap.tsv')):
        p = line.rstrip('\n').split('\t')
        if len(p) > 3 and p[1] in ('F', 'C', 'X', 'G'):
            try:
                m.setdefault(int(p[0], 16), p[3] or p[2])
            except ValueError:
                pass
    return m


def write_report(path, bad, link, boot=None):
    """reports/vtgate/<link>.tsv (orchestrator 12:24): one row per disagreeing slot with the expected native name."""
    nat = native_of(link)
    with open(path, 'w') as f:
        f.write('# fnvnx-vtgate/1\n# table\tclass\texe_vftable\tslot\texe_function\texpected_native\tactual\tkind\tboot\tsource\n')
        for va, cls, kind_, j, sym, want, cause, z, src in bad:
            wa = int(want, 16) if want else None
            exp = ('D1/D0 of ' + (nat.get(wa) or '__fnv_%08x' % wa)) if wa is not None and kind_ == 'minus-one' and \
                False else (nat.get(wa) or ('__fnv_%08x' % wa if wa is not None else ''))
            f.write('\t'.join([z, cls, '%08x' % va, str(j), want or '', exp, sym, cause,
                                '1' if (boot and wa in boot) else '0', src]) + '\n')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('exe')
    ap.add_argument('-o', '--out', required=True, help='classlayout.tsv to write')
    ap.add_argument('--link', help='link root (out/, fixed/) for the gate')
    ap.add_argument('--gate', help='classgate.tsv to write (default: next to -o)')
    ap.add_argument('--report', help='reports/vtgate/<link>.tsv to write (per-slot list with the expected native name)')
    ap.add_argument('--boot', help='boot set: exe addresses, one `<addr> [name]` per line (S18 trace-coc called.txt)')
    a = ap.parse_args(argv)
    exev = exevft.scan(a.exe)
    rows = build(a.exe, exev)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    write_layout(rows, a.out)
    c = collections.Counter((r['kind'], 'primary' if r['off'] == 0 else 'secondary') for r in rows)
    print('classlayout: %d RTTI vftables (%d primary): %s' % (
        len(rows), sum(1 for r in rows if r['off'] == 0),
        ', '.join('%s/%s %d' % (k[0], k[1], v) for k, v in sorted(c.items()))))
    ks = collections.Counter(r['dtor_slot'] for r in rows if r['kind'] == 'minus-one')
    print('classlayout: minus-one dtor slots: %s' % ', '.join('%d: %d' % kv for kv in sorted(ks.items())))
    if a.link:
        lay = {r['vftable']: r for r in rows}
        st, bad = gate(a.link, a.exe, lay, exev)
        gp = a.gate or os.path.join(os.path.dirname(os.path.abspath(a.out)), 'classgate.tsv')
        with open(gp, 'w') as f:
            f.write('# fnvnx-classgate/1\n# vftable\tclass\tkind\tnative_slot\tnative_symbol\texe_target\tcause'
                    '\tnative_table\tsource\n')
            for r in bad:
                f.write('%08x\t%s\t%s\t%d\t%s\t%s\t%s\t%s\t%s\n' % r)
        if a.report:
            boot = set()
            if a.boot:
                for line in open(a.boot):
                    t = line.split()
                    if t and re.fullmatch(r'[0-9a-fA-F]{8}', t[0]):
                        boot.add(int(t[0], 16))
            write_report(a.report, bad, a.link, boot)
        print('classgate: tables %d, gated %d, ok %d, mismatch-tables %d, mismatch-slots %d' % (
            st['tables'], st['gated'], st['ok'], st['mismatch-tables'], st['mismatch-slots']))
        for k in sorted(st):
            print('  %s\t%d' % (k, st[k]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
