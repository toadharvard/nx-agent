"""vtmodel.py - virtual calls on both machines (difffuzz-13): the player's FalloutNV.exe vftables paired with the
link's Itanium vtables (address map kind V).

An object the fuzzer gives a class (`this` of a member function, a pointer / reference parameter to a class) gets a
vptr planted in its first word (world.Case.vptrs): on x86 an image vftable V of the class or of a class derived from
it at offset 0, whose slots (pinned in the world) are the exe's own words, the original addresses of the functions;
on AArch64 the native address point that the kind-V row pairs with V. A virtual call then reads a real slot on each
machine: x86 calls the original address (intercepted, or run when the unit holds it), the native code the native
definition (`__fnv_<X>` = a code stub of X or the unit's own definition, D0 / D1 named stubs of a `??_G`): both
are call events of the same original address, compared like direct calls (runner.compare).

The native address point is a copy of the paired vtable synthesized in the AArch64 private range (VT_BASE): the
group's words up to the end of that vtable, read from the defining object in the link's fixed/, each relocated slot
resolved for this image (its own definition, CODE_STUB + X for `__fnv_<X>`, a named stub that names the original
address, or a fresh named stub), padded to the exe's slot count with `__s13_vtpad_<V>_<k>` stubs where the link's
native vtable is shorter (a pairing gap: classify.py vptr-unpaired).

Sources: the exe (FNVNX_EXE, read only, never copied: RTTI complete object locators give every vftable with its
class, offset and hierarchy; the slots are the .text words from V on), addrmap.tsv kind V rows and fixed/ objects,
units.json `??_7C@@6B@` names (the decomp's class name of a vftable). The scan is cached under FNVNX_DFWORK
(derived from the exe: local only, never committed). Without the exe nothing is planted.
"""
import collections
import fcntl
import json
import os
import re
import struct
import sys

import config

sys.path.insert(0, str(config.PORT / 'loader' / 'tools'))
sys.path.insert(0, str(config.LINKTOOLS))

CACHE_VERSION = 2
TEXT = (0x00401000, 0x00fdf000)
MAX_DERIVED = 6            # vftables per static class: its own, then up to this many derived classes at offset 0
VT_BASE, VT_END = 0x0bc00000, 0x0c000000      # synthesized native vtable groups (AArch64 private range)
VT_STUBS = 0x097f0000      # fresh named stubs for slot targets the unit image never names (top of the named stubs)
ADDR_ALIAS = re.compile(r'^__fnv_([0-9a-f]{8})$')
ADDR_ANY = re.compile(r'^__fnv_([0-9a-f]{8})(?:_[CD][0-9])?$')
MSVC_VFT = re.compile(r'^\?\?_7((?:[A-Za-z_]\w*@)+)@6B@$')


def exe_path():
    p = os.environ.get('FNVNX_EXE', '')
    return p if p and os.path.isfile(p) else None


def td_class(name):
    """'.?AVBar@Foo@@' -> 'Foo::Bar' (templates and odd names: None)."""
    m = re.match(r'^\.\?A[VU]((?:[A-Za-z_]\w*@)+)@$', name)
    if not m:
        return None
    return '::'.join(reversed([p for p in m.group(1).split('@') if p]))


