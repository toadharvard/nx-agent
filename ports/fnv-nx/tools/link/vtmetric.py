"""vtmetric.py - S2c-vt's metric on a finished link: how far the native vtables are from the player's exe vftables.

    python3 vtmetric.py <link dir> <FalloutNV.exe> [--rows <tsv>] [--json]

<link dir> is a published link (link/<c12> of the artifacts repository: out/addrmap.tsv, out/refs.tsv, fixed/*.o)
or a build directory's link stage with the same layout. Every number is about the image vftables the address map
pairs with a native vtable (kind `V`):

* `trap-slots` - (image vftable, slot) pairs whose native slot calls a trap stub (`__fnvnx_vtrap_*`, a virtual
  function defined nowhere), split by what the exe holds at that slot: `trap-native-at-exe` (the address map has a
  native definition at the exe slot's address: the slot fill, `fnvlink resolve --exe`, binds it) and
  `trap-no-native` (the exe slot is past the native definitions, outside the exe vftable, or not compiled yet);
  `trap-symbols` - the distinct trap stubs behind them, `trap-symbols-unpaired` - trap stubs no paired vtable calls.
* `primary-shorter` - primary vftables whose native vtable has fewer slots than the exe's (GCC's two destructor
  slots count as MSVC's one);
* `secondary-full-entry` (S2c-vt2) - secondary vftable slots bound to `__fnv_<a>` where the exe slot's function is
  one MSVC enters with `this` at that secondary base (the map's entry is `__fnvnx_msvc_<a>`): `this` arrives d bytes
  off; not part of `metric`;
* `slot-one-delete-this` (W23) - primary tables with no destructor pair whose exe slot 0 is a deleting destructor and
  whose native slot 1 still holds exe slot 1 = `DeleteThis() { delete this; }` (exevft.delete_this): the exe's slot
  order where Itanium's D0 belongs, a loop on any native `delete`. fnvlink's d0_slot_fix drives it to 0 (not part
  of `metric`);
* `mispaired-primary` / `mispaired-secondary` - vftables whose native vtable is another class than the exe's RTTI
  class, where the native class is itself an RTTI class of the exe (by name, after demangling both sides; shells
  such as `FUN_<addr>` or the decomp's local reconstructions, which name no RTTI class, are not counted).

Slot k of a native vtable is slot j of the exe vftable with GCC's destructor pair folded: j = k up to the complete
destructor D1, j = k - 1 after the deleting destructor D0 (the "minus-one rule"). The destructor pair is found by
name (D1/D0 or their address-bound forms `__fnv_<a>_D1`, `__fnvnx_vslot_*_d1|_d0`) or, in an abstract class, as the
two adjacent zero words GCC stores.

The exe vftables are those scan() finds through RTTI plus, at the V rows' other addresses, the vftables without
RTTI (plain_vftables, bounded by the next known vftable); VTMETRIC_RTTI_ONLY=1 counts the RTTI ones only (the
first definition of the metric, before fc8b9f9's successor).

Reads only; the exe is the player's own (agent-only, D3) and nothing derived from it is written except --rows.
"""
import collections
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exevft    # noqa: E402
import vtables   # noqa: E402

NATIVE_KINDS = ('F', 'C', 'X', 'G')
TRAP = '__fnvnx_vtrap_'
MSVC_ENTRY = set()   # addresses whose map entry is __fnvnx_msvc_<a> (read_addrmap)
def obj_name(source):
    return source.replace('/', '_') + '.o'


def read_addrmap(path):
    native, vrows = {}, {}
    for line in open(path, encoding='utf-8', errors='surrogateescape'):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) < 6:
            continue
        a = int(f[0], 16)
        if f[1] in NATIVE_KINDS and f[2]:
            native[a] = f[1]
            if f[2] == '__fnvnx_msvc_%08x' % a:   # MSVC enters it with this at a secondary base (thisadj.py)
                MSVC_ENTRY.add(a)
        elif f[1] == 'V':
            vrows[a] = (f[2], f[4], f[5], int(f[6]) if len(f) > 6 and f[6] else 8)
    return native, vrows


