"""vtables.py - native (Itanium) vtables and class typeinfo for the image's vftables (docs/LINK.md, "Vtables").

Reading, on any ELF32 relocatable object (GCC's or ours):

* `Obj(path).ents(sym)` - a vtable group `_ZTV<C>` or a typeinfo `_ZTI<C>` as 4-byte words with their relocations;
* `address_points(ents)` - the vtables of a group: byte offset of each address point and its offset-to-top;
* `parse_typeinfo(ents)` - class / single / multiple-inheritance typeinfo with its direct bases (offsets, flags);
* `base_offsets(...)` - the offsets of the base-class subobjects of a given name, which is what an MSVC secondary
  vftable `??_7C@@6B<B>@@@` (`C::`vftable'{for `B'}`) stands for: its native counterpart is the vtable of the group
  whose offset-to-top is minus that offset.

Synthesis (`run`, called by `fnvlink.py probe` / `fnvlink.py vtables`): for classes that have an image vftable or
are referenced by `_ZTV`/`_ZTI` but whose vtable/typeinfo no object emits (key function never compiled, or inline-only
virtuals in units that never construct the class), a probe translation unit includes a unit that sees the class and
makes GCC itself lay the vtable out: `struct V<k> : C { virtual void __fnvnx_key(); }` with that key function
defined, so V<k>'s vtable group is emitted. It is C's group plus the key slot (last of the primary vtable), with V<k>'s
implicit destructor (D1/D0 and their this-adjusting thunks) and typeinfo in place of C's: removing the key slot and
renaming those gives C's group exactly as GCC would emit it. The typeinfo comes from `std::tr2::direct_bases` (base
`type_info` symbols via `typeid`, offsets as folded `(B *)(C *)4096` constants, public = convertible); classes with
virtual bases are not synthesized. Output per host unit: `out/vtables/<unit>.vt.S` (weak `_ZTV`/`_ZTI`/`_ZTS` and
non-virtual thunks, only for names no compiled object defines) assembled to `zz_vt_<unit>.o`; `synth.tsv` lists
them and the next `resolve` links them as extra objects of their host unit (their slot references are bound by
address like every other reference).
"""
import collections
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess

import layout

AS = os.environ.get('AS', 'aarch64-linux-gnu-as')
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
CXXFILT = os.environ.get('CXXFILT', 'c++filt')
Ent = collections.namedtuple('Ent', 'word sym addend')
TypeInfo = collections.namedtuple('TypeInfo', 'kind flags bases')   # bases: [(ti symbol, offset, virtual, public)]
ABI = '_ZTVN10__cxxabiv1'
TI_VPTR = {'class': ABI + '17__class_type_infoE', 'si': ABI + '20__si_class_type_infoE',
           'vmi': ABI + '21__vmi_class_type_infoE'}
SPECIAL_PREFIXES = ('_ZTV', '_ZTI', '_ZTS', '_ZTh', '_ZTv', '_ZTc', '_ZTT')
R_AARCH64_P32_ABS32 = 1


def s32(w):
    return w - (1 << 32) if w & 0x80000000 else w


# ---------------------------------------------------------------------------------------------------- reading