def scan_exe(path):
    """{V: dict(cls, off, bases [(class, mdisp)], slots)} of every vftable with an RTTI locator in the exe."""
    import fnvnx_pe
    pe = fnvnx_pe.PE(open(path, 'rb').read())
    img = fnvnx_pe.Image(pe)

    def cstr(a):
        s = img.string(a, 512)
        return s[0].decode('latin-1') if s else None

    cols = {}

    def col(a):
        if a in cols:
            return cols[a]
        got = None
        w = img.read(a, 20)
        if w is not None:
            sig, off, _, td, chd = struct.unpack('<IIIII', w)
            name = cstr(td + 8) if sig == 0 and img.read(td, 8) is not None else None
            if name and name.startswith('.?A'):
                bases = []
                h = img.read(chd, 16)
                if h is not None:
                    hsig, _, nb, bca = struct.unpack('<IIII', h)
                    if hsig == 0 and 0 < nb < 128:
                        for i in range(nb):
                            bcd = img.read32(bca + 4 * i)
                            b = img.read(bcd, 12) if bcd is not None else None
                            if b is None:
                                break
                            btd, _, mdisp = struct.unpack('<IIi', b)
                            bn = cstr(btd + 8)
                            if bn and td_class(bn):
                                bases.append((td_class(bn), mdisp))
                got = dict(cls=td_class(name), off=off, bases=bases)
        cols[a] = got
        return got

    out = {}
    for m in img.maps:
        n = m.file_bytes // 4
        for i in range(n - 1):
            w = struct.unpack_from('<I', m.data, 4 * i)[0]
            if not 0x00fdf000 <= w < 0x02100000:
                continue
            nxt = struct.unpack_from('<I', m.data, 4 * i + 4)[0]
            if not TEXT[0] <= nxt < TEXT[1]:
                continue
            c = col(w)
            if c is None:
                continue
            v = m.va + 4 * i + 4
            slots = img.vtable_slots(v, cap=1024)
            if slots:
                out[str(v)] = dict(c, slots=slots)
    return out


class ExeVftables:
    def __init__(self, path, cache_dir):
        st = os.stat(path)
        key = '%d-%d' % (st.st_size, int(st.st_mtime))
        os.makedirs(cache_dir, exist_ok=True)
        cp = os.path.join(cache_dir, 'exe_vftables.json')
        with open(cp + '.lock', 'w') as lk:            # the fuzzer's workers scan once, not concurrently
            fcntl.flock(lk, fcntl.LOCK_EX)
            d = None
            if os.path.exists(cp):
                try:
                    d = json.load(open(cp))
                    if d.get('key') != key or d.get('version') != CACHE_VERSION:
                        d = None
                except (OSError, ValueError):
                    d = None
            if d is None:
                d = dict(vft=scan_exe(path), key=key, version=CACHE_VERSION)
                with open(cp + '.tmp', 'w') as f:
                    json.dump(d, f)
                os.replace(cp + '.tmp', cp)
        self.vft = {int(k): v for k, v in d['vft'].items()}
        self.by_class = collections.defaultdict(list)     # class -> primary vftables (offset 0)
        self.derived = collections.defaultdict(list)      # class -> vftables of classes deriving from it at 0
        for v, e in sorted(self.vft.items()):
            if e['off'] != 0 or not e['cls']:
                continue
            self.by_class[e['cls']].append(v)
            for b, mdisp in e['bases']:
                if mdisp == 0 and b != e['cls']:
                    self.derived[b].append(v)


