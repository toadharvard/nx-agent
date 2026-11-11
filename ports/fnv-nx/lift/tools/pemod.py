#!/usr/bin/env python3
"""PE32 generalities for the lifter tools: sections, address translation, relocations, imports, exports, and the byte ranges that
the PE data directories name (import/export/IAT/delay/debug/load-config/TLS/bound/resource/reloc). Pure functions of the file bytes."""
import os, struct, bisect
from types import SimpleNamespace
EXEC = 0x20000000 | 0x20           # IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_CNT_CODE
MODULES = '/home/user/wt-l1/modules/modules.tsv'                                  # L1's static load bases (env FNVNX_MODULES)
MODDIRS = '/home/user/wt-exe:/home/user/wt-redist:/home/user/game'                # where the module files live (env FNVNX_MODDIRS)

def modules(path=None):
    """{lower-case file name: SimpleNamespace(name, sha256, size, pref, base, sizeimg, tls, entry, static)} from modules.tsv (L1: assigned_base is the load base of the image)."""
    out = {}
    for l in open(path or os.environ.get('FNVNX_MODULES', MODULES)):
        if l[0] == '#' or not l.strip(): continue
        f = l.rstrip('\n').split('\t'); h = lambda x: int(x, 16)
        out[f[0].lower()] = SimpleNamespace(name=f[0].lower(), sha256=f[1], size=int(f[2]), pref=h(f[3]), base=h(f[4]), sizeimg=h(f[5]), tls=int(f[6]), entry=h(f[7]), static=int(f[8]))
    return out

def locate(arg):
    """arg = a module file name or a path -> (path, modules.tsv row). The name must be in modules.tsv (else KeyError) and the file's sha256 and size must equal the row (else ValueError)."""
    import hashlib
    name = os.path.basename(arg).lower(); mods = modules()
    if name not in mods and not name.endswith('.dll'): name += '.dll'
    if name not in mods: raise KeyError('%s is not in modules.tsv' % name)
    row = mods[name]; cand = [arg] if os.path.isfile(arg) else [os.path.join(d, f) for d in os.environ.get('FNVNX_MODDIRS', MODDIRS).split(':') if os.path.isdir(d) for f in sorted(os.listdir(d)) if f.lower() == name]
    if not cand: raise FileNotFoundError('%s: no file in %s' % (name, os.environ.get('FNVNX_MODDIRS', MODDIRS)))
    for c in cand:
        h = hashlib.sha256(open(c, 'rb').read()).hexdigest()
        if h == row.sha256 and os.path.getsize(c) == row.size: return c, row
    raise ValueError('%s: sha256 mismatch against modules.tsv (%s has %s, expected %s)' % (name, cand[0], h, row.sha256))

