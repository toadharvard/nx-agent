"""Self-consistency checks of the synthetic data image (stream S14).

There is no exe to compare with, so the image is checked against itself, the decomp's names (units.json) and S2's
address map (out/addrmap.tsv of a link run):

  pointers   every pointer word the image holds (a resolved DIR32/DIR32NB relocation of the item whose value the
             word holds) targets a defined image byte or a code address the address map knows
  vftables   every vftable slot points to a function whose calling convention and arity fit the method the slot
             declares (the relocation's symbol in the object that emitted the vftable)
  rtti       vftable -> Complete Object Locator -> TypeDescriptor / ClassHierarchyDescriptor -> BaseClassArray ->
             BaseClassDescriptors, in each object and across objects, and the TypeDescriptors placed in the image
  overlaps   no two items claim one byte with different contents (every conflict is classified)
  strings    $SG literals (and ??_C@ pool literals) hold their terminator inside their own bytes, the image holds
             it, and no other item claims their bytes with other contents

Every check returns {kind: count}, a few examples per kind, and the list of violations. KINDS says which kinds
are violations and who owns them; the other kinds classify what passed or could not be checked.
"""
import bisect
import collections
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'link'))
import coffobj  # noqa: E402

WIN_LO, WIN_HI = 0x00fdf000, 0x02100000
TEXT_LO, TEXT_HI = 0x00401000, 0x00fdf000
IMAGE_BASE = 0x00400000
ST_NONE, ST_SOFT, ST_UNK, ST_HARD = 0, 1, 2, 3

S14 = 'S14'                       # this stream: the synthesis itself
DECOMP = 'decomp (S1b/S11)'       # units.json binds a name to an address the object's content contradicts
VSLOT = 'S2b/S2c (slot binding, ABI)'
NOBODY = 'none (x86 only)'