def read_renames(path):
    """{object file: {renamed target: original undefined name}} from refs.tsv (zz_vt objects resolve with their host
    unit's names, so they share the host's rows)."""
    out = collections.defaultdict(dict)
    for line in open(path, encoding='utf-8', errors='surrogateescape'):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) >= 6 and f[5]:
            if f[0].endswith('#vtables'):   # the synthesized vtable object of a host unit: zz_vt_<host>.o
                out['zz_vt_' + obj_name(f[0][:-len('#vtables')])][f[5]] = f[1]
                continue
            out[obj_name(f[0])][f[5]] = f[1]
            out['zz_vt_' + obj_name(f[0])].setdefault(f[5], f[1])
    return out


def demangle(names, tool):
    names = sorted(set(names))
    if not names:
        return {}
    r = subprocess.run([tool], input='\n'.join(names) + '\n', capture_output=True, text=True, check=True)
    lines = r.stdout.split('\n')
    if 'undname' in os.path.basename(tool):   # llvm-undname echoes each input, then its result and a blank line
        out, i = {}, 0
        while i + 1 < len(lines):
            if lines[i] in names:
                out[lines[i]] = lines[i + 1]
                i += 2
            else:
                i += 1
        return out
    return dict(zip(names, lines))


def norm_class(text):
    t = re.sub(r'\b(class|struct|union|enum) ', '', text)
    t = re.sub(r'\s+', '', t)
    return t.replace('`anonymousnamespace\'', '(anonymousnamespace)')