def load(path, newbase=None):
    """newbase=None: the file as stored (preferred base). Else the image as the Windows loader maps it at `base`: every address (sections, relocations,
    exports, entry VAs) at `newbase`, delta = newbase - preferred added to the dword of every HIGHLOW fixup, so all stored VAs (TLS directory, load config, tables) speak `newbase`."""
    if newbase is None and os.environ.get('FNVNX_BASE'): newbase = int(os.environ['FNVNX_BASE'], 16)      # same resolver as fnvlift.Image
    d = open(path, 'rb').read(); pe = struct.unpack_from('<I', d, 0x3c)[0]
    assert d[pe:pe + 4] == b'PE\0\0' and struct.unpack_from('<H', d, pe + 24)[0] == 0x10b, 'not a PE32 image'
    nsec, soh = struct.unpack_from('<H', d, pe + 6)[0], struct.unpack_from('<H', d, pe + 20)[0]
    base, sizeimg = struct.unpack_from('<II', d, pe + 52)[0], struct.unpack_from('<I', d, pe + 80)[0]
    nd = struct.unpack_from('<I', d, pe + 24 + 92)[0]; secs = []; xs = []; o = pe + 24 + soh
    for i in range(nsec):
        nm = d[o:o + 8].rstrip(b'\0').decode(); vs, va, rs, ro = struct.unpack_from('<IIII', d, o + 8); fl = struct.unpack_from('<I', d, o + 36)[0]
        secs.append((nm, base + va, vs, ro, rs))
        if fl & EXEC: xs.append((nm, base + va, min(vs, rs), ro))
        o += 40
    order = sorted(range(len(secs)), key=lambda i: secs[i][1]); starts = [secs[i][1] for i in order]
    M = SimpleNamespace(d=d, pe=pe, base=base, sizeimg=sizeimg, secs=secs, xs=xs, nd=nd, entry=struct.unpack_from('<I', d, pe + 40)[0])
    def loc(a):
        i = bisect.bisect_right(starts, a) - 1
        if i >= 0:
            nm, va, vs, ro, rs = secs[order[i]]
            if a < va + vs and a - va < rs: return nm, ro + a - va
    M.loc = loc
    M.off = lambda a: (loc(a) or (0, None))[1]
    M.sec = lambda a: (loc(a) or (None,))[0]
    def u32(a):
        x = M.off(a); return struct.unpack_from('<I', d, x)[0] if x is not None else 0
    M.u32 = u32
    M.dirent = lambda i: struct.unpack_from('<II', d, pe + 24 + 96 + 8 * i) if i < nd else (0, 0)
    def cstr(a, wide=False):
        x = M.off(a); e = x
        if x is None: return b''
        if wide:
            while d[e:e + 2] != b'\0\0' and e < len(d): e += 2
        else:
            while d[e] and e < len(d) - 1: e += 1
        return d[x:e]
    M.cstr = cstr
    M.pref = M.base
    if newbase is not None and newbase != M.base:
        delta = (newbase - M.base) & 0xffffffff; rl = relocs(M)                    # relocation list at the preferred base, read before the shift
        M.d = d = bytearray(d)
        for r in rl or ():
            x = M.off(r)
            if x is not None: struct.pack_into('<I', d, x, (struct.unpack_from('<I', d, x)[0] + delta) & 0xffffffff)
        secs[:] = [(n, (va + delta) & 0xffffffff, vs, ro, rs) for n, va, vs, ro, rs in secs]; xs[:] = [(n, (va + delta) & 0xffffffff, z, ro) for n, va, z, ro in xs]
        starts[:] = [(x + delta) & 0xffffffff for x in starts]; M.base = newbase
    return M