# kind -> (violation?, owner, meaning)
KINDS = {
    # pointers
    'ptr:data-known': (False, None, 'target byte known'),
    'ptr:data-claimed': (False, None, 'target claimed by an item, value unknown (unresolved relocation/contested)'),
    'ptr:data-bss': (False, None, 'target in .bss (zero by section)'),
    'ptr:data-end': (False, None, 'one past the end of the item the relocation names'),
    'ptr:code-function': (False, None, 'function start in the address map (F/C/X/G)'),
    'ptr:code-extern': (False, None, '.text address the map knows without native definition (E)'),
    'ptr:tls-offset': (False, None, 'SECREL offset inside .tls'),
    'ptr:skip-unresolved': (False, None, 'relocation without an address: the word stays unknown (coverage)'),
    'ptr:skip-soft': (False, None, 'relocation in the soft tail of an item (beyond the bytes it owns)'),
    'ptr:skip-other-item': (False, None, 'the word holds another item\'s value (that item is checked)'),
    'ptr:skip-word-unknown': (False, None, 'part of the word unknown (contested)'),
    'ptr:data-undefined': (True, 'exe/CRT (no object defines it)', 'target in a data section, no item defines it'),
    'ptr:data-no-section': (True, S14, 'target in the window but outside every inferred section'),
    'ptr:code-eh-label': (True, NOBODY, 'EH table entry -> label inside a function (funclet/catch block)'),
    'ptr:code-not-linked': (True, 'S1 (unit not in the link)', 'function start units.json defines, absent from '
                                                               'the address map'),
    'ptr:code-inside-function': (True, 'S2c (address map)', 'inside a function the map knows, not a start'),
    'ptr:code-unmapped': (True, 'S2c (address map)', '.text address the map does not know'),
    'ptr:tls-offset-out': (True, S14, 'SECREL offset outside .tls'),
    'ptr:outside': (True, S14, 'neither image data nor .text'),
    # vftable slots
    'slot:fits': (False, None, 'convention and arity of the definition fit the slot'),
    'slot:same-name': (False, None, 'the definition at the target is the slot\'s own symbol'),
    'slot:fastcall-member-form': (False, None, '__fastcall f(T*, int edx, ...) definition: fits a __thiscall slot'),
    'slot:ignores-this': (False, None, 'free __stdcall definition with the slot\'s parameters, or __cdecl without '
                                       'any: x86-compatible (ecx unused); natively a bridge that drops `this`'),
    'slot:purecall': (False, None, '_purecall'),
    'slot:extern': (False, None, 'target is a .text address without definition (E): not checkable'),
    'slot:uncheckable': (False, None, 'a name undname cannot parse, special or untyped (C) definition'),
    'slot:unresolved': (False, None, 'slot word unknown (no address for the slot\'s symbol)'),
    'slot:convention': (True, VSLOT + ', ' + DECOMP, 'definition has another calling convention than the slot'),
    'slot:arity': (True, VSLOT + ', ' + DECOMP, 'definition takes another number of parameters than the slot'),
    'slot:not-function': (True, DECOMP, 'target is not a function start'),
    # rtti
    'rtti:col-ok': (False, None, 'vftable -> COL -> TD/CHD chain consistent in its object'),
    'rtti:no-col': (False, None, 'vftable without COL word (class compiled without RTTI)'),
    'rtti:col-unplaced': (False, None, 'COL has no address: the image keeps the vftable\'s [-1] word unknown'),
    'rtti:col-word-other-item': (True, DECOMP + ', S3 (hints)', 'the vftable\'s [-1] word holds the preceding '
                                 'item\'s bytes: no COL there in the original (class built without C++ RTTI, the '
                                 'decomp\'s /GR adds one) or the ??_7 address is off'),
    'rtti:td-ok': (False, None, 'placed TypeDescriptor: type_info vptr, spare 0, its own name'),
    'rtti:td-vptr-unresolved': (False, None, 'placed TypeDescriptor whose vptr word is unknown'),
    'rtti:col-signature': (True, S14, 'COL signature is not 0 (x86)'),
    'rtti:col-td-class': (True, DECOMP, 'COL\'s TypeDescriptor names another class than the vftable'),
    'rtti:col-chd-class': (True, DECOMP, 'COL\'s ClassHierarchyDescriptor belongs to another class'),
    'rtti:chd-broken': (True, S14, 'CHD/BCA/BCD missing, signature not 0, count != array, BCD[0] not the class'),
    'rtti:col-disagree-units': (True, DECOMP, 'one COL name, other offsets in different objects (layout)'),
    'rtti:col-struct-key': (True, DECOMP, 'one COL, its TypeDescriptor .?AV in some objects and .?AU in others'),
    'rtti:chd-disagree-units': (True, DECOMP, 'one class hierarchy, declared differently in different objects'),
    'rtti:td-image-spelling': (True, DECOMP, 'the image holds another unit\'s spelling of a placed TD name'),
    'rtti:td-broken': (True, S14, 'placed TD: vptr not type_info, spare not 0, name not its own or unterminated'),
    # overlaps (conflicts between distinct items)
    'overlap:td-class-struct-key': (True, DECOMP, 'TD .?AV vs .?AU of one class (declared class and struct)'),
    'overlap:td-different-class': (True, DECOMP, 'TDs of different classes at one address'),
    'overlap:real-different-value': (True, DECOMP, 'different __real@ constants at one address'),
    'overlap:literal-different-content': (True, DECOMP, 'different ??_C@ literals at one address'),
    'overlap:literal-overrun': (True, DECOMP, 'a literal runs into the next item (the unit\'s string is longer)'),
    'overlap:vftable-different-content': (True, DECOMP, 'vftables at one address with other slots'),
    'overlap:vftable-overrun': (True, DECOMP, 'a vftable runs into the next item (more virtuals than the original)'),
    'overlap:aggregate-vs-member': (True, DECOMP, 'a stand-in aggregate\'s bytes vs a variable inside it'),
    'overlap:sg': (True, S14, '$SG literal vs another item'),
    'overlap:var': (True, DECOMP, 'variables of different units with other initial values at one address'),
    'overlap:bss-vs-initialised': (True, DECOMP, 'a .bss (zero) variable over another item\'s non-zero bytes: the '
                                                 'image keeps the initialised bytes'),
    'overlap:other': (True, S14, 'other conflicting pair'),
    # strings
    'str:sg-terminated': (False, None, '$SG ends with its terminator inside its own bytes'),
    'str:pool-terminated': (False, None, '??_C@ literal ends with its terminator inside its own bytes'),
    'str:sg-shared': (False, None, '$SG terminated, its bytes (partly) provided by an identical item'),
    'str:pool-shared': (False, None, '??_C@ literal terminated, its bytes (partly) provided by an identical item'),
    'str:sg-nul-outside': (True, S14, '$SG terminator outside the bytes the item owns (left soft)'),
    'str:sg-nul-overwritten': (True, S14, 'the image holds no NUL where the $SG ends'),
    'str:sg-no-nul': (True, S14, '$SG data has no terminator at all'),
    'str:sg-overlap': (True, S14, 'another item provides a $SG byte with another value'),
    'str:pool-nul-outside': (True, S14, '??_C@ terminator outside the bytes the item owns'),
    'str:pool-nul-overwritten': (True, DECOMP, 'the image holds no NUL where the ??_C@ literal ends (another '
                                               'item\'s claim, see overlaps)'),
    'str:pool-overlap': (True, DECOMP, 'another item provides a ??_C@ byte with another value'),
}


