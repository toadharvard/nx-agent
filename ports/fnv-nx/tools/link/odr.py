#!/usr/bin/env python3
"""ODR stand-ins: one class name, several unrelated definitions (S1 / orchestrator 2026-11-08).

The decomp declares unit-local stand-in classes at global scope (`struct SpawnedSub : SpawnedSubBase`,
`class SpawnedObject : public SpawnedBase`, `struct StandIn`, ...), each unit its own. GCC emits their implicit
vtables, typeinfo and inline members as COMDAT groups under the same names, the linker keeps one copy, and every
other unit's objects get that unit's vtable and destructor (00e98f20's SpawnedSub vptr, EQ). Prepare cannot rename
them: most members are declared-only and bound by name (S1).

rename(): before resolve reads the objects, a class C collides when one of its COMDAT groups (signature `_ZTV<C>`,
`_ZTI<C>`, `_ZTS<C>` or a direct member `_ZN[K]<C>...`) has different contents in two objects (section bytes, and the
relocations by offset, type, symbol name and addend), and the exe has no RTTI class named C (a real game class keeps
its name: VTFILL replaces its copies with one transform(exe) table). In every object defining a group of such a C,
C's vtable and typeinfo symbols (`_ZTV<C>`, `_ZTI<C>`, `_ZTS<C>`) are renamed `<name>.fnvu<unit hash>` (objcopy
--redefine-sym renames the definition and that object's own references: the ctor's vptr store, a derived class's
typeinfo), so each unit keeps its own vtable; members keep their names (a member placed at an exe address stays
bound by name). Identical copies of a class that does not collide stay merged. Plain global class names only (no template, no namespace). FNVNX_ODR=0
turns it off. Writes out/odr.tsv (class, objects, renamed symbols) and the renamed copies to out/odr/.
"""
import collections
import hashlib
import json
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vtables  # noqa: E402

OBJCOPY = os.environ.get('OBJCOPY', 'aarch64-linux-gnu-objcopy')
SHT_GROUP = 17
_TI = re.compile(r'^_ZT[VIS](\d+)([A-Za-z_]\w*)$')
_MEMBER = re.compile(r'^_ZNK?(\d+)([A-Za-z_]\w*)')


def class_of(sym):
    """C for C's own COMDAT symbols: `_ZTV<n>C` / `_ZTI` / `_ZTS`, `_ZN[K]<n>C` + a ctor / dtor / one member name +
    `E...`; None for templates, nested names and everything else."""
    m = _TI.match(sym)
    if m:
        n = int(m.group(1))
        return m.group(2) if len(m.group(2)) == n else None
    m = _MEMBER.match(sym)
    if not m:
        return None
    n = int(m.group(1))
    c = m.group(2)[:n]
    if len(c) != n:
        return None
    rest = sym[m.start(2) + n:]
    if re.match(r'(?:C[123]|D[012])E', rest):
        return c
    m2 = re.match(r'(\d+)', rest)
    if m2:
        k = int(m2.group(1))
        tail = rest[len(m2.group(1)) + k:]
        return c if tail.startswith('E') else None
    return None


def groups(path):
    """[(signature symbol, digest, [symbols defined in the group's sections])] of an ELF32 relocatable object."""
    o = vtables.Obj(path)
    out = []
    for gi, s in enumerate(o.secs):
        if s[1] != SHT_GROUP:
            continue
        sig = o.syms[s[7]][0] if s[7] < len(o.syms) else ''
        if class_of(sig) is None:
            continue
        words = struct.unpack_from('<%dI' % (s[5] // 4), o.data, s[4])
        members = list(words[1:])
        h = hashlib.sha1()
        for m in members:
            ms = o.secs[m]
            if ms[1] in (4, 9):   # the RELA / REL sections: read through o.rel of their target
                continue
            h.update(o.secnames[m].encode('utf-8', 'surrogateescape') + b'\0')
            if ms[1] != 8:   # SHT_NOBITS
                h.update(o.data[ms[4]:ms[4] + ms[5]])
            for off, (si, ty, add) in sorted(o.rel(m).items()):
                nm = o.syms[si][0] or ('sec:' + o.secnames[o.syms[si][5]] if o.syms[si][5] < len(o.secnames) else '?')
                h.update(('%d %d %s %d|' % (off, ty, nm, add)).encode('utf-8', 'surrogateescape'))
        mset = set(members)
        defs = sorted({nm for (nm, value, size, typ, bind, shndx) in o.syms
                       if nm and shndx in mset and typ != 3 and bind in (1, 2)})
        out.append((sig, h.hexdigest()[:16], defs))
    return out


def _cached_groups(paths, cache_path):
    cache = {}
    if cache_path and os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path))
        except (OSError, ValueError):
            cache = {}
    got, changed = {}, False
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        key = '%s|%d|%d' % (p, st.st_size, int(st.st_mtime))
        g = cache.get(key)
        if g is None:
            try:
                g = groups(p)
            except (OSError, ValueError, struct.error, IndexError):
                g = []
            cache[key] = g
            changed = True
        got[p] = g
    if changed and cache_path:
        live = {'%s|%d|%d' % (p, os.stat(p).st_size, int(os.stat(p).st_mtime)) for p in got}
        tmp = cache_path + '.tmp'
        json.dump({k: v for k, v in cache.items() if k in live}, open(tmp, 'w'))
        os.replace(tmp, cache_path)
    return got