class Obj:
    """ELF32 little-endian relocatable object: sections, index-aligned symbols, RELA relocations by target section."""

    def __init__(self, path):
        self.path = path
        data = self.data = open(path, 'rb').read()
        if data[:4] != b'\x7fELF' or data[4] != 1 or data[5] != 1:
            raise ValueError('%s: not an ELF32 little-endian object' % path)
        shoff, = struct.unpack_from('<I', data, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from('<HHH', data, 0x2e)
        self.secs = secs = [struct.unpack_from('<IIIIIIIIII', data, shoff + i * shentsize) for i in range(shnum)]
        # (name, type, flags, addr, offset, size, link, info, addralign, entsize)
        self.secnames = [self._cstr(secs[shstrndx][4] + s[0]) for s in secs]
        self.syms = []      # (name, value, size, type, bind, shndx); index == symbol index
        self._rela = collections.defaultdict(list)   # target section -> RELA section headers (parsed on demand)
        self._rel = {}                               # target section -> {offset: (sym index, type, addend)}
        for s in secs:
            if s[1] == 2:   # SHT_SYMTAB
                stroff = secs[s[6]][4]
                for k in range(s[5] // 16):
                    nm, value, size, info, other, shndx = struct.unpack_from('<IIIBBH', data, s[4] + k * 16)
                    self.syms.append((self._cstr(stroff + nm), value, size, info & 15, info >> 4, shndx))
            elif s[1] == 4:   # SHT_RELA
                self._rela[s[7]].append(s)
        self.defs = {}
        for i, (nm, value, size, typ, bind, shndx) in enumerate(self.syms):
            if nm and shndx and shndx < 0xff00 and typ != 3 and (nm not in self.defs or bind != 0):
                self.defs[nm] = i

    def _cstr(self, off):
        return self.data[off:self.data.index(b'\0', off)].decode('utf-8', 'surrogateescape')

    def bind(self, name):
        return {0: 'local', 1: 'global', 2: 'weak'}.get(self.syms[self.defs[name]][4], '?')

    def section_of(self, name):
        return self.secnames[self.syms[self.defs[name]][5]]

    def is_bss(self, name):
        return self.secs[self.syms[self.defs[name]][5]][1] == 8   # SHT_NOBITS: dynamically initialized

    def rel(self, shndx):
        if shndx not in self._rel:
            tab = self._rel[shndx] = {}
            for s in self._rela.get(shndx, []):
                for k in range(s[5] // 12):
                    off, info, add = struct.unpack_from('<IIi', self.data, s[4] + k * 12)
                    tab[off] = (info >> 8, info & 0xff, add)
        return self._rel[shndx]

    def ents(self, name):
        """Words of a defined symbol with their relocations: [Ent(word, symbol or None, addend)]. A relocation
        against a section symbol (a local, unnamed target) gives the symbol '.section:<name>'."""
        nm, value, size, typ, bind, shndx = self.syms[self.defs[name]]
        sec = self.secs[shndx]
        rel = self.rel(shndx)
        out = []
        for k in range(size // 4):
            off = value + 4 * k
            w = 0 if sec[1] == 8 else struct.unpack_from('<I', self.data, sec[4] + off)[0]
            r = rel.get(off)
            if r is None:
                out.append(Ent(w, None, 0))
            else:
                t = self.syms[r[0]]
                sym = t[0] if t[3] != 3 else '.section:' + self.secnames[t[5]]
                out.append(Ent(w, sym if r[1] == R_AARCH64_P32_ABS32 else '.reloc%d:%s' % (r[1], sym), r[2]))
        return out


def address_points(ents):
    """Vtables of a group: [(byte offset of the address point from the group symbol, offset-to-top)], in group
    order (the first is the primary vtable). An address point follows each (offset-to-top, typeinfo) pair."""
    pts = []
    for i in range(1, len(ents)):
        if ents[i].sym and ents[i].sym.startswith('_ZTI') and ents[i - 1].sym is None:
            pts.append(((i + 1) * 4, s32(ents[i - 1].word)))
    return pts


_slot_cache = {}


def slot_of(path, name):
    """(vtable symbol, slot index from its address point) of the first slot naming `name` in the object at path, or
    None (for the text of a trap stub)."""
    if path not in _slot_cache:
        found = {}
        o = Obj(path)
        for z in sorted(o.defs):
            if z.startswith('_ZTV'):
                ents = o.ents(z)
                pts = [p for p, _ in address_points(ents)]
                for i, e in enumerate(ents):
                    below = [p for p in pts if p <= 4 * i]
                    if e.sym and below and e.sym not in found:
                        found[e.sym] = (z, (4 * i - below[-1]) // 4)
        _slot_cache.clear()
        _slot_cache[path] = found
    return _slot_cache[path].get(name)


def parse_typeinfo(ents):
    """-> TypeInfo or None. vmi base offset_flags: offset << 8 | public 2 | virtual 1 (for a virtual base the
    'offset' is the position of its vbase offset in the vtable, not an object offset)."""
    if len(ents) < 2 or not ents[0].sym:
        return None
    k = ents[0].sym
    if k == TI_VPTR['class']:
        return TypeInfo('class', 0, [])
    if k == TI_VPTR['si'] and len(ents) >= 3:
        return TypeInfo('si', 0, [(ents[2].sym, 0, False, True)])
    if k == TI_VPTR['vmi'] and len(ents) >= 4:
        n = ents[3].word
        bases = []
        for j in range(n):
            if 5 + 2 * j >= len(ents):
                return None
            w = ents[5 + 2 * j].word
            bases.append((ents[4 + 2 * j].sym, s32(w) >> 8, bool(w & 1), bool(w & 2)))
        return TypeInfo('vmi', ents[2].word, bases)
    return None


def base_offsets(ti_name, want, tis, canon, max_depth=12):
    """Offsets (non-virtual paths) of the base-class subobjects of the class of typeinfo ti_name whose canonical
    class name canon(ti) is want, from the shallowest inheritance level that has any; [] if none."""
    level = [(ti_name, 0)]
    for _ in range(max_depth):
        nxt, hits = [], []
        for name, off in level:
            ti = tis.get(name)
            if ti is None:
                continue
            for b, boff, virt, _ in ti.bases:
                if virt or not b:
                    continue
                if canon(b) == want:
                    hits.append(off + boff)
                nxt.append((b, off + boff))
        if hits:
            return sorted(set(hits))
        if not nxt:
            break
        level = nxt
    return []


def class_prefix(enc):
    """Mangled type encoding of a class -> the nested-name prefix of its members without the closing E:
    '1C' -> 'N1C', 'N2ns1CE' -> 'N2ns1C', 'St9exception' -> 'NSt9exception' (destructor: _Z + prefix + 'D1Ev')."""
    if enc.startswith('N') and enc.endswith('E'):
        return enc[:-1]
    return 'N' + enc


# ---------------------------------------------------------------------------------------------------- vftable map

VFT = re.compile(r"^const (.*)::`vftable'(?:\{for `(.*)'\})?$")


def vftable_names(units, ms, canon_msvc):
    """{(msvc ??_7 name, address): (class canonical name, for-base canonical name or None, class text)} over every
    unit's references (one name can stand for different vftables in different units: local reconstructions)."""
    out, parsed = {}, {}
    for u in units.values():
        for n, a in u.refs.items():
            if not n.startswith('??_7') or (n, a) in out:
                continue
            if n not in parsed:
                m = VFT.match(ms[n] or '')
                parsed[n] = (canon_msvc(m.group(1)), canon_msvc(m.group(2)) if m.group(2) else None,
                             m.group(1)) if m else None
            if parsed[n]:
                out[(n, a)] = parsed[n]
    return out


class RttiIndex:
    """Defined vtables and typeinfos (global/weak) of the objects in link order: mangled name -> (Obj, synthetic) of
    the first definition, the copy the linker keeps. Units do not always agree on a class (local reconstructions of
    the same name), so a class's bases are read from the typeinfos of the object whose vtable is used, then from
    the kept copies (typeinfos_for)."""

    def __init__(self):
        self.where = {}
        self._ents = {}
        self._local = {}
        self._global = None

    def add(self, obj, synthetic):
        for name in obj.defs:
            if name.startswith(('_ZTV', '_ZTI')) and obj.bind(name) != 'local' and name not in self.where:
                self.where[name] = (obj, synthetic)

    def ents(self, name):
        if name not in self._ents:
            w = self.where.get(name)
            self._ents[name] = w[0].ents(name) if w else None
        return self._ents[name]

    def typeinfos_for(self, obj):
        if self._global is None:
            self._global = {}
            for n in self.where:
                if n.startswith('_ZTI'):
                    ti = parse_typeinfo(self.ents(n) or [])
                    if ti:
                        self._global[n] = ti
        if obj.path not in self._local:
            loc = {}
            for n in obj.defs:
                if n.startswith('_ZTI') and obj.bind(n) != 'local':
                    ti = parse_typeinfo(obj.ents(n))
                    if ti:
                        loc[n] = ti
            self._local[obj.path] = loc
        return collections.ChainMap(self._local[obj.path], self._global)


def vftable_map(vnames, rtti, canon_it):
    """Bind image vftables to native address points (docs/LINK.md, kind V).

    vnames: vftable_names(); rtti: RttiIndex of all objects; canon_it(mangled): canonical class name of a _ZTV/_ZTI
    symbol. -> ({address: (ztv symbol, offset, object path, note)}, Counter of outcomes). Several decomp classes may
    name one vftable: the one whose vtable at that address point has more slots wins, then a compiled vtable over a
    synthesized one, then a name other than FUN_..., then name order."""
    by_cls = collections.defaultdict(list)
    for n in rtti.where:
        if n.startswith('_ZTV'):
            by_cls[canon_it(n)].append(n)
    stats = collections.Counter()
    cands = collections.defaultdict(list)
    for (msn, addr), (cls, base, _) in sorted(vnames.items()):
        zs = sorted(by_cls.get(cls, []))
        if not zs:
            stats['no-vtable' + ('-secondary' if base else '')] += 1
            continue
        z = zs[0]
        ents = rtti.ents(z)
        pts = address_points(ents)
        if not pts:
            stats['no-address-point'] += 1
            continue
        obj, synthetic = rtti.where[z]
        if base is None:
            ap, how = pts[0][0], 'primary'
        else:
            ti = ents[pts[0][0] // 4 - 1].sym
            offs = base_offsets(ti, base, rtti.typeinfos_for(obj), canon_it)
            if len(offs) != 1:
                stats['secondary-' + ('base-not-found' if not offs else 'base-ambiguous')] += 1
                continue
            hit = [p for p, ott in pts if ott == -offs[0]]
            if not hit:
                stats['secondary-no-vtable-at-offset'] += 1
                continue
            ap, how = hit[0], 'secondary {for %s} at +%d' % (base, offs[0])
        nxt = [p for p, _ in pts if p > ap]
        nslots = ((nxt[0] - 8) if nxt else 4 * len(ents)) // 4 - ap // 4
        rank = (-nslots, synthetic, cls.split('::')[-1].startswith('FUN_'), cls, msn)
        cands[addr].append((rank, z, ap, obj.path, how + (', synthesized' if synthetic else ''), base is not None))
    out = {}
    for addr, cs in cands.items():
        cs.sort()
        _, z, ap, path, note, sec = cs[0]
        out[addr] = (z, ap, path, note)
        stats['mapped-%s-%s' % ('secondary' if sec else 'primary', 'synth' if 'synthesized' in note else 'real')] += 1
    return out, stats


# ---------------------------------------------------------------------------------------------------- requests

def write_requests(path, reqs):
    """reqs: {class canonical name: (spelling, why set, [host units])}."""
    with open(path, 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#class\tspelling\twhy\thosts\n')
        for c, (sp, why, hosts) in sorted(reqs.items()):
            f.write('%s\t%s\t%s\t%s\n' % (c, sp or '', ','.join(sorted(why)), ' '.join(hosts)))


def load_requests(path):
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            if line.startswith('#'):
                continue
            f = line.rstrip('\n').split('\t')
            if len(f) >= 4:
                out[f[0]] = (f[1] or None, set(f[2].split(',')), f[3].split())
    return out


def spelling(text, canon_type, split_qual):
    """C++ type-id for a demangled class name ('class Foo<class Bar *>' or 'ns::Foo<Bar*>'), None if it cannot be
    named from outside (anonymous namespace, local class)."""
    if not text or 'anonymous namespace' in text or '{anon}' in text or '`' in text or "'" in text:
        return None
    return '::' + '::'.join(canon_type(p) for p in split_qual(text))


def load_synth(out):
    """synth.tsv -> [(host unit, object path)] for the objects that exist."""
    path = os.path.join(out, 'vtables', 'synth.tsv')
    res = []
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if not line.startswith('#') and len(f) >= 2 and os.path.exists(f[1]):
                res.append((f[0], f[1]))
    return res


# ---------------------------------------------------------------------------------------------------- probe

HEAD = r'''#include <typeinfo>
#include <type_traits>
#include <tr2/type_traits>
namespace __fnvnx_vt {
template <int I, class L> struct nth { typedef void type; };
template <int I, class H, class... T> struct nth<I, std::tr2::__reflection_typelist<H, T...>>
    : nth<I - 1, std::tr2::__reflection_typelist<T...>> {};
template <class H, class... T> struct nth<0, std::tr2::__reflection_typelist<H, T...>> { typedef H type; };
template <class L> struct len { static const unsigned value = 0; };
template <class... T> struct len<std::tr2::__reflection_typelist<T...>> { static const unsigned value = sizeof...(T); };
template <class C> using db = typename std::tr2::direct_bases<C>::type;
template <class C> using ab = typename std::tr2::bases<C>::type;
template <class C, int I> using dbase = typename nth<I, db<C>>::type;
template <class C, int I> using abase = typename nth<I, ab<C>>::type;
// the deallocation of a deleting destructor: class operator delete (unsized first), else the global sized one
template <class T> auto dealloc(void *p, int) -> decltype(T::operator delete(p), void()) { T::operator delete(p); }
template <class T> auto dealloc(void *p, long) -> decltype(T::operator delete(p, sizeof(T)), void())
{ T::operator delete(p, sizeof(T)); }
template <class T> void dealloc(void *p, ...) { ::operator delete(p, sizeof(T)); }
}
'''
NS = '__fnvnx_vt::'


def probe_items(k, t, phase, nd=0, na=0):
    """Source lines (one item each) for class number k spelled t. Phase 1: counts and flags (info), the class's own
    typeinfo symbol (self), the derived class with a key function (vt). Phase 2 (nd direct, na direct or indirect
    bases from phase 1): per direct base its typeid, offset and public-ness; per base its typeid and offset (a
    virtual base has no constant offset: the variable lands in .bss, initialized at run time)."""
    if phase == 1:
        return [('info', 'extern "C" const unsigned __fnvnx_vti_%d[5] = {__is_polymorphic(%s), __is_final(%s), '
                 '%slen<%sdb<%s>>::value, %slen<%sab<%s>>::value, sizeof(%s)};' % (k, t, t, NS, NS, t, NS, NS, t, t)),
                ('self', 'extern "C" const void *const __fnvnx_vtc_%d = &typeid(%s);' % (k, t)),
                ('vt', 'namespace __fnvnx_vt { struct V%d : %s { virtual void __fnvnx_key(); }; '
                 'void V%d::__fnvnx_key() {} }' % (k, t, k))]
    items = []
    for i in range(nd):
        b = '%sdbase<%s, %d>' % (NS, t, i)
        items.append(('db%d' % i, 'extern "C" const void *const __fnvnx_vtb_%d_%d = &typeid(%s); extern "C" const '
                      'unsigned __fnvnx_vto_%d_%d[2] = {(unsigned)(unsigned long)(const %s *)(const %s *)4096, '
                      'std::is_convertible<%s *, %s *>::value};' % (k, i, b, k, i, b, t, t, b)))
    for j in range(na):
        b = '%sabase<%s, %d>' % (NS, t, j)
        items.append(('ab%d' % j, 'extern "C" const void *const __fnvnx_vta_%d_%d = &typeid(%s);' % (k, j, b)))
        items.append(('av%d' % j, 'extern "C" const unsigned __fnvnx_vtv_%d_%d = (unsigned)(unsigned long)'
                      '(const %s *)(const %s *)4096;' % (k, j, b, t)))
    return items


def compile_probe(tree, workdir, unit, items, tag, extra=()):
    """items: [(class k, item name, line)]. Drops the items GCC reports errors in (probe lines named by an error
    or by its 'required from' notes; an 'info' failure drops the whole class) and recompiles.
    -> (Obj or None, surviving items)."""
    base = unit.replace('/', '_') + '.' + tag
    src, obj = os.path.join(workdir, base + '.cpp'), os.path.join(workdir, base + '.o')
    alive = list(items)
    fl = [f if f != '-O2' else '-O0' for f in layout.flags(tree)] + list(extra)
    head = HEAD + '#include "%s"\n' % os.path.join(tree, unit)
    first = head.count('\n') + 1
    srcfile = os.path.join(tree, unit)
    key = hashlib.sha1(('%s|%s|%s' % (' '.join(fl), os.path.getmtime(srcfile) if os.path.exists(srcfile) else 0,
                                      '\n'.join(line for _, _, line in alive))).encode()).hexdigest()
    done = os.path.join(workdir, base + '.done.json')
    try:
        d = json.load(open(done))
        if d.get('key') == key and os.path.exists(obj) and d.get('ok'):
            names = set(map(tuple, d['alive']))
            return Obj(obj), [it for it in alive if (it[0], it[1]) in names]
    except (OSError, ValueError, KeyError):
        pass
    for _ in range(10):
        if not alive:
            return None, []
        open(src, 'w').write(head + '\n'.join(line for _, _, line in alive) + '\n')
        try:
            r = subprocess.run([CXX, *fl, src, '-o', obj], cwd=tree, capture_output=True, text=True,
                               errors='replace', timeout=600)
        except subprocess.TimeoutExpired:
            return None, []
        if os.environ.get('FNVNX_PROBE_DEBUG'):
            print(r.stderr[:3000])
        if r.returncode == 0:
            json.dump({'key': key, 'ok': True, 'alive': [[k, nm] for k, nm, _ in alive]}, open(done, 'w'))
            return Obj(obj), alive
        bad = {int(n) - first for n in re.findall(r'%s:(\d+):\d+:' % re.escape(os.path.basename(src)), r.stderr)}
        bad = {b for b in bad if 0 <= b < len(alive)}
        if not bad:     # an error outside the items: the unit itself does not compile here
            return None, []
        badinfo = {alive[b][0] for b in bad if alive[b][1] == 'info'}
        alive = [it for i, it in enumerate(alive) if i not in bad and it[0] not in badinfo]
    return None, []


def _words(obj, name, n):
    if name not in obj.defs:
        return None
    if obj.is_bss(name):
        return 'dynamic'
    return [x.word for x in obj.ents(name)[:n]]


def _ptr(obj, name):
    if name not in obj.defs or obj.is_bss(name):
        return None
    e = obj.ents(name)
    return e[0].sym if e else None


def probe_host(tree, workdir, unit, classes):
    """classes: [(k, spelling)] -> {k: probe result} for the classes the unit can name. A result holds the class's
    mangled encoding (enc), flags, and when available the derived class's vtable group (vt_ents, made with the
    derived class V<k>: vt_k) and the direct bases (bases: [(typeinfo symbol, offset or None, virtual, public)])."""
    res = {}
    obj, alive = compile_probe(tree, workdir, unit,
                               [(k, nm, line) for k, t in classes for nm, line in probe_items(k, t, 1)], 'vt1')
    if obj is None:
        return res
    have = collections.defaultdict(set)
    for k, nm, _ in alive:
        have[k].add(nm)
    for k, t in classes:
        info = _words(obj, '__fnvnx_vti_%d' % k, 5) if 'info' in have[k] else None
        if not isinstance(info, list) or len(info) < 5:
            continue
        r = res[k] = dict(spelling=t, polymorphic=bool(info[0]), final=bool(info[1]), ndirect=info[2], nall=info[3],
                          size=info[4], enc=None, vt_k=k)
        if not r['ndirect'] and not r['nall']:   # a root class: nothing to probe in phase 2
            r['bases'], r['all_bases'], r['virtual_any'] = [], [], False
        me = _ptr(obj, '__fnvnx_vtc_%d' % k) if 'self' in have[k] else None
        if me and me.startswith('_ZTI'):
            r['enc'] = me[4:]
        dn = 'V%d' % k
        d = 'N10__fnvnx_vt%d%sE' % (len(dn), dn)
        if 'vt' in have[k] and '_ZTV' + d in obj.defs:
            r['vt_ents'] = obj.ents('_ZTV' + d)
            r['virtual_bases'] = '_ZTT' + d in obj.defs
            dti = obj.ents('_ZTI' + d) if '_ZTI' + d in obj.defs else []
            if not r['enc'] and len(dti) >= 3 and dti[2].sym and dti[2].sym.startswith('_ZTI'):
                r['enc'] = dti[2].sym[4:]
    items = [(k, nm, line) for k, t in classes if k in res and res[k]['ndirect'] + res[k]['nall'] <= 64
             for nm, line in probe_items(k, t, 2, res[k]['ndirect'], res[k]['nall'])]
    if not items:
        return res
    obj2, alive2 = compile_probe(tree, workdir, unit, items, 'vt2')
    if obj2 is None:
        return res
    have2 = collections.defaultdict(set)
    for k, nm, _ in alive2:
        have2[k].add(nm)
    for k, r in res.items():
        bases, ok = [], True
        for i in range(r['ndirect']):
            ti = _ptr(obj2, '__fnvnx_vtb_%d_%d' % (k, i)) if 'db%d' % i in have2[k] else None
            ow = _words(obj2, '__fnvnx_vto_%d_%d' % (k, i), 2)
            if not ti or ow is None:
                ok = False
                break
            bases.append((ti, None, True, None) if ow == 'dynamic' else (ti, s32(ow[0]) - 4096, False, bool(ow[1])))
        allb, virt = [], False
        for j in range(r['nall']):
            ti = _ptr(obj2, '__fnvnx_vta_%d_%d' % (k, j)) if 'ab%d' % j in have2[k] else None
            if not ti:
                ok = False
                break
            allb.append(ti)
            if 'av%d' % j in have2[k] and _words(obj2, '__fnvnx_vtv_%d_%d' % (k, j), 1) == 'dynamic':
                virt = True
        if ok:
            r['bases'], r['all_bases'], r['virtual_any'] = bases, allb, virt or any(b[2] for b in bases)
    return res


# ---------------------------------------------------------------------------------------------------- synthesis

def synth_vtable(r):
    """Probe result -> (C's vtable group as [Ent], None) or (None, reason)."""
    ents, enc = r.get('vt_ents'), r.get('enc')
    if not r['polymorphic']:
        return None, 'not polymorphic'
    if r['final']:
        return None, 'final class'
    if ents is None or not enc:
        return None, 'derived probe class does not compile (deleted/private destructor, ...)'
    if r.get('virtual_bases') or r.get('virtual_any'):
        return None, 'virtual bases'
    dn = 'V%d' % r['vt_k']
    dpre = 'N10__fnvnx_vt%d%s' % (len(dn), dn)
    cpre = class_prefix(enc)
    key = '_Z%s11__fnvnx_keyEv' % dpre
    out = []
    for e in ents:
        s = e.sym
        if s == key:
            continue
        if s and '__fnvnx_vt' in s:
            if s == '_ZTI%sE' % dpre:
                s = '_ZTI' + enc
            else:
                m = re.match(r'^(_ZT[hv][n0-9_]*_|_Z)%s(D[012]Ev)$' % re.escape(dpre), s)
                if not m:
                    return None, 'unexpected probe symbol in the vtable'
                s = m.group(1) + cpre + m.group(2)
        elif s and s.startswith(('.section:', '.reloc')):
            return None, 'slot target is local to the unit'
        out.append(Ent(e.word, s, e.addend))
    if len(out) != len(ents) - 1:
        return None, 'key slot not found'
    return out, None


def synth_typeinfo(r):
    """Probe result -> (TypeInfo with C's bases, None) or (None, reason)."""
    if 'bases' not in r and not r['ndirect'] and not r['nall']:
        r = dict(r, bases=[], all_bases=[], virtual_any=False)
    if 'bases' not in r:
        return None, 'bases not nameable'
    if r['virtual_any']:
        return None, 'virtual bases'
    bases = r['bases']
    if any(b[0].startswith(('.section:', '.reloc')) for b in bases):
        return None, 'base local to the unit'
    allb = r['all_bases']
    flags = 1 if len(set(allb)) != len(allb) else 0       # __non_diamond_repeat_mask (no virtual bases here)
    if not bases:
        return TypeInfo('class', 0, []), None
    if len(bases) == 1 and bases[0][3] and bases[0][1] == 0:
        return TypeInfo('si', 0, [(bases[0][0], 0, False, True)]), None
    return TypeInfo('vmi', flags, [(b[0], b[1], False, b[3]) for b in bases]), None


def typeinfo_ents(enc, ti):
    out = [Ent(0, TI_VPTR[ti.kind], 8), Ent(0, '_ZTS' + enc, 0)]
    if ti.kind == 'si':
        out.append(Ent(0, ti.bases[0][0], 0))
    elif ti.kind == 'vmi':
        out += [Ent(ti.flags, None, 0), Ent(len(ti.bases), None, 0)]
        for b, off, virt, pub in ti.bases:
            out += [Ent(0, b, 0), Ent(((off << 8) | (2 if pub else 0) | (1 if virt else 0)) & 0xffffffff, None, 0)]
    return out


def emit_data(sym, ents):
    lines = ['    .section .data.rel.ro.%s,"aw"' % sym, '    .p2align 2', '    .weak %s' % sym,
             '    .type %s, %%object' % sym, '    .size %s, %d' % (sym, 4 * len(ents)), '%s:' % sym]
    for e in ents:
        if e.sym is None:
            lines.append('    .word 0x%08x' % e.word)
        else:
            lines.append('    .word %s%s' % (e.sym, '%+d' % e.addend if e.addend else ''))
    return '\n'.join(lines) + '\n'


def emit_string(sym, text):
    return ('    .section .rodata.%s,"a"\n    .weak %s\n    .type %s, %%object\n    .size %s, %d\n%s:\n'
            '    .asciz "%s"\n' % (sym, sym, sym, sym, len(text) + 1, sym, text))


THUNK = re.compile(r'^_ZTh(n?)(\d+)_(.+)$')


def emit_thunk(sym):
    """Non-virtual this-adjusting thunk _ZTh[n]<k>_<function>: adjust w0 (the ILP32 this), tail-call the function."""
    m = THUNK.match(sym)
    if not m:
        return None
    k, target, op = int(m.group(2)), '_Z' + m.group(3), 'sub' if m.group(1) else 'add'
    if k < 4096:
        adj = ['    %s w0, w0, #%d' % (op, k)]
    else:
        adj = ['    mov w16, #%d' % (k & 0xffff)] + (['    movk w16, #%d, lsl #16' % (k >> 16)] if k >> 16 else []) + \
              ['    %s w0, w0, w16' % op]
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % sym, '    .p2align 2', '    .weak %s' % sym,
                      '    .type %s, %%function' % sym, '%s:' % sym] + adj +
                     ['    b %s' % target, '    .size %s, .-%s' % (sym, sym)]) + '\n'


def demangle(names):
    names = sorted(set(names))
    if not names:
        return {}
    p = subprocess.run([CXXFILT], input='\n'.join(names) + '\n', capture_output=True, text=True,
                       errors='surrogateescape')
    return dict(zip(names, p.stdout.split('\n')))


LD = os.environ.get('LD', 'aarch64-linux-gnu-ld')
OBJCOPY = os.environ.get('OBJCOPY', 'aarch64-linux-gnu-objcopy')
EMIT_FLAGS = ('-O2', '-fkeep-inline-functions', '-ffunction-sections', '-fdata-sections')
INIT_SECTIONS = ('.init_array', '.fini_array', '.preinit_array', '.ctors', '.dtors')


def slot_wants(items, defined, used=None):
    """Function symbols the synthesized vtables of one host unit need and no compiled object defines (thunks stand
    for their target)."""
    want = set()
    for kind, enc, data in items:
        if kind != 'vt' or (used is not None and enc not in used):
            continue
        for e in data:
            s = e.sym
            if not s or s.startswith(('.', '__cxa_')):
                continue
            m = THUNK.match(s)
            if m:
                s = '_Z' + m.group(3)
            if not s.startswith('_ZT') and s not in defined:
                want.add(s)
    return want


def extract_host(tree, workdir, unit, classes, want):
    """Emit the inline functions and implicit destructors the synthesized vtables of a host unit need, from GCC.

    classes: [(k, spelling, enc)] of the classes synthesized on this host; want: the slot symbols nobody defines.
    The host unit is compiled again with -fkeep-inline-functions and, per class, a qualified destructor call (so the
    implicit destructor D1/D2 is defined; for a class without key function its vtable, D0 and inline virtuals come
    with it); a deleting destructor still missing is emulated (D1, then the class's or the global sized operator
    delete) under its mangled name. `ld -r --gc-sections` keeps what the wanted symbols reach (static initializers
    removed first, so they are no roots); every global definition is weakened: a copy of a definition of the host
    unit loses to the real one, COMDAT copies dedupe. -> (path of the object or None, {wanted symbols defined})."""
    base = unit.replace('/', '_')
    items = []
    for k, sp, enc in classes:
        items.append((k, 'dtor', 'namespace __fnvnx_vt { typedef %s E%d; extern "C" void __fnvnx_vte_%d(E%d *p) '
                      '{ p->E%d::~E%d(); } }' % (sp, k, k, k, k, k)))
    obj3, alive3 = compile_probe(tree, workdir, unit, items, 'vt3', EMIT_FLAGS)
    if obj3 is None:
        return None, set()
    have = {n for n in obj3.defs if obj3.bind(n) != 'local'}
    items4 = []
    for k, sp, enc in classes:
        d0 = '_Z%sD0Ev' % class_prefix(enc)
        if d0 in want and d0 not in have:
            items4.append((k, 'd0', 'namespace __fnvnx_vt { typedef %s F%d; void __fnvnx_vtd_%d(void *) __asm__("%s"); '
                           'void __fnvnx_vtd_%d(void *p) { static_cast<F%d *>(p)->F%d::~F%d(); dealloc<F%d>(p, 0); } }'
                           % (sp, k, k, d0, k, k, k, k, k)))
    inputs = []
    clean = os.path.join(workdir, base + '.vt3s.o')
    if not strip_init(obj3.path, clean):
        return None, set()
    inputs.append(clean)
    if items4:
        obj4, _ = compile_probe(tree, workdir, unit, items4, 'vt4', ('-O2',))
        clean4 = os.path.join(workdir, base + '.vt4s.o')
        if obj4 is not None and strip_init(obj4.path, clean4):
            have |= {n for n in obj4.defs if n.startswith('_Z') and obj4.bind(n) != 'local'}
            inputs.append(clean4)
    parts, roots = [], set()
    for i, path in enumerate(inputs):   # each input holds the whole host unit: gc and weaken them one by one
        part = gc_weaken(path, want, os.path.join(workdir, '%s.x%d' % (base, i)))
        if part:
            parts.append(part[0])
            roots |= part[1]
    if not parts:
        return None, set()
    if len(parts) == 1:
        return parts[0], roots
    wo = os.path.join(workdir, base + '.vtx.o')
    r = subprocess.run([LD, '-m', 'aarch64linux32', '-r', '-o', wo, *parts], capture_output=True, text=True)
    if r.returncode:
        print('vtables: ld -r failed for %s: %s' % (unit, r.stderr[:500]), flush=True)
        return parts[0], roots
    return wo, roots


def strip_init(src, dst):
    """Copy an object without its static-initializer tables: the host unit's initializers must never run twice (and
    must not be gc roots)."""
    args = []
    for sec in INIT_SECTIONS:
        args += ['-R', sec, '-R', '.rela' + sec]
    return subprocess.run([OBJCOPY, *args, src, dst], capture_output=True).returncode == 0


def undefine_strong(src, dst, keep):
    """Copy an ELF32 object with its strong global definitions outside COMDAT groups turned into undefined
    references (st_shndx 0), except the names in keep: what the emitted inline code calls or reads of the host unit
    then binds to the host's real definition (by name or by address, like any reference) instead of being copied,
    and `--gc-sections` drops those bodies."""
    import elfsyms
    data = bytearray(open(src, 'rb').read())
    o = Obj(src)
    comdat = {i for i, s in enumerate(elfsyms.symbols(src), 1) if s.comdat}
    sym_sec = next(s for s in o.secs if s[1] == 2)
    n = 0
    for i, (nm, value, size, typ, bind, shndx) in enumerate(o.syms):
        if bind == 1 and shndx and shndx < 0xff00 and typ in (1, 2) and i not in comdat and nm not in keep:
            off = sym_sec[4] + 16 * i
            struct.pack_into('<II', data, off + 4, 0, 0)            # st_value, st_size
            struct.pack_into('<H', data, off + 14, 0)               # st_shndx
            data[off + 12] = (bind << 4) | 0                        # st_info: global, NOTYPE
            n += 1
    open(dst, 'wb').write(data)
    return n


def gc_weaken(path, want, stem):
    """`ld -r --gc-sections` of one object with the wanted symbols it defines as roots (the host's own strong
    definitions undefined first), then every global definition weakened. -> (object path, roots) or None."""
    o = Obj(path)
    roots = sorted(n for n in want if n in o.defs and o.bind(n) != 'local')
    if not roots:
        return None
    script, uo, xo, wo = stem + '.roots.ld', stem + 'u.o', stem + '.o', stem + 'w.o'
    undefine_strong(path, uo, set(roots))
    open(script, 'w').write('EXTERN(\n%s\n)\n' % '\n'.join(roots))
    r = subprocess.run([LD, '-m', 'aarch64linux32', '-r', '--gc-sections', '-o', xo, script, uo],
                       capture_output=True, text=True)
    if r.returncode:
        print('vtables: ld -r --gc-sections failed for %s: %s' % (path, r.stderr[:500]), flush=True)
        return None
    x = Obj(xo)
    argf = stem + '.weaken'
    open(argf, 'w').write('\n'.join('--weaken-symbol=' + n for n in sorted(x.defs) if x.bind(n) == 'global') + '\n')
    if subprocess.run([OBJCOPY, '@' + argf, xo, wo], capture_output=True).returncode:
        return None
    return wo, set(roots)


def encode(r):
    d = dict(r)
    if d.get('vt_ents') is not None:
        d['vt_ents'] = [list(e) for e in d['vt_ents']]
    return d


def decode(d):
    r = dict(d)
    if r.get('vt_ents') is not None:
        r['vt_ents'] = [Ent(*e) for e in r['vt_ents']]
    if r.get('bases') is not None:
        r['bases'] = [tuple(b) for b in r['bases']]
    return r


def run(out, tree, jobs=4):
    """The synthesis step: vtable_requests.tsv + rtti_defined.txt (written by resolve) -> out/vtables/ (synth.tsv,
    classes.tsv, stats.txt, one .vt.S / zz_vt_*.o per host unit). Probe results are cached per host unit (source
    mtime + class list) in out/vtables/cache.json."""
    import sigs   # sibling module (fnvlink.py puts this directory on sys.path)
    reqs = load_requests(os.path.join(out, 'vtable_requests.tsv'))
    dpath = os.path.join(out, 'rtti_defined.txt')
    defined = set(open(dpath, encoding='utf-8', errors='surrogateescape').read().split()) if os.path.exists(dpath) \
        else set()
    vdir = os.path.join(out, 'vtables')
    work = os.path.join(vdir, 'probe')
    os.makedirs(work, exist_ok=True)
    for name in os.listdir(vdir):
        if name.endswith(('.vt.S', '.o')):
            os.remove(os.path.join(vdir, name))
    print('vtables: %d classes requested' % len(reqs), flush=True)
    todo = {c: (sp, hosts) for c, (sp, why, hosts) in reqs.items() if sp and hosts}
    results = {}          # class -> (host, probe result)
    tried = collections.defaultdict(set)
    cache_path = os.path.join(vdir, 'cache.json')
    try:
        cache = json.load(open(cache_path))
    except (OSError, ValueError):
        cache = {}
    newcache, kno, rounds, stats = {}, 0, 0, collections.Counter()

    def one(h, cls):
        src = os.path.join(tree, h)
        key = hashlib.sha1(('%s|%s|%s' % (h, os.path.getmtime(src) if os.path.exists(src) else 0,
                                          '|'.join(sp for _, sp, _ in cls))).encode()).hexdigest()
        if key in cache:
            got = cache[key]
            return key, {cls[int(i)][0]: decode(v) for i, v in got.items()}, got
        try:
            res = probe_host(tree, work, h, [(k, sp) for k, sp, _ in cls])
        except Exception as e:   # one host must not stop the step
            print('vtables: probe of %s failed: %r' % (h, e), flush=True)
            res = {}
        pos = {k: i for i, (k, _, _) in enumerate(cls)}
        return key, res, {str(pos[k]): encode(v) for k, v in res.items()}

    while todo and rounds < 8:
        rounds += 1
        open_ = {c: [h for h in hs if h not in tried[c]][:3] for c, (_, hs) in todo.items()}
        open_ = {c: hs for c, hs in open_.items() if hs}
        assign = collections.defaultdict(list)
        while open_:   # greedy: the host unit that can take most of the open classes first
            cnt = collections.Counter(h for hs in open_.values() for h in hs)
            h = max(sorted(cnt), key=lambda x: cnt[x])
            for c in [c for c, hs in open_.items() if h in hs]:
                assign[h].append(c)
                tried[c].add(h)
                del open_[c]
        if not assign:
            break
        jobsl = []
        for h, cs in sorted(assign.items()):
            jobsl.append((h, [(kno + i, todo[c][0], c) for i, c in enumerate(sorted(cs))]))
            kno += len(cs)
        print('vtables round %d: %d classes, %d host units' % (rounds, sum(len(c) for _, c in jobsl), len(jobsl)),
              flush=True)
        with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
            for (h, cls), (key, res, enc) in zip(jobsl, ex.map(lambda j: one(*j), jobsl)):
                newcache[key] = enc
                for k, _, c in cls:
                    r = res.get(k)
                    if r is not None and r.get('enc'):
                        results.setdefault(c, (h, r))
        todo = {c: v for c, v in todo.items() if c not in results}
        json.dump({**cache, **newcache}, open(cache_path + '.tmp', 'w'))   # an interrupted run resumes from here
        os.replace(cache_path + '.tmp', cache_path)
        # base typeinfos no object defines: probe them too, on the host unit that named them
        have_enc = {r['enc'] for _, r in results.values()}
        want = {(b[0], h) for h, r in results.values() for b in r.get('bases') or []
                if b[0] and b[0].startswith('_ZTI') and b[0] not in defined and b[0][4:] not in have_enc}
        dem = demangle(n for n, _ in want)
        for n, h in sorted(want):
            d = dem.get(n, '')
            if d.startswith('typeinfo for '):
                sp = spelling(d[len('typeinfo for '):], sigs.canon_type, sigs.split_qual)
                cname = 'base:' + n
                if sp and cname not in results and cname not in todo:
                    todo[cname] = (sp, [h])
    json.dump(newcache, open(cache_path + '.tmp', 'w'))
    os.replace(cache_path + '.tmp', cache_path)

    # ---- emit, per host unit
    per_host = collections.defaultdict(list)
    done_enc, rows = set(), []
    for c, (h, r) in sorted(results.items()):
        enc = r['enc']
        if enc in done_enc:
            continue
        done_enc.add(enc)
        what = []
        if r['polymorphic'] and '_ZTV' + enc not in defined:
            vt, why = synth_vtable(r)
            if vt is not None:
                per_host[h].append(('vt', enc, vt)); what.append('vtable'); stats['vtables synthesized'] += 1
            else:
                stats['vtable not synthesized: ' + why] += 1; what.append('no vtable: ' + why)
        if '_ZTI' + enc not in defined:
            ti, why = synth_typeinfo(r)
            if ti is not None:
                per_host[h].append(('ti', enc, ti)); what.append('typeinfo'); stats['typeinfos synthesized'] += 1
            else:
                stats['typeinfo not synthesized: ' + why] += 1; what.append('no typeinfo: ' + why)
        rows.append((c, h, enc, '; '.join(what) or 'nothing missing'))
    for c in sorted(todo):
        stats['class not nameable in any host unit'] += 1
        rows.append((c, '-', '-', 'no host unit could name the class'))
    for c, (sp, why, hosts) in reqs.items():
        if not (sp and hosts):
            stats['class without spelling or host (anonymous, units not compiled)'] += 1
    synth_rows, thunks_done, built = [], set(), []
    for h, items in sorted(per_host.items()):
        base = h.replace('/', '_')
        s_path, o_path = os.path.join(vdir, base + '.vt.S'), os.path.join(vdir, 'zz_vt_' + base + '.o')
        text = ['// fnv-nx synthesized vtables/typeinfo, classes seen by %s (tools/link/vtables.py; local output)' % h]
        thunks = set()
        for kind, enc, data in items:
            if kind == 'vt':
                text.append(emit_data('_ZTV' + enc, data))
                thunks.update(e.sym for e in data if e.sym and e.sym.startswith('_ZTh') and e.sym not in defined)
            else:
                text.append(emit_data('_ZTI' + enc, typeinfo_ents(enc, data)))
                if '_ZTS' + enc not in defined:
                    text.append(emit_string('_ZTS' + enc, enc))
        for t in sorted(thunks - thunks_done):
            code = emit_thunk(t)
            if code:
                text.append(code)
                stats['thunks generated'] += 1
            thunks_done.add(t)
        open(s_path, 'w').write('\n'.join(text) + '\n')
        vt_o = os.path.join(work, base + '.vt.o')
        r = subprocess.run([AS, '-mabi=ilp32', s_path, '-o', vt_o], capture_output=True, text=True)
        if r.returncode:
            print('vtables: as failed for %s: %s' % (s_path, r.stderr[:1000]), flush=True)
            stats['objects not assembled'] += 1
            continue
        synth_rows.append((h, o_path, ' '.join(sorted({enc for _, enc, _ in items}))))
        built.append((h, vt_o, o_path, items))

    # ---- the slot functions no object defines: inline virtuals and implicit destructors, emitted from the host
    spell = {r['enc']: r['spelling'] for _, r in results.values()}
    # slot functions are emitted only for classes whose vtable a compiled object references (the others back an
    # address-map entry only: their slots trap, resolve binds them like the virtuals defined nowhere)
    used = {r['enc'] for c, (_, r) in results.items() if 'vtable-ref' in reqs.get(c, (None, set(), []))[1]}
    xcache_path = os.path.join(vdir, 'emit_cache.json')
    try:
        xcache = json.load(open(xcache_path))
    except (OSError, ValueError):
        xcache = {}
    newx, wanted_all, emitted_all = {}, set(), set()

    def emit_one(job):
        h, vt_o, o_path, items = job
        want = slot_wants(items, defined, used)
        classes = [(i, spell[enc], enc) for i, (kind, enc, _) in enumerate(items) if kind == 'vt' and enc in spell
                   and enc in used]
        if not want or not classes:
            return h, None, want, set(), None
        src = os.path.join(tree, h)
        key = hashlib.sha1(('emit4|%s|%s|%s|%s|%s' % (' '.join(layout.flags(tree)), h,
                                                os.path.getmtime(src) if os.path.exists(src) else 0,
                                                ' '.join(sorted(want)), '|'.join(sp for _, sp, _ in classes))
                            ).encode()).hexdigest()
        got = xcache.get(key)
        if got and (got[0] is None or os.path.exists(got[0])):
            return h, got[0], want, set(got[1]), key
        try:
            path, roots = extract_host(tree, work, h, classes, want)
        except Exception as e:   # one host must not stop the step
            print('vtables: emitting the slot functions of %s failed: %r' % (h, e), flush=True)
            path, roots = None, set()
        return h, path, want, roots, key

    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        emitted = {h: (path, want, roots, key) for h, path, want, roots, key in ex.map(emit_one, built)}
    for h, vt_o, o_path, items in built:
        path, want, roots, key = emitted[h]
        if key:
            newx[key] = [path, sorted(roots)]
        wanted_all |= want
        emitted_all |= roots
        if path:
            tmp = o_path + '.tmp'
            r = subprocess.run([LD, '-m', 'aarch64linux32', '-r', '-o', tmp, vt_o, path], capture_output=True,
                               text=True)
            if r.returncode == 0 and strip_init(tmp, o_path):   # never a static initializer in these objects
                os.remove(tmp)
                stats['objects with emitted slot functions'] += 1
                continue
            print('vtables: ld -r failed for %s: %s' % (h, r.stderr[:500]), flush=True)
        shutil.copyfile(vt_o, o_path)
    json.dump(newx, open(xcache_path + '.tmp', 'w'))
    os.replace(xcache_path + '.tmp', xcache_path)
    stats['slot functions no object defines'] = len(wanted_all)
    stats['slot functions emitted from the host units'] = len(emitted_all & wanted_all)
    with open(os.path.join(vdir, 'synth.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#host_unit\tobject\tclasses (mangled encodings)\n')
        for row in synth_rows:
            f.write('\t'.join(row) + '\n')
    with open(os.path.join(vdir, 'classes.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('#class\thost_unit\tencoding\tresult\n')
        for row in rows:
            f.write('\t'.join(row) + '\n')
    lines = ['vtables: %d classes requested, %d named by a host unit (%d probe rounds), %d objects'
             % (len(reqs), len(results), rounds, len(synth_rows))]
    lines += ['  %-64s %d' % kv for kv in sorted(stats.items())]
    open(os.path.join(vdir, 'stats.txt'), 'w').write('\n'.join(lines) + '\n')
    print('\n'.join(lines), flush=True)
    return stats