def is_violation(kind):
    return KINDS[kind][0]


class Result:
    def __init__(self, name):
        self.name = name
        self.counts = collections.Counter()
        self.examples = collections.defaultdict(list)
        self.violations = []

    def add(self, kind, where, detail=''):
        assert kind in KINDS, kind
        self.counts[kind] += 1
        if len(self.examples[kind]) < 8:
            self.examples[kind].append('%s %s' % (where, detail) if detail else where)
        if is_violation(kind):
            self.violations.append((kind, where, detail))

    def summary(self):
        return dict(counts=dict(sorted(self.counts.items())),
                    violations={k: n for k, n in sorted(self.counts.items()) if is_violation(k)},
                    examples={k: v for k, v in sorted(self.examples.items())})


def load_addrmap(path):
    """S2's addrmap.tsv: address -> kind (F C X G V E)"""
    out = {}
    with open(path) as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            p = line.split('\t', 2)
            try:
                out[int(p[0], 16)] = p[1]
            except (ValueError, IndexError):
                continue
    return out


def h(a):
    return '0x%08x' % a


class Funcs:
    """function extents from units.json (names.funcs: start -> (unit, symbol, size))"""

    def __init__(self, funcs):
        self.funcs = funcs
        self.starts = sorted(funcs)

    def containing(self, a):
        k = bisect.bisect_right(self.starts, a) - 1
        if k < 0:
            return None
        s = self.starts[k]
        return s if a < s + max(1, self.funcs[s][2]) else None


# ----------------------------------------------------------------------------------------------------- pointers