def colliding(per_obj, exe_classes=()):
    """per_obj: {object: [(sig, digest, defs)]} -> {class: {object: [symbols to rename]}} for the classes with a
    group whose contents differ between objects and no exe RTTI class of that name."""
    dig = collections.defaultdict(set)
    for p, gs in per_obj.items():
        for sig, d, _ in gs:
            dig[sig].add(d)
    bad = {class_of(sig) for sig, ds in dig.items() if len(ds) > 1} - set(exe_classes) - {None}
    out = collections.defaultdict(dict)
    for p, gs in per_obj.items():
        for sig, d, defs in gs:
            c = class_of(sig)
            if c in bad:
                # only the vtable and typeinfo: an inline member keeps its name, so a member a unit places at an exe
                # address (and the binding by that name) is untouched; the unit's ctor stores its own vtable, which
                # W47 / VTFILL can pair with the exe vftable that unit's constructor stores
                out[c].setdefault(p, set()).update(x for x in defs if _TI.match(x))
    return {c: {p: sorted(v) for p, v in m.items() if v} for c, m in out.items() if any(m.values())}


def suffix(source):
    return '.fnvu' + hashlib.sha1(source.encode('utf-8', 'surrogateescape')).hexdigest()[:8]


def rename(have, out, exe_classes=(), log=print):
    """Rewrite u.obj of every unit in have that defines a colliding class's groups to a renamed copy in out/odr/.
    -> counts."""
    st = collections.Counter()
    if os.environ.get('FNVNX_ODR', '1') == '0':
        return st
    by_path = {u.obj: u for u in have if u.obj}
    os.makedirs(os.path.join(out, 'odr'), exist_ok=True)
    per_obj = _cached_groups(sorted(by_path), os.path.join(out, 'cache', 'odr_groups.json'))
    coll = colliding(per_obj, exe_classes)
    renames = collections.defaultdict(dict)   # object -> {old: new}
    for c, m in coll.items():
        for p, syms in m.items():
            sx = suffix(by_path[p].source)
            for s in syms:
                renames[p][s] = s + sx
    rows = []
    for p, mp in sorted(renames.items()):
        u = by_path[p]
        dst = os.path.join(out, 'odr', os.path.basename(p))
        args = ['--redefine-sym=%s=%s' % (a, b) for a, b in sorted(mp.items())]
        r = subprocess.run([OBJCOPY] + args + [p, dst], capture_output=True, text=True)
        if r.returncode:
            log('odr: objcopy failed for %s: %s' % (p, r.stderr.strip()[:300]))
            st['objcopy-failed'] += 1
            continue
        u.obj = dst
        st['objects'] += 1
        st['symbols'] += len(mp)
    for c, m in sorted(coll.items()):
        rows.append('%s\t%s\t%s\n' % (c, ' '.join(sorted(by_path[p].source for p in m)),
                                      ' '.join(sorted({s for v in m.values() for s in v}))))
    st['classes'] = len(coll)
    with open(os.path.join(out, 'odr.tsv'), 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('# fnvnx-odr/1: class\tunits whose copies were renamed (<symbol>.fnvu<unit hash>)\tsymbols\n')
        f.writelines(rows)
    return st


def exe_class_names(exev):
    """Simple and qualified class names of the exe's RTTI vftables."""
    import classlayout
    out = set()
    for v in exev.values():
        if v.td:
            out.update(classlayout.simple_name(v.td))
    return out