def relocs(M):
    """HIGHLOW fixups as absolute addresses; None if the image has no base relocation directory."""
    rr, rsz = M.dirent(5)
    if not rr or not rsz or M.off(M.base + rr) is None: return None
    p = M.off(M.base + rr); pend = p + rsz; out = []
    while p < pend:
        page, bs = struct.unpack_from('<II', M.d, p)
        if not bs: break
        for i in range((bs - 8) // 2):
            e = struct.unpack_from('<H', M.d, p + 8 + 2 * i)[0]
            if e >> 12 == 3: out.append(M.base + page + (e & 0xfff))
        p += bs
    return out

def imports(M):
    """{IAT slot VA: 'dll!name' (or 'dll!#ord')} from the import table."""
    out = {}; b = M.base; rva, sz = M.dirent(1); a = b + rva if rva else 0
    while a and M.u32(a + 12):
        dll = M.cstr(b + M.u32(a + 12)).decode('latin1').lower(); it = b + (M.u32(a) or M.u32(a + 16)); ia = b + M.u32(a + 16); k = 0
        while M.u32(it + 4 * k):
            v = M.u32(it + 4 * k)
            out[ia + 4 * k] = '%s!%s' % (dll, '#%d' % (v & 0xffff) if v >> 31 else M.cstr(b + (v & 0x7fffffff) + 2).decode('latin1')); k += 1
        a += 20
    return out

def delay_imports(M):
    """[(dll, name)] of the delay-import directory."""
    out = []; rva, sz = M.dirent(13)
    if not rva: return out
    a = M.base + rva
    while M.u32(a + 4):
        v = 0 if M.u32(a) & 1 else M.base; dll = M.cstr(v + M.u32(a + 4)).decode('latin1').lower(); k = 0; it = v + M.u32(a + 16)
        while M.u32(it + 4 * k):
            w = M.u32(it + 4 * k); out.append((dll, '#%d' % (w & 0xffff) if w >> 31 else M.cstr(v + (w & 0x7fffffff) + 2).decode('latin1'))); k += 1
        a += 32
    return out

def exports(M):
    """(code VAs sorted, forwarder count, (ordinal base, number of functions, number of names))."""
    rva, sz = M.dirent(0)
    if not rva: return [], 0, (0, 0, 0)
    b = M.base; e = b + rva; nf, nn, af = M.u32(e + 20), M.u32(e + 24), b + M.u32(e + 28); code = set(); fw = 0
    for i in range(nf):
        r = M.u32(af + 4 * i)
        if not r: continue
        if rva <= r < rva + sz: fw += 1
        else: code.add(b + r)
    return sorted(code), fw, (M.u32(e + 16), nf, nn)

def pe_ranges(M):
    """[(va, length, label)] bytes that the PE data directories name (structures, tables and the strings they point to)."""
    R = []; b = M.base; u = M.u32
    def dr(i, lab):
        rva, sz = M.dirent(i)
        if rva and sz: R.append((b + rva, sz, lab))
    def zs(a, lab, wide=False):
        if a and M.off(a) is not None: R.append((a, len(M.cstr(a, wide)) + (2 if wide else 1), lab))
    for i, lab in ((2, 'resource'), (5, 'reloc'), (11, 'bound-import'), (12, 'iat'), (4, 'certificate'), (14, 'clr')): dr(i, lab)
    rva, sz = M.dirent(10)
    if rva: R.append((b + rva, max(u(b + rva), 4), 'load-config'))
    rva, sz = M.dirent(0)                                          # export: header, three tables, names
    if rva:
        e = b + rva; nf, nn = u(e + 20), u(e + 24); R.append((e, 40, 'export')); zs(b + u(e + 12), 'export-name')
        R += [(b + u(e + 28), 4 * nf, 'export-func'), (b + u(e + 32), 4 * nn, 'export-names'), (b + u(e + 36), 2 * nn, 'export-ords')]
        for i in range(nn): zs(b + u(b + u(e + 32) + 4 * i), 'export-name')
        for i in range(nf):
            r = u(b + u(e + 28) + 4 * i)
            if rva <= r < rva + sz: zs(b + r, 'export-fwd')
    def thunks(t, lab):                                            # 0-terminated dword array; by-name entries are hint + string
        k = 0
        while t and M.off(t + 4 * k) is not None:
            v = u(t + 4 * k)
            if not v: break
            if not v >> 31 and lab == 'ilt':
                n = len(M.cstr(b + v + 2)); R.append((b + v, 2 + n + 1 + ((n + 1) & 1), 'import-name'))
            k += 1
        if t: R.append((t, 4 * (k + 1), lab))
    rva, sz = M.dirent(1)
    if rva:
        a = b + rva; n = 0
        while u(a + 20 * n + 12) or u(a + 20 * n + 16): n += 1
        R.append((a, 20 * (n + 1), 'import'))
        for i in range(n):
            q = a + 20 * i; zs(b + u(q + 12), 'import-dll'); it = u(q) or u(q + 16)
            thunks(b + it if it else 0, 'ilt'); thunks(b + u(q + 16), 'iat-thunks')
    rva, sz = M.dirent(13)
    if rva:
        a = b + rva; n = 0
        while u(a + 32 * n + 4): n += 1
        R.append((a, 32 * (n + 1), 'delay-import'))
        for i in range(n):
            q = a + 32 * i; v = 0 if u(q) & 1 else b
            zs(v + u(q + 4), 'delay-dll'); R.append((v + u(q + 8), 4, 'delay-hmod')); thunks(v + u(q + 12), 'iat-thunks'); thunks(v + u(q + 16), 'ilt')
    rva, sz = M.dirent(6)
    for i in range(sz // 28 if rva else 0):
        q = b + rva + 28 * i; R.append((q, 28, 'debug'))
        if u(q + 20) and u(q + 16): R.append((b + u(q + 20), u(q + 16), 'debug-data'))
    rva, sz = M.dirent(9)
    if rva:
        q = b + rva; R.append((q, 24, 'tls')); s0, s1, ix, cb = u(q), u(q + 4), u(q + 8), u(q + 12)
        if s1 > s0: R.append((s0, s1 - s0, 'tls-template'))
        R.append((ix, 4, 'tls-index'))
        k = 0
        while cb and u(cb + 4 * k): k += 1
        if cb: R.append((cb, 4 * (k + 1), 'tls-callbacks'))
    return sorted(R)