def check_pointers(img, distinct, sec, names, codemap):
    """every pointer word the image holds"""
    r = Result('pointers')
    funcs = Funcs(names.funcs)
    st = img.st
    tls = sec.get('tls')
    sections = [sec['rdata'], sec['data']] + ([tls] if tls else [])
    bss = (sec['bss_lo_evidence'], sec['data'][1])
    by_name = {}
    for it in distinct:
        by_name.setdefault(it.name, it)
    for it in distinct:
        if it.fam == 'bss' or it.addr < WIN_LO or it.end > WIN_HI:
            continue
        for o, typ, tn, toff, point in it.relocs:
            if typ not in (coffobj.REL_DIR32, coffobj.REL_DIR32NB, coffobj.REL_SECREL):
                continue
            va = it.addr + o
            where = '%s %s+%d' % (h(va), it.name[:60], o)
            if point is None:
                r.add('ptr:skip-unresolved', where, tn)
                continue
            if o >= it.hard:
                r.add('ptr:skip-soft', where)
                continue
            if img.win[va - WIN_LO] != it.idx:
                r.add('ptr:skip-other-item', where)      # the word holds another item's value (see overlaps)
                continue
            v = img.word(va)
            if v is None:
                r.add('ptr:skip-word-unknown', where)
                continue
            if typ == coffobj.REL_SECREL:
                if tls and 0 <= v < tls[1] - tls[0]:
                    r.add('ptr:tls-offset', where)
                else:
                    r.add('ptr:tls-offset-out', where, '-> %d' % v)
                continue
            if typ == coffobj.REL_DIR32NB:
                v = (v + IMAGE_BASE) & 0xffffffff
            if WIN_LO <= v < WIN_HI:
                s = st[v - WIN_LO]
                if not any(lo <= v < hi for lo, hi in sections):
                    r.add('ptr:data-no-section', where, '-> %s %s' % (h(v), tn))
                elif s == ST_HARD:
                    r.add('ptr:data-known', where)
                elif s in (ST_UNK, ST_SOFT):
                    r.add('ptr:data-claimed', where)
                elif bss[0] <= v < bss[1]:
                    r.add('ptr:data-bss', where)
                else:
                    t = by_name.get(tn)
                    if t is not None and toff > 0 and v in (t.addr + t.hard, t.addr + len(t.data)):
                        r.add('ptr:data-end', where)
                    else:
                        r.add('ptr:data-undefined', where, '-> %s %s' % (h(v), tn))
            elif TEXT_LO <= v < TEXT_HI:
                k = codemap.get(v)
                if k in ('F', 'C', 'X', 'G'):
                    r.add('ptr:code-function', where)
                elif k == 'E':
                    r.add('ptr:code-extern', where)
                elif v in names.funcs:
                    r.add('ptr:code-not-linked', where, '-> %s %s (%s)' % (
                        h(v), names.funcs[v][1][:50], names.sources[names.funcs[v][0]]))
                elif funcs.containing(v) is not None:
                    if it.kind == 'eh' or it.fam == 'eh':
                        r.add('ptr:code-eh-label', where, '-> %s %s' % (h(v), tn))
                    else:
                        r.add('ptr:code-inside-function', where, '-> %s %s' % (h(v), tn))
                else:
                    r.add('ptr:code-unmapped', where, '-> %s %s' % (h(v), tn))
            else:
                r.add('ptr:outside', where, '-> %s %s' % (h(v), tn))
    return r


# ----------------------------------------------------------------------------------------------------- vftables

UNDNAME = os.environ.get('UNDNAME') or 'llvm-undname'


def demangle(names_list):
    """{mangled: demangled or None} through llvm-undname (echoes each input, then the result)"""
    names_list = sorted(set(n for n in names_list if n and n.startswith('?')))
    res = {}
    if not names_list:
        return res
    try:
        p = subprocess.run([UNDNAME], input='\n'.join(names_list) + '\n', capture_output=True, text=True,
                           errors='surrogateescape')
    except OSError:
        return res
    out = p.stdout.split('\n')
    pos = 0
    for m in names_list:
        j = next((k for k in range(pos, min(pos + 64, len(out) - 1)) if out[k] == m), None)
        if j is None:
            continue
        d = out[j + 1]
        res[m] = None if d.startswith('error:') else d
        pos = j + 2
    return res


def slot_words(it):
    """(item offset, relocation) of a vftable item's slots: consecutive relocated words from its symbol"""
    rel = {o: x for x in it.relocs for o in (x[0],)}
    o = it.soff
    out = []
    while o + 4 <= len(it.data) and o in rel:
        out.append((o, rel[o]))
        o += 4
    return out


def fit(slot, defn):
    """kind for a slot signature vs the definition's (sigs.Sig)"""
    if slot.kind != 'func' or defn.kind != 'func' or slot.params is None or defn.params is None:
        return 'slot:uncheckable'
    sp = list(slot.params)
    dp = list(defn.params)
    scc, dcc = slot.cc, defn.cc
    if not defn.member:                         # free function: `this` is an explicit first parameter
        if dcc == 'fastcall' and scc == 'thiscall' and len(dp) >= 2:
            rest = dp[2:]
            if '...' in sp or '...' in rest or len(rest) == len(sp):
                return 'slot:fastcall-member-form'
            return 'slot:arity'
        if scc == 'thiscall' and '...' not in sp and '...' not in dp and len(dp) == len(sp) and \
                (dcc == 'stdcall' or (dcc == 'cdecl' and not sp)):
            return 'slot:ignores-this'          # callee pops the same bytes and never reads ecx
        if dcc != scc or scc == 'thiscall':
            return 'slot:convention'
        dp = dp[1:]
    elif dcc != scc:
        return 'slot:convention'
    if '...' in sp or '...' in dp:
        return 'slot:fits'
    return 'slot:fits' if len(sp) == len(dp) else 'slot:arity'


