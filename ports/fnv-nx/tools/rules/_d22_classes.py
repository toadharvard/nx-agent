"""_d22_classes.py - the class list of prepare rule D22 (docs/ARCHITECTURE.md D22, tools/rules/s1_compile.py).

    python3 tools/rules/_d22_classes.py --exe FalloutNV.exe --decomp ext/fnv_decomp [--addrmap out/addrmap.tsv]
                                       -o <dir>/d22.json [--report <file.tsv>]

A decomp class is listed when:
  - its first declared virtual is at slot 0 (it has no base) and it declares no destructor;
  - that virtual is a placeholder: no definition outside the class, an empty or one-statement inline body at
    most, and no call by its name in the file;
  - every exe vftable paired with the class's name has a deleting destructor in slot 0.
    The pairings are the recipe's `??_7<C>@@6B@` symbols, plus the link's primary V rows when --addrmap is given,
    plus (--rtti) the image's RTTI: the primary vftables whose type descriptor names the class. RTTI is used only to
    list (positive evidence); a slot 0 the shape test does not recognise is no evidence against a class.
    A slot-0 function is a deleting destructor when the recipe names it ??_G / ??_E, or when its x86 tests bit 0
    of its flags argument, calls an operator delete and returns 4 bytes.

The output reads the owner's exe (slot 0 of each vftable), so it is exe-derived. It goes to the private
fnv-nx-build branch recon/d22_vtable_dtor (tools/farm/recon.sh puts it into FNVNX_RECON), never into nx-agent.
Format: {"format": "fnvnx-d22/1", "decomp_commit": <sha>, "classes": [{"class": "ns::C" or "T<>" for a template,
"members": [<placeholder names>], "vftables": [...], "slot0": [...]}]}.
"""
import argparse
import collections
import json
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import prepare            # noqa: E402
import s1_compile as S    # noqa: E402

SRC_EXT = ('.cpp', '.h', '.inl', '.hpp', '.c')


class Image:
    def __init__(self, path):
        d = self.d = open(path, 'rb').read()
        pe = struct.unpack_from('<I', d, 0x3c)[0]
        n = struct.unpack_from('<H', d, pe + 6)[0]
        opt = struct.unpack_from('<H', d, pe + 20)[0]
        self.base = struct.unpack_from('<I', d, pe + 24 + 28)[0]
        sh = pe + 24 + opt
        self.secs = [struct.unpack_from('<8sIIII', d, sh + 40 * i) for i in range(n)]

    def off(self, a):
        for _, vs, va, rs, ra in self.secs:
            if va <= a - self.base < va + max(vs, rs):
                return a - self.base - va + ra
        return None

    def dword(self, a):
        o = self.off(a)
        return struct.unpack_from('<I', self.d, o)[0] if o is not None else None

    def text(self, a):
        return any(nm.startswith(b'.text') and va <= a - self.base < va + vs for nm, vs, va, _, _ in self.secs)

    def code(self, a, n):
        o = self.off(a)
        return self.d[o:o + n] if o is not None else b''


FLAG_TESTS = (b'\xf6\x44\x24\x04\x01', b'\x8b\x45\x08\x83\xe0\x01', b'\xf6\x45\x08\x01', b'\x8a\x45\x08\x24\x01',
              b'\x0f\xb6\x45\x08\x83\xe0\x01', b'\xf6\x44\x24\x08\x01')
DELETE_NAME = re.compile(r'^\?\?(?:3|_V)@YAXPAX@Z$|^\?_delete@@|^\?NiFree@@|^\?\?3\w*@@SAXPAX@Z$')


def deleting(img, a, names, deletes):
    """'named' / 'shape' when the function at a is a deleting destructor (see the module doc), else ''"""
    if any(n.startswith(('??_G', '??_E')) for n in names.get(a, ())):
        return 'named'
    b = img.code(a, 160)
    if not any(t in b[:80] for t in FLAG_TESTS) or b'\xc2\x04\x00' not in b:
        return ''
    for i in range(len(b) - 4):
        if b[i] == 0xe8 and (a + i + 5 + struct.unpack_from('<i', b, i + 1)[0]) & 0xffffffff in deletes:
            return 'shape'
    return ''


def msvc_class(q):
    """`Inner@Outer@ns` -> 'ns::Outer::Inner'; a template `?$X@...` -> 'X<>'; None for other shapes"""
    if q.startswith('?$'):
        n = q[2:].split('@')[0]
        return n + '<>' if re.match(r'^[A-Za-z_]\w*$', n) else None
    parts = q.split('@')
    if not all(re.match(r'^[A-Za-z_]\w*$', p) for p in parts):
        return None
    return '::'.join(reversed(parts))


def pairings(units, addrmap):
    pair = collections.defaultdict(set)
    for u in units:
        for n, a in u.get('symbols', {}).items():
            m = re.match(r'^\?\?_7(.+?)@@6B@$', n)
            q = m and msvc_class(m.group(1))
            if q:
                pair[q].add(int(a, 16))
    if addrmap:
        rows = []
        for line in open(addrmap):
            f = line.rstrip('\n').split('\t')
            if len(f) > 5 and f[1] == 'V' and f[5].startswith('primary'):
                rows.append((int(f[0], 16), f[2]))
        dem = subprocess.run(['c++filt'], input='\n'.join(z for _, z in rows), capture_output=True,
                             text=True).stdout.split('\n')
        for (a, _), d in zip(rows, dem):
            n = d.replace('vtable for ', '').strip()
            if '(anonymous namespace)' in n:
                continue
            if '<' in n:
                n = re.sub(r'<.*', '', n) + '<>'
            pair[n].add(a)
    return pair