def measure(link, exe, rows_path=None):
    out = os.path.join(link, 'out')
    native, vrows = read_addrmap(os.path.join(out, 'addrmap.tsv'))
    renames = read_renames(os.path.join(out, 'refs.tsv'))
    symaddr = read_symaddr(os.path.join(out, 'addrmap.tsv'))
    mism_pairs = collections.Counter()
    classes = read_class_rows(os.path.join(out, 'vtables', 'classes.tsv'))
    exev = exevft.scan(exe)
    if os.environ.get('VTMETRIC_RTTI_ONLY') != '1':   # the V rows' vftables without RTTI too (exevft.plain_vftables)
        exev.update(exevft.plain_vftables(exe, [v for v in vrows if v not in exev], set(exev) | set(vrows)))
    objs = {}
    st = collections.Counter()
    rows = []
    trap_syms, trap_paired = set(), set()
    znames = set()
    per_v = {}
    for v, (z, ofile, note, ap) in sorted(vrows.items()):
        path = os.path.join(link, 'fixed', ofile)
        if ofile not in objs:
            try:
                objs[ofile] = vtables.Obj(path)
            except (OSError, ValueError):
                objs[ofile] = None
        o = objs[ofile]
        if o is None or z not in o.defs:
            st['v-rows-unreadable'] += 1
            continue
        ents = o.ents(z)
        n = vtables.slots_at(ents, ap)
        slot_ents = ents[ap // 4: ap // 4 + n]
        ren = renames.get(ofile, {})
        names = [ren.get(e.sym, e.sym) if e.sym else None for e in slot_ents]
        d = exevft.dtor_pos(names, [e.word for e in slot_ents])
        nmsvc = n - 1 if d is not None else n
        e = exev.get(v)
        per_v[v] = (z, note, n, nmsvc, e)
        # W23: `delete p` calls Itanium slot 1. A primary table in the exe's slot order (no destructor pair) that
        # holds exe slot 1 = `DeleteThis() { delete this; }` there loops on a native delete (S5 round 6)
        if e is not None and e.td and ap == 8 and note.startswith('primary') and len(e.slots) >= 2 and d is None \
                and (native.get(e.slots[0]) == 'G' or exevft.deleting_dtor(exe, e.slots[0])) \
                and exevft.delete_this(exe, e.slots[1]) and not (n >= 2 and exevft.slot_one_is_d0(
                    names[0], names[1])):
            st['slot-one-delete-this'] += 1
            rows.append(('%08x' % v, z, '1', '1', '%08x' % e.slots[1], '', names[1] if n >= 2 else '',
                         'slot-one-delete-this'))
        znames.add(z)
        kind = 'primary' if note.startswith('primary') else 'secondary'
        st['v-%s' % kind] += 1
        if e is None:
            st['v-%s-not-in-exe' % kind] += 1
        elif nmsvc < len(e.slots) and e.td:   # a vftable without RTTI has no reliable end (exevft.plain_vftables)
            st['%s-shorter' % kind] += 1
        if e is not None:
            st['slot-vftables'] += 1
            cur = [x.sym for x in slot_ents]   # the symbols the fixed object names now (names: the units' own)
            agree, bad = compare_slots(cur, d, e.slots, symaddr)
            st['slot-compared'] += agree + len(bad)
            if bad:
                st['slot-vftables-mismatch'] += 1
                st['slot-mismatch'] += len(bad)
                for k_, j_, na_, ea_ in bad:
                    mism_pairs[(na_, ea_)] += 1
                    rows.append(('%08x' % v, z, str(k_), str(j_), '%08x' % ea_, '%08x' % na_, cur[k_] or '',
                                 'slot-mismatch'))
        for k, ent in enumerate(slot_ents):
            if kind == 'secondary' and e is not None and (ent.sym or '').startswith('__fnv_'):
                # S2c-vt2: a secondary slot bound to the full-object entry of a function MSVC enters at this base
                j = exevft.exe_slot_index(k, d)
                a = e.slots[j] if j < len(e.slots) else None
                if a in MSVC_ENTRY and ent.sym == '__fnv_%08x' % a:
                    st['secondary-full-entry'] += 1
                    rows.append(('%08x' % v, z, str(k), str(j), '%08x' % a, native.get(a, ''), ent.sym,
                                 'secondary-full-entry'))
            if not (ent.sym or '').startswith(TRAP):
                continue
            trap_paired.add(ent.sym)
            st['trap-slots'] += 1
            j = exevft.exe_slot_index(k, d)
            a = e.slots[j] if e is not None and j < len(e.slots) else None
            got = 'trap-native-at-exe' if a is not None and a in native else 'trap-no-native'
            st[got] += 1
            if got == 'trap-no-native':
                st['trap-no-native-' + ('no-exe-vftable' if e is None else 'past-exe-slots' if a is None
                                        else 'not-compiled')] += 1
            rows.append(('%08x' % v, z, str(k), str(j), '' if a is None else '%08x' % a, native.get(a, '') if a else '',
                         names[k] or '', got))
    for o in objs.values():
        if o is None:
            continue
        for s in o.syms:
            if s[0].startswith(TRAP) and s[5] == 0:
                trap_syms.add(s[0])
    st['slot-mismatch-pairs'] = len(mism_pairs)
    for v, e_ in exev.items():
        if v not in vrows and e_.td:
            st['unpaired-%s-%s' % ('secondary' if e_.off else 'primary', unpaired_reason(e_.td, e_.off, classes))] += 1
    st['trap-symbols'] = len(trap_paired)
    st['trap-symbols-unpaired'] = len(trap_syms - trap_paired)
    # mispairings: the native vtable's class against the exe's RTTI class
    it = demangle(znames, os.environ.get('CXXFILT', 'c++filt'))
    msn = {v: exevft.msvc_name(e.td) for v, (_, _, _, _, e) in per_v.items() if e is not None and e.td}
    ms = demangle(msn.values(), os.environ.get('UNDNAME', 'llvm-undname-18' if not shutil_which('llvm-undname')
                                                else 'llvm-undname'))
    exe_classes = set()
    for a in msn.values():
        m = re.match(r"^const (.*)::`vftable'$", ms.get(a, ''))
        if m:
            exe_classes.add(norm_class(m.group(1)))
    for v, (z, note, n, nmsvc, e) in per_v.items():
        if e is None or v not in msn:
            continue
        nat = it.get(z, '')
        if not nat.startswith('vtable for '):
            continue
        nat = norm_class(nat[len('vtable for '):])
        m = re.match(r"^const (.*)::`vftable'$", ms.get(msn[v], ''))
        if not m:
            continue
        exc = norm_class(m.group(1))
        if nat == exc or nat not in exe_classes:
            continue
        kind = 'primary' if note.startswith('primary') else 'secondary'
        st['mispaired-%s' % kind] += 1
        rows.append(('%08x' % v, z, '', '', '', '', 'exe class ' + exc, 'mispaired-' + kind))
    st['metric'] = st['trap-slots'] + st['primary-shorter'] + st['mispaired-primary']
    if rows_path:
        with open(rows_path, 'w', encoding='utf-8', errors='surrogateescape') as f:
            f.write('#vftable\tnative vtable\tnative slot\texe slot\texe address\tnative kind\tslot symbol\toutcome\n')
            for r in rows:
                f.write('\t'.join(r) + '\n')
    return st


# ---- S2d-vslot: where the native slot's function is not the exe's (EQ difffuzz native-vslot-differs)
#
# `slot-mismatch` compares, for every image vftable paired with a native vtable, the address behind each native slot
# (through the address map) with the exe vftable's word at the slot GCC's minus-one layout puts it in: slot k + 1 of
# the native vtable against exe slot k after the destructor pair. Exe slots 0 and 1 are not compared (the destructor
# and the first virtual, so a shifted pair is not mistaken for a mismatch). A slot whose symbol or exe word the map
# does not place is skipped, so a trap stub never counts (trap-slots has those). Where the destructor pair is not
# found by name (`__fnvnx_vflag_<a>_0|1` flag stubs bind a pair of exe slots as often as a destructor), the
# alignment with the most agreeing slots decides: the exe is the evidence, not the spelling.
FLAG_PAIR = re.compile(r'__fnvnx_vflag[3c]?_[0-9a-f]{8}_([01])$')
ADDR_NAME = re.compile(r'__fnv(?:nx_msvc)?_([0-9a-f]{8})(?:_(?:D[012]|d[01]))?$')
ADDR_STUB = re.compile(r'__fnvnx_v(?:flag[3c]?|gdel|free|const|adj)_([0-9a-f]{8})(?:_\d)?$')


def read_symaddr(path):
    """{symbol: address} for every symbol the address map's code rows name (link symbol, native symbol, and the
    D1 forms): the address behind a native vtable slot's name."""
    out = {}
    for line in open(path, encoding='utf-8', errors='surrogateescape'):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) < 6 or f[1] not in NATIVE_KINDS:
            continue
        a = int(f[0], 16)
        for c in (f[2], f[3], f[7] if len(f) > 7 else '', f[8] if len(f) > 8 else ''):
            if c:
                out.setdefault(c, a)
    return out