def check_vftables(img, distinct, names, codemap):
    import sigs
    r = Result('vftables')
    funcs = Funcs(names.funcs)
    todo = []
    for it in distinct:
        if it.kind != 'vftable' or it.fam == 'bss' or it.addr < WIN_LO or it.end > WIN_HI:
            continue
        for o, (o2, typ, tn, toff, point) in slot_words(it):
            va = it.addr + o
            w = img.win[va - WIN_LO]
            if w != it.idx and not (point is None and w < 0):
                continue                    # another item's value (see overlaps)
            todo.append((it, o, tn, point))
    want = set()
    for it, o, tn, point in todo:
        want.add(tn)
        if point is not None and point in names.funcs:
            want.add(names.funcs[point][1])
            want.update(names.defnames.get(point, ()))
    dem = demangle(want)
    parsed = {}

    def sig(n):
        if n not in parsed:
            parsed[n] = sigs.parse_msvc(n, dem.get(n)) if n.startswith('?') else sigs.parse_msvc_c(n)
        return parsed[n]

    for it, o, tn, point in todo:
        where = '%s %s[%d]' % (h(it.addr + o), it.name[:50], (o - it.soff) // 4)
        if point is None:
            r.add('slot:unresolved', where, tn)
            continue
        if tn in ('__purecall', '_purecall'):
            r.add('slot:purecall', where)
            continue
        d = names.funcs.get(point)
        if d is None:
            k = codemap.get(point)
            if k == 'E':
                r.add('slot:extern', where, '%s -> %s' % (tn, h(point)))
            elif funcs.containing(point) is not None:
                r.add('slot:not-function', where, '%s -> %s inside %s' % (tn, h(point), h(funcs.containing(point))))
            else:
                r.add('slot:not-function', where, '%s -> %s' % (tn, h(point)))
            continue
        dname = d[1]
        if tn in names.defnames.get(point, ()):
            r.add('slot:same-name', where)       # the slot's symbol is defined there (or folded into it)
            continue
        best = None
        for dn in sorted(names.defnames.get(point, {dname})):     # every definition at the target: best fit
            k = fit(sig(tn), sig(dn))
            if best is None or FIT_ORDER.index(k) < FIT_ORDER.index(best[0]):
                best = (k, dn)
        k, dn = best
        r.add(k, where, '' if k in ('slot:fits', 'slot:uncheckable') else '%s -> %s %s' % (tn, h(point), dn))
    return r


FIT_ORDER = ('slot:fits', 'slot:fastcall-member-form', 'slot:ignores-this', 'slot:uncheckable', 'slot:arity',
             'slot:convention')


# ----------------------------------------------------------------------------------------------------- RTTI

def td_qual(tdname):
    """'??_R0?AVTESForm@@@8' -> ('V', 'TESForm@@')"""
    if tdname and tdname.startswith('??_R0?A') and tdname.endswith('@8') and len(tdname) > 10:
        return tdname[7], tdname[8:-2]
    return None, None


def u32(it, o):
    return struct.unpack_from('<I', it.data, o)[0] if o + 4 <= len(it.data) else None


def rel_at(it, o):
    for x in it.relocs:
        if x[0] == o:
            return x
    return None


def check_rtti(img, distinct, every_item, names):
    """every_item: placed and unplaced items of all objects (phase 1 items + unplaced)"""
    r = Result('rtti')
    r.colless = []         # (vftable VA, its name, the item holding its [-1] word, that item's unit)
    by_unit = {}
    for it in every_item:
        if it.name.startswith('??_R'):
            by_unit[(it.unit, it.name)] = it
    # cross-object agreement of COLs and CHDs
    seen_col = {}
    seen_chd = {}
    for (u, n), it in by_unit.items():
        if n.startswith('??_R4'):
            key = (u32(it, 0), u32(it, 4), u32(it, 8), (rel_at(it, 12) or (0, 0, None))[2],
                   (rel_at(it, 16) or (0, 0, None))[2])
            seen_col.setdefault(n, {}).setdefault(key, []).append(u)
        elif n.startswith('??_R3'):
            bca = (rel_at(it, 12) or (0, 0, None))[2]
            arr = by_unit.get((u, bca))
            bases = tuple(x[2] for x in sorted(arr.relocs)) if arr else None
            key = (u32(it, 4), u32(it, 8), bases)
            seen_chd.setdefault(n, {}).setdefault(key, []).append(u)
    for n, ks in sorted(seen_col.items()):
        if len(ks) > 1:
            unkeyed = {k[:3] + (td_qual(k[3])[1], k[4]) for k in ks}
            kind = 'rtti:col-struct-key' if len(unkeyed) == 1 else 'rtti:col-disagree-units'
            r.add(kind, n, ' / '.join('u%s' % v[:3] for v in ks.values()))
    for n, ks in sorted(seen_chd.items()):
        if len(ks) > 1:
            r.add('rtti:chd-disagree-units', n, ' / '.join('u%s' % v[:3] for v in ks.values()))
    # vftable -> COL chains (in the vftable's own object)
    for it in distinct:
        if it.kind != 'vftable' or it.fam == 'bss':
            continue
        where = '%s %s' % (h(it.addr + it.soff), it.name[:60])
        col_rel = rel_at(it, it.soff - 4) if it.soff >= 4 else None
        if col_rel is None or not (col_rel[2] or '').startswith('??_R4'):
            r.add('rtti:no-col', where)
            continue
        cw = it.addr + it.soff - 4 - WIN_LO
        if img.st[cw] != ST_HARD:
            r.add('rtti:col-unplaced', where)
        elif img.win[cw] != it.idx:
            o2 = distinct[img.win[cw]]
            r.add('rtti:col-word-other-item', where, '%s %s (u%d) holds 0x%08x' % (
                h(o2.addr), o2.name[:50], o2.unit, img.word(cw + WIN_LO) or 0))
            r.colless.append((it.addr + it.soff, it.name, o2.name, o2.unit))
        col = by_unit.get((it.unit, col_rel[2]))
        if col is None:
            r.add('rtti:chd-broken', where, 'COL %s not in the object' % col_rel[2])
            continue
        if u32(col, 0) != 0:
            r.add('rtti:col-signature', where, '%s: %s' % (col.name, u32(col, 0)))
            continue
        td = rel_at(col, 12)
        chd = rel_at(col, 16)
        key, q = td_qual(td[2] if td else None)
        if q is None:
            r.add('rtti:chd-broken', where, 'COL without TD')
            continue
        if not it.name.startswith('??_7' + q + '6B'):
            r.add('rtti:col-td-class', where, td[2])
            continue
        if chd is None or chd[2] != '??_R3' + q + '8':
            r.add('rtti:col-chd-class', where, chd[2] if chd else '-')
            continue
        c = by_unit.get((it.unit, chd[2]))
        bad = None
        if c is None or u32(c, 0) != 0:
            bad = 'CHD missing or signature'
        else:
            n = u32(c, 8)
            bca_rel = rel_at(c, 12)
            arr = by_unit.get((it.unit, bca_rel[2])) if bca_rel else None
            if arr is None:
                bad = 'BCA missing'
            else:
                bcds = [x[2] for x in sorted(arr.relocs)]
                if len(bcds) < n or not n:
                    bad = 'BCA holds %d of %d' % (len(bcds), n or 0)
                else:
                    b0 = by_unit.get((it.unit, bcds[0]))
                    if b0 is None or (rel_at(b0, 0) or (0, 0, None))[2] != td[2]:
                        bad = 'BCD[0] is not the class'
                    elif u32(b0, 4) != n - 1:
                        bad = 'BCD[0] contains %s, CHD counts %d' % (u32(b0, 4), n)
                    else:
                        for bn in bcds[:n]:
                            b = by_unit.get((it.unit, bn))
                            if b is None or not (rel_at(b, 0) or (0, 0, ''))[2].startswith('??_R0'):
                                bad = 'BCD %s without TD' % bn
                                break
        if bad:
            r.add('rtti:chd-broken', where, bad)
        else:
            r.add('rtti:col-ok', where)
    # TypeDescriptors placed in the image
    for it in distinct:
        if not it.name.startswith('??_R0') or it.fam == 'bss' or it.addr < WIN_LO or it.end > WIN_HI:
            continue
        o = it.addr - WIN_LO
        where = '%s %s' % (h(it.addr), it.name[:60])
        want = ('.' + it.name[5:-2]).encode('latin-1') + b'\0'
        vp = rel_at(it, 0)
        if vp is None or vp[2] != '??_7type_info@@6B@' or u32(it, 4) != 0 or it.data[8:8 + len(want)] != want:
            r.add('rtti:td-broken', where, vp[2] if vp else 'no vptr')
            continue
        have = bytes(img.val[o + 8:o + 8 + len(want)])
        if any(s != ST_HARD for s in img.st[o + 8:o + 8 + len(want)]) or have != want:
            if img.win[o + 8] != it.idx or any(img.win[o + 8 + k] != it.idx for k in range(len(want))):
                r.add('rtti:td-image-spelling', where, have.rstrip(b'\0').decode('latin-1', 'replace'))
            else:
                r.add('rtti:td-broken', where, 'image bytes')
            continue
        r.add('rtti:td-vptr-unresolved' if img.word(it.addr) is None else 'rtti:td-ok', where)
    return r


# ----------------------------------------------------------------------------------------------------- overlaps

def classify_overlap(a, b):
    na, nb = a.name, b.name
    if na.startswith('$SG') or nb.startswith('$SG'):
        return 'overlap:sg'
    if na.startswith('??_R0') and nb.startswith('??_R0'):
        ka, qa = td_qual(na)
        kb, qb = td_qual(nb)
        if qa == qb and qa is not None:
            return 'overlap:td-class-struct-key'
        return 'overlap:td-different-class'
    if a.kind == 'real' and b.kind == 'real' and a.addr == b.addr:
        return 'overlap:real-different-value'
    if a.kind == 'literal' and b.kind == 'literal' and a.addr == b.addr:
        return 'overlap:literal-different-content'
    if a.kind == 'vftable' and b.kind == 'vftable' and a.addr + a.soff == b.addr + b.soff:
        return 'overlap:vftable-different-content'
    first = a if (a.addr, -a.hard) <= (b.addr, -b.hard) else b
    other = b if first is a else a
    if first.kind == 'literal' or (first.kind == 'real' and other.addr != first.addr):
        return 'overlap:literal-overrun'
    if first.kind == 'vftable':
        return 'overlap:vftable-overrun'
    if first.kind == 'var' and first.hard >= 4 * max(1, other.hard) and other.addr > first.addr:
        return 'overlap:aggregate-vs-member'
    if a.kind == 'var' and b.kind == 'var':
        return 'overlap:var'
    return 'overlap:other'


def check_overlaps(conflicts, bss_conflicts=()):
    r = Result('overlaps')
    outcome = collections.Counter()
    for a, b, zero, nz in bss_conflicts:
        z, other = (a, b) if a.fam == 'bss' else (b, a)
        outcome['bss-vs-initialised/initialised-kept'] += 1
        r.add('overlap:bss-vs-initialised', '%s %s (u%d, .bss)' % (h(z.addr), z.name[:50], z.unit),
              'vs %s %s (u%d): %d non-zero bytes' % (h(other.addr), other.name[:50], other.unit, nz))
    for a, b, agree, diff in conflicts:
        k = classify_overlap(a, b)
        if a.rank != b.rank:
            how = 'rank'
        elif a.copies != b.copies:
            how = 'majority'
        else:
            how = 'contested'
        outcome['%s/%s' % (k.split(':')[1], how)] += 1
        r.add(k, '%s %s (u%d, rank %s, %d units)' % (h(a.addr), a.name[:50], a.unit, a.rank, a.copies),
              'vs %s %s (u%d, rank %s, %d units): %d bytes differ, %s' % (h(b.addr), b.name[:50], b.unit, b.rank,
                                                                        b.copies, diff, how))
    res = r
    res.outcome = outcome
    return res


# ----------------------------------------------------------------------------------------------------- strings

def terminator(data, start):
    """offset just past the terminator of the string at start (narrow NUL, or NUL pair of UTF-16 text), or None"""
    end = data.find(b'\0', start)
    if end < 0:
        return None
    # UTF-16 text: the first NUL is the high byte of the first character; the terminator is an aligned NUL pair
    if end == start + 1 and len(data) - start >= 4 and data[start + 2] != 0 and data[start + 3] == 0:
        k = start
        while k + 1 < len(data):
            if data[k] == 0 and data[k + 1] == 0:
                return k + 2
            k += 2
        return None
    return end + 1


def check_strings(img, distinct):
    r = Result('strings')
    for it in distinct:
        if it.kind != 'literal' or it.fam == 'bss' or it.addr < WIN_LO or it.end > WIN_HI:
            continue
        sg = it.name.startswith('$SG')
        p = 'str:sg-' if sg else 'str:pool-'
        o = it.addr - WIN_LO
        where = '%s %s (u%d)' % (h(it.addr), it.name[:50], it.unit)
        end = terminator(it.data, it.soff)
        if end is None:
            r.add('str:sg-no-nul' if sg else 'str:pool-nul-outside', where)
            continue
        if end > it.hard:
            r.add(p + 'nul-outside', where, 'string ends at +%d, item owns %d' % (end, it.hard))
            continue
        lost = [k for k in range(it.soff, end) if img.st[o + k] != ST_HARD or img.val[o + k] != it.data[k]]
        if lost:
            if any(img.st[o + k] == ST_HARD for k in lost):
                r.add(p + 'overlap', where, '%d bytes from another item' % len(lost))
            elif it.data[end - 1] == 0 and (end - 1) in lost:
                r.add(p + 'nul-overwritten', where, '%d bytes unknown (contested)' % len(lost))
            else:
                r.add(p + 'overlap', where, '%d bytes unknown (contested)' % len(lost))
            continue
        if all(img.win[o + k] == it.idx for k in range(it.soff, end)):
            r.add(p + 'terminated', where)
        else:
            r.add(p + 'shared', where)
    return r


# ----------------------------------------------------------------------------------------------------- driver

def run_all(img, distinct, every_item, sec, names, conflicts, codemap):
    res = [check_pointers(img, distinct, sec, names, codemap), check_vftables(img, distinct, names, codemap),
           check_rtti(img, distinct, every_item, names),
           check_overlaps(conflicts, getattr(img, 'bss_conflicts', ())), check_strings(img, distinct)]
    out = {x.name: x.summary() for x in res}
    out['overlaps']['outcome'] = dict(sorted(res[3].outcome.items()))
    by_kind = collections.Counter()
    for x in res:
        for k, n in x.counts.items():
            if is_violation(k):
                by_kind[k] += n
    out['violations_by_kind'] = {k: dict(count=n, owner=KINDS[k][1], meaning=KINDS[k][2])
                                 for k, n in sorted(by_kind.items())}
    out['violations_by_owner'] = dict(collections.Counter(
        KINDS[k][1] for x in res for k, _, _ in x.violations))
    return out, res


def write_hints(path, results):
    """S3 vftable hints (loader/host/loadcheck --hints, fnvnx_scan_opts.vtable_hints): the vftables whose [-1]
    word the image gives to the preceding item, i.e. classes without a COL in the original (built without C++
    RTTI), which S3's COL search cannot find. One original VA per line, sorted; '#' lines name the evidence.
    Decomp-derived: local only. Confirm on the player's exe with loader/tools/check_hints.py."""
    rows = {}
    for x in results:
        for va, name, holder, unit in getattr(x, 'colless', ()):
            rows.setdefault(va, []).append('%s ([-1] is the end of %s, u%d)' % (name, holder, unit))
    with open(path, 'w') as f:
        f.write('# fnvnx vftable hints: COL-less vftables (synth_checks rtti:col-word-other-item), %d\n' % len(rows))
        for va in sorted(rows):
            for why in rows[va]:
                f.write('# 0x%08x %s\n' % (va, why))
        for va in sorted(rows):
            f.write('0x%08x\n' % va)
    return len(rows)


def write_violations(path, results):
    with open(path, 'w') as f:
        f.write('# check\tkind\towner\twhere\tdetail\n')
        for x in results:
            for k, where, detail in x.violations:
                f.write('%s\t%s\t%s\t%s\t%s\n' % (x.name, k, KINDS[k][1], where, detail))