def _read_group(path, sym):
    """(words, size) of the vtable group `sym` as the object at `path` defines it: plain words as ints, typeinfo
    words None, relocated slots as the target's name (an address alias `__fnv_<X>` of a definition when it has
    one), None when the symbol is missing."""
    import elfsyms
    data = open(path, 'rb').read()
    if data[:4] != b'\x7fELF' or data[4] != 1:
        return None
    shoff, = struct.unpack_from('<I', data, 0x20)
    shentsize, shnum, _ = struct.unpack_from('<HHH', data, 0x2e)
    secs = [struct.unpack_from('<IIIIIIIIII', data, shoff + i * shentsize) for i in range(shnum)]
    syms = elfsyms.symbols(path)
    s = next((x for x in syms if x.name == sym and x.shndx and x.size), None)
    if s is None or s.shndx >= len(secs):
        return None
    sec = secs[s.shndx]
    raw = data[sec[4] + s.value:sec[4] + s.value + s.size] if sec[1] != 8 else bytes(s.size)
    words = list(struct.unpack('<%dI' % (len(raw) // 4), raw[:len(raw) // 4 * 4]))
    alias = {}
    for x in syms:
        if x.shndx and ADDR_ALIAS.match(x.name):
            alias.setdefault((x.shndx, x.value), x.name)
    for rs in secs:
        if rs[1] != 4 or rs[7] != s.shndx:               # SHT_RELA of the vtable's section
            continue
        for k in range(rs[5] // 12):
            off, info, add = struct.unpack_from('<IIi', data, rs[4] + 12 * k)
            if not s.value <= off < s.value + s.size or (off - s.value) % 4:
                continue
            idx = info >> 8
            if not 0 < idx <= len(syms):
                continue
            t = syms[idx - 1]
            i = (off - s.value) // 4
            if t.name.startswith('_ZTI'):
                words[i] = None
                continue
            if t.shndx and t.type in ('section', 'func', 'notype'):
                a = alias.get((t.shndx, (t.value if t.type != 'section' else 0) + add))
                if a:
                    words[i] = a
                    continue
                if t.type == 'section' or t.bind == 'local':
                    words[i] = 0                          # a local target without an address alias
                    continue
            words[i] = t.name if not add else 0
    return words, s.size


def native_slots(words, apoff):
    """Slots of the vtable at byte offset apoff of a group: up to the next (offset-to-top, typeinfo) pair or the end."""
    i = apoff // 4
    n = 0
    while i + n < len(words):
        if words[i + n] is None:                          # the next vtable's typeinfo: its offset-to-top precedes
            return max(n - 1, 0)
        n += 1
    return n


class Model:
    """The vptr model of one link: candidates per class, x86 slot pins, native address points per unit image."""

    def __init__(self, linkdir, cache_dir, units):
        p = exe_path()
        self.exe = ExeVftables(p, cache_dir) if p else None
        self.linkdir = linkdir
        self.rows = {}                                    # V -> (vtable symbol, object, address-point offset)
        ap = os.path.join(linkdir, 'out', 'addrmap.tsv')
        if os.path.exists(ap):
            for line in open(ap, encoding='utf-8', errors='replace'):
                f = line.rstrip('\n').split('\t')
                if len(f) > 6 and f[1] == 'V' and f[2]:
                    try:
                        self.rows.setdefault(int(f[0], 16), (f[2], f[4], int(f[6] or 8)))
                    except ValueError:
                        pass
        self.decomp_vft = {}                              # decomp class name -> its primary image vftable
        for u in units:
            for n, a in u['symbols'].items():
                m = MSVC_VFT.match(n)
                if m:
                    self.decomp_vft.setdefault('::'.join(reversed([x for x in m.group(1).split('@') if x])),
                                               int(a, 16))
        self._groups = {}
        self._alias = None
        self.short = set()                                # V paired with a native vtable shorter than the exe's

    def candidates(self, cls):
        """Image vftables to plant in an object of static class `cls`: its own primary vftable (the decomp's name
        for it, else the exe's RTTI class), then up to MAX_DERIVED classes derived from it at offset 0 (spread
        over the hierarchy); only those the link pairs with a native vtable."""
        if self.exe is None or not cls:
            return []
        own = []
        v0 = self.decomp_vft.get(cls)
        if v0 is not None and v0 in self.exe.vft:
            own.append(v0)
        rcls = self.exe.vft[v0]['cls'] if own else cls
        own += [v for v in self.exe.by_class.get(rcls or cls, []) if v not in own]
        own = [v for v in own if v in self.rows][:1]
        der = [v for v in self.exe.derived.get(rcls or cls, []) if v in self.rows and v not in own]
        if len(der) > MAX_DERIVED:
            step = len(der) / MAX_DERIVED
            der = [der[int(i * step)] for i in range(MAX_DERIVED)]
        return own + der

    def for_function(self, addr):
        """Primary vftables (paired) whose slots hold `addr`: the classes a virtual member function belongs to, for a
        `this` whose decomp class (often a local shell such as FUN_004948c0_shell) names no vftable."""
        if self.exe is None:
            return []
        if getattr(self, '_by_slot', None) is None:
            self._by_slot = collections.defaultdict(list)
            for v, e in sorted(self.exe.vft.items()):
                if e['off'] == 0 and v in self.rows:
                    for a in set(e['slots']):
                        self._by_slot[a].append(v)
        got = self._by_slot.get(addr, [])
        if len(got) > MAX_DERIVED + 1:
            step = len(got) / (MAX_DERIVED + 1)
            got = [got[int(i * step)] for i in range(MAX_DERIVED + 1)]
        return got

    def slots(self, v):
        e = self.exe.vft.get(v) if self.exe else None
        return e['slots'] if e else []

    def x86_pins(self, vs):
        """{V: slot words} pinned in the shared world (the oracle reads its slots there)."""
        return {v: struct.pack('<%dI' % len(self.slots(v)), *self.slots(v)) for v in vs if self.slots(v)}

    def group(self, sym, obj):
        key = (sym, obj)
        if key not in self._groups:
            got = None
            path = os.path.join(self.linkdir, 'fixed', obj)
            if os.path.exists(path):
                try:
                    got = _read_group(path, sym)
                except (ValueError, struct.error, IndexError):
                    got = None
            self._groups[key] = got
        return self._groups[key]

    def aliases(self):
        if self._alias is None:
            import a64image
            _, self._alias = a64image.load_aliases(os.path.join(self.linkdir, 'out', 'aliases.ld'))
        return self._alias

    def native(self, vs, image, stub_orig):
        """Native address points of the vftables `vs` in this unit image -> (ap {V: native address},
        memory {address: bytes} of the synthesized groups, extra named stubs {address: name}); stub_orig gets the
        original address of every fresh stub that names one."""
        st = getattr(image, '_vt_state', None)
        if st is None:      # per unit image: one layout of synthesized groups and fresh stubs for all its targets
            st = image._vt_state = dict(mem={}, stubs={}, done={}, cur=VT_BASE, nstub=[0],
                                        by_name={n: a for a, n in image.stubs.items()}, orig={})
        ap, mem, stubs, done, by_name, nstub = {}, st['mem'], st['stubs'], st['done'], st['by_name'], st['nstub']
        alias = self.aliases()
        cur = st['cur']

        def resolve(name):
            for _ in range(3):
                if name in image.syms:
                    return image.syms[name]
                if name in by_name:
                    return by_name[name]
                m = ADDR_ALIAS.match(name)
                if m:
                    return 0x08000000 + int(m.group(1), 16)        # a64image.CODE_STUB + X
                if name in alias:
                    name = alias[name]
                    continue
                break
            a = VT_STUBS + 16 * nstub[0]
            nstub[0] += 1
            stubs[a] = name
            by_name[name] = a
            m = ADDR_ANY.match(name)
            if m:
                st['orig'][a] = int(m.group(1), 16)
            return a

        for v in vs:
            row = self.rows.get(v)
            if row is None:
                continue
            sym, obj, apoff = row
            if v in done:
                ap[v] = done[v]
                continue
            g = self.group(sym, obj)
            if g is None:
                continue
            words = g[0]
            n = native_slots(words, apoff)
            want = len(self.slots(v)) + 2          # Itanium: one destructor slot more than MSVC, a spare
            # the vtable at the address point, then (when the link pairs V with a shorter native vtable, e.g. a
            # decomp shell class with only its destructor beside FOPipboyManager's 21 slots) padding slots: each a
            # named stub __s13_vtpad_<V>_<k> that classify.py reports as vptr-unplanted pairing (S2c), never a read
            # past the group
            body = words[:apoff // 4 + n]
            pad = max(0, want - n)
            if cur + 4 * (len(body) + pad) > VT_END:
                break
            b = bytearray()
            for w in body:
                b += struct.pack('<I', (resolve(w) if isinstance(w, str) else (w or 0)) & 0xffffffff)
            for k in range(n, n + pad):
                b += struct.pack('<I', resolve('__s13_vtpad_%08x_%d' % (v, k)))
            if pad:
                self.short.add(v)
            mem[cur] = bytes(b)
            done[v] = ap[v] = cur + apoff
            cur = (cur + len(b) + 15) & ~15
        st['cur'] = cur
        stub_orig.update(st['orig'])
        return ap, dict(mem), dict(stubs)


ARGCLASS = re.compile(r'^(?:const\s+)?(?:class|struct)\s+([A-Za-z_][\w:]*)\s*(?:const\s*)?[*&]\s*(?:const)?$')


def arg_classes(conv):
    """{label: class} of `this` and the parameters that point at / refer to a class (from the decorated name)."""
    out = {}
    sig = getattr(conv, 'sig', None)
    if sig is not None and getattr(sig, 'member', False) and len(getattr(sig, 'qual', ())) >= 2:
        out['this'] = '::'.join(sig.qual[:-1])
    for v in conv.vals:
        if v.label == 'this':
            continue
        m = ARGCLASS.match((getattr(v, 'raw', '') or '').strip())
        if m and v.is_ptr:
            out[v.label] = m.group(1)
    return out