def slot_address(name, symaddr):
    """The exe address a native vtable slot's symbol stands for: the map's row for the name, else the address a bound
    name carries (`__fnv_<a>`, `__fnv_<a>_D1`, `__fnvnx_msvc_<a>`, `__fnvnx_vflag_<a>_<n>`, `__fnvnx_vgdel_<a>`);
    None for a trap stub, a stand-in or a name nothing places."""
    if not name:
        return None
    if name in symaddr:
        return symaddr[name]
    m = ADDR_NAME.match(name) or ADDR_STUB.match(name)
    return int(m.group(1), 16) if m else None


def _pair_positions(names, d):
    """The slots a destructor pair (D1 then D0) can start at, by what the fixed object's names say: the pair given
    (d), a flag-stub pair (_0, _1), a slot followed by a D0-named one (the link binds D1 to an address-bound name or a
    flag stub and leaves the class's own `...D0Ev`, or `__fnv_<a>_D0`, as it was), and two adjacent slots with the
    same symbol (D1 and D0 bound to the one function the exe's single deleting destructor is). compare_slots lets the
    exe decide between them."""
    out = []
    if d is not None:
        out.append(d)
    for i in range(len(names) - 1):
        a, b = names[i], names[i + 1]
        if not a or not b or i in out:
            continue
        fa, fb = FLAG_PAIR.match(a), FLAG_PAIR.match(b)
        if fa and fb and fa.group(1) == '0' and fb.group(1) == '1':
            out.append(i)
        elif not exevft.D0_SLOT.search(a) and (exevft.D0_SLOT.search(b) or b.endswith('_D0')
                                               or (fb and fb.group(1) == '1' and fa is None)):
            out.append(i)
        elif a == b:
            out.append(i)
    return out