def rtti_vftables(img):
    """{class key: set(primary vftable)} from the image's RTTI: a dword in .rdata that points at a Complete Object
    Locator (signature 0, offset 0) whose type descriptor names `.?AV<C>@@` / `.?AU<C>@@` is followed by the
    vftable (its first slot in .text). Secondary (offset != 0) vftables are left out."""
    out = collections.defaultdict(set)
    for nm, vs, va, rs, ra in img.secs:
        if not nm.startswith(b'.rdata'):
            continue
        for o in range(ra, ra + min(vs, rs) - 8, 4):
            co = img.off(struct.unpack_from('<I', img.d, o)[0])
            if co is None or co + 20 > len(img.d):
                continue
            sig, off, _, td, _ = struct.unpack_from('<IIIII', img.d, co)
            if sig or off:
                continue
            to = img.off(td)
            if to is None:
                continue
            name = img.d[to + 8:to + 208].split(b'\0')[0]
            if not name.startswith((b'.?AV', b'.?AU')) or not name.endswith(b'@@'):
                continue
            vft = img.base + va + (o - ra) + 4
            if not img.text(img.dword(vft) or 0):
                continue
            q = msvc_class(name[4:-2].decode('latin1'))
            if q:
                out[q].add(vft)
    return out


def scan(decomp):
    """{class key: set(members)} for every definition whose first virtual is a placeholder at slot 0"""
    found = collections.defaultdict(set)
    files = 0
    for root in ('src', 'include'):
        for dp, _, fs in os.walk(os.path.join(decomp, root)):
            for f in fs:
                if not f.endswith(SRC_EXT):
                    continue
                t = open(os.path.join(dp, f), encoding='utf-8', errors='surrogateescape').read()
                if 'virtual' not in t:
                    continue
                files += 1
                masked = S.d22_mask(t)
                classes = S.d22_classes(t, masked)
                vdecls = collections.defaultdict(list)
                for c in classes:
                    for v in c['virtuals']:
                        if v[2]: vdecls[v[2]].append((v[0], v[1]))
                for c in classes:
                    if not c['first'] or c['bases'] or c['dtor'] or any(q.startswith('{') for q in c['qual']):
                        continue
                    if S.d22_placeholder(masked, c['qual'][-1], c['first'], vdecls[c['first'][2]]):
                        key = c['qual'][-1] + '<>' if c['template'] else '::'.join(c['qual'])
                        found[key].add(c['first'][2])
    return found, files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True)
    ap.add_argument('--decomp', required=True)
    ap.add_argument('--addrmap')
    ap.add_argument('-o', '--out', required=True)
    ap.add_argument('--report')
    ap.add_argument('--rtti', action='store_true',
                    help='also pair a class with the primary vftables the image\'s RTTI names it by (S1-rules: '
                         'stand-ins with a named slot 0 that carry the game\'s class name)')
    ap.add_argument('--only-new', help='an existing d22.json: write only classes it does not list')
    a = ap.parse_args()
    img = Image(a.exe)
    units = json.load(open(os.path.join(a.decomp, 'build', 'units.json')))['units']
    names, deletes = collections.defaultdict(set), set()
    for u in units:
        for f in u['functions']:
            names[int(f['address'], 16)].add(f['symbol'])
        for n, ad in u.get('symbols', {}).items():
            if DELETE_NAME.match(n):
                deletes.add(int(ad, 16))
    pair = pairings(units, a.addrmap)
    if a.rtti:
        for k, v in rtti_vftables(img).items():
            if k not in pair:                  # the recipe's / link's pairing wins where it has one
                pair[k] |= v
    found, files = scan(a.decomp)
    if a.only_new:
        known = {c['class'] for c in json.load(open(a.only_new)).get('classes', ())}
        found = {k: v for k, v in found.items() if k not in known}
    st = collections.Counter()
    out, rep = [], []
    for key in sorted(found):
        vs = sorted(pair.get(key, ()))
        if not vs:
            st['placeholder classes without a vftable pairing'] += 1
            continue
        s0 = [img.dword(v) for v in vs]
        kinds = [deleting(img, x, names, deletes) if x and img.text(x) else '' for x in s0]
        if not all(kinds):
            st['paired, slot 0 not a deleting destructor (in at least one vftable)'] += 1
            rep.append((key, ','.join(sorted(found[key])), 'skip', ' '.join('%08x' % v for v in vs)))
            continue
        st['listed'] += 1
        out.append({'class': key, 'members': sorted(found[key]), 'vftables': ['%08x' % v for v in vs],
                    'slot0': ['%08x' % x for x in s0], 'evidence': sorted(set(kinds))})
        rep.append((key, ','.join(sorted(found[key])), 'listed', ' '.join('%08x' % v for v in vs)))
    commit = subprocess.run(['git', '-C', a.decomp, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    json.dump({'format': 'fnvnx-d22/1', 'decomp_commit': commit, 'classes': out}, open(a.out, 'w'), indent=0)
    if a.report:
        with open(a.report, 'w') as f:
            for r in rep:
                f.write('\t'.join(r) + '\n')
    print('files %d, placeholder classes %d, paired names %d' % (files, len(found), len(pair)))
    for k, v in sorted(st.items()):
        print('%s: %d' % (k, v))


if __name__ == '__main__':
    main()
