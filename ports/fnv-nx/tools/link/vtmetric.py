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
            out[obj_name(f[0])][f[5]] = f[1]
            out['zz_vt_' + obj_name(f[0])][f[5]] = f[1]
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
        znames.add(z)
        kind = 'primary' if note.startswith('primary') else 'secondary'
        st['v-%s' % kind] += 1
        if e is None:
            st['v-%s-not-in-exe' % kind] += 1
        elif nmsvc < len(e.slots) and e.td:   # a vftable without RTTI has no reliable end (exevft.plain_vftables)
            st['%s-shorter' % kind] += 1
        for k, ent in enumerate(slot_ents):
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