def compare_slots(names, d, exe_slots, symaddr):
    """-> (agree, [(k, j, native address, exe address)] mismatches) for the alignment that agrees most: candidates are
    d (exevft.dtor_pos, may be None), every flag-stub pair, and no pair at all; ties keep the earliest candidate.
    Only exe slots j >= 2 are compared."""
    addr = [slot_address(n, symaddr) for n in names]
    best = None
    for c in [d] + [p for p in _pair_positions(names, d) if p != d] + ([None] if d is not None else []):
        agree, bad = 0, []
        for k, a in enumerate(addr):
            j = exevft.exe_slot_index(k, c)
            if a is None or j < 2 or j >= len(exe_slots):
                continue
            if a == exe_slots[j]:
                agree += 1
            else:
                bad.append((k, j, a, exe_slots[j]))
        if best is None or agree > best[0]:
            best = (agree, bad)
    return best


# ---- S2d-vslot: exe vftables no V row stands for
#
# `unpaired-<primary|secondary>-<reason>` counts the exe vftables with RTTI that the address map has no kind-V row
# for, by what the class's row in vtables/classes.tsv says (the synthesis step's outcome for the class):
#   no-polymorphic-view  - `typeinfo` only: every host unit declares the class without a virtual member, so GCC gives
#                          it no vptr and the synthesis (vtables.py) cannot emit a vtable (QuantityMenu, 0x010701c4,
#                          which the destructor 0x00720a40 stores in its vptr; 171 more on link 0540aff0d154);
#   no-secondary-base    - the class has a vtable, but the decomp declares it without the base subobject this vftable
#                          belongs to (offset > 0: TESObjectARMA's 12 component vftables, 0x0102a28c among them), so no
#                          native address point has offset-to-top minus the COL offset;
#   not-nameable         - no host unit could name the class (the row says so);
#   template-or-no-row   - a class template instance or a name with no row (the simple-name match cannot place it).
UNPAIRED_NAME = re.compile(r'^\.\?A[VU]([^@?$]+)@@$')


def read_class_rows(path):
    """{class name: outcome} from vtables/classes.tsv (the synthesis step's per-class result column)."""
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding='utf-8', errors='surrogateescape'):
        f = line.rstrip('\n').split('\t')
        if len(f) >= 4 and not line.startswith('#'):
            out[f[0]] = f[3]
    return out


def unpaired_reason(td, off, classes):
    """The reason an exe vftable of RTTI class `td` (COL offset `off`) has no V row, from the class rows."""
    m = UNPAIRED_NAME.match(td or '')
    if not m or m.group(1) not in classes:
        return 'template-or-no-row'
    r = classes[m.group(1)]
    if r.startswith('vtable'):
        return 'no-secondary-base' if off else 'paired-elsewhere'
    if r.startswith('typeinfo'):
        return 'no-polymorphic-view'
    if 'no host unit' in r or 'nameable' in r:
        return 'not-nameable'
    return 'other'


def shutil_which(x):
    import shutil
    return shutil.which(x)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    rows = argv[argv.index('--rows') + 1] if '--rows' in argv else None
    st = measure(argv[0], argv[1], rows)
    if '--json' in argv:
        print(json.dumps(dict(st), sort_keys=True))
    else:
        for k in sorted(st):
            print('%-40s %d' % (k, st[k]))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
