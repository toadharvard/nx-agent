#!/usr/bin/env python3
"""esm.py - a small reader of TES4-format plugins (FalloutNV.esm and its DLCs) for S16-compare.

It gives the *static* half of a scene state: the cell a scene loads and every placed reference in it (REFR, ACHR,
ACRE, PGRE, PMIS) with its base form, position, rotation, scale and initially-disabled flag, after applying the load
order's overrides. The format is the public TES4/FO3/FNV plugin format (record and group headers, zlib-compressed
records, XXXX subrecords). Nothing here is taken from the decomp.

    esm.py find   <plugin>... --type WTHR [--edid-re RE]          editor ids + form ids of one record type
    esm.py cell   <plugin>... --cell <editor id or X,Y> [--world WastelandNV] [--radius 2] [-o state.json]
                                                                   the cell's references as a scene-state file
"""
import argparse, json, math, re, struct, sys, zlib

REF_TYPES = {b'REFR', b'ACHR', b'ACRE', b'PGRE', b'PMIS', b'PBEA', b'PFLA', b'PCBE', b'NAVM'}
PLACED = REF_TYPES - {b'NAVM'}
F_DELETED, F_DISABLED, F_PERSISTENT, F_COMPRESSED = 0x20, 0x800, 0x400, 0x40000

def subrecords(data):
    i, big = 0, None
    while i + 6 <= len(data):
        t, n = data[i:i + 4], struct.unpack_from('<H', data, i + 4)[0]
        i += 6
        if t == b'XXXX':
            big = struct.unpack_from('<I', data, i)[0]; i += n; continue
        if big is not None: n, big = big, None
        yield t, data[i:i + n]; i += n

class Plugin:
    """one plugin file: its masters, and a flat walk of records with the group path they sit in"""
    def __init__(self, path):
        self.path, self.buf = path, open(path, 'rb').read()
        if self.buf[:4] != b'TES4': raise ValueError(f'{path}: not a TES4 plugin')
        n = struct.unpack_from('<I', self.buf, 4)[0]
        self.masters = [d.rstrip(b'\0').decode('latin-1') for t, d in subrecords(self.buf[24:24 + n]) if t == b'MAST']
        self.hdr_end = 24 + n

    def walk(self, want=None):
        """yield (type, formid, flags, data, groups) for every record; groups = [(gtype, label), ...] outermost first.
        want: a set of record types to decode (others are skipped without decompression)."""
        buf, stack = self.buf, []          # stack of (end offset, gtype, label)
        i = self.hdr_end
        while i < len(buf):
            while stack and i >= stack[-1][0]: stack.pop()
            t = buf[i:i + 4]
            if t == b'GRUP':
                size, label, gtype = struct.unpack_from('<I4si', buf, i + 4)
                stack.append((i + size, gtype, label)); i += 24; continue
            size, flags, fid = struct.unpack_from('<III', buf, i + 4)
            body = i + 24
            i = body + size
            if want is not None and t not in want: continue
            data = buf[body:body + size]
            if flags & F_COMPRESSED:
                data = zlib.decompress(data[4:])
            yield t, fid, flags, data, [(g, l) for _, g, l in stack]

def load_order(paths):
    """plugins in load order with the mapping of each one's local mod index to the global one"""
    plugs, names = [], []
    for p in paths:
        pl = Plugin(p); base = p.replace('\\', '/').split('/')[-1]
        idx = []
        for m in pl.masters:
            idx.append(next((k for k, n in enumerate(names) if n.lower() == m.lower()), None))
        idx.append(len(names))             # its own records
        pl.modmap, pl.name = idx, base
        plugs.append(pl); names.append(base)
    return plugs

def gid(pl, fid):
    """the form id in the global load order (top byte = position of the defining plugin)"""
    mi = fid >> 24
    g = pl.modmap[min(mi, len(pl.modmap) - 1)]
    return ((g if g is not None else 0xFF) << 24) | (fid & 0xFFFFFF)

def fields(data):
    d = {}
    for t, v in subrecords(data): d.setdefault(t, v)
    return d

def edid(f):
    v = f.get(b'EDID')
    return v.rstrip(b'\0').decode('latin-1') if v else ''

def find(plugs, rtype, edid_re=None):
    out = {}
    rx = re.compile(edid_re, re.I) if edid_re else None
    for pl in plugs:
        for t, fid, flags, data, _ in pl.walk({rtype.encode()}):
            e = edid(fields(data))
            if rx and not rx.search(e): continue
            out[gid(pl, fid)] = e
    return out

def cell_refs(plugs, cell, world=None, radius=0):
    """the scene-state 'cell' and 'refs' of one cell after all overrides (last plugin wins per form id)"""
    want = {b'CELL', b'WRLD'} | REF_TYPES
    grid = None
    m = re.fullmatch(r'\s*(-?\d+)\s*,\s*(-?\d+)\s*', cell)
    if m: grid = (int(m.group(1)), int(m.group(2)))
    worlds, cinfo, target, refs = {}, {}, None, {}
    for pl in plugs:
        for t, fid, flags, data, groups in pl.walk(want):
            g = gid(pl, fid)
            if t == b'WRLD':
                worlds[g] = edid(fields(data)); continue
            if t == b'CELL':
                f = fields(data)
                w = next((struct.unpack('<I', l)[0] for gt, l in groups if gt == 1), None)
                w = gid(pl, w) if w is not None else None
                xclc = struct.unpack_from('<ii', f[b'XCLC']) if b'XCLC' in f and len(f[b'XCLC']) >= 8 else None
                cinfo[g] = {'form': g, 'edid': edid(f), 'world': w, 'grid': xclc,
                            'interior': bool(f.get(b'DATA', b'\0')[0] & 1)}
                continue
            # a placed reference: its cell is the label of the enclosing cell-children group (type 6)
            owner = next((struct.unpack('<I', l)[0] for gt, l in reversed(groups) if gt == 6), None)
            if owner is None: continue
            owner = gid(pl, owner)
            f = fields(data)
            if t not in PLACED: continue
            name = struct.unpack('<I', f[b'NAME'])[0] if b'NAME' in f else 0
            pos = struct.unpack('<6f', f[b'DATA'][:24]) if b'DATA' in f and len(f[b'DATA']) >= 24 else (0,) * 6
            scale = struct.unpack('<f', f[b'XSCL'])[0] if b'XSCL' in f else 1.0
            persistent = any(gt == 8 for gt, _ in groups)
            refs[g] = {'ref': g, 'base': gid(pl, name), 'kind': t.decode(), 'edid': edid(f), 'cell': owner,
                       'pos': [round(x, 3) for x in pos[:3]], 'rot': [round(x, 5) for x in pos[3:]],
                       'scale': round(scale, 4), 'disabled': bool(flags & F_DISABLED),
                       'deleted': bool(flags & F_DELETED), 'persistent': persistent}
    for c in cinfo.values():
        if c['world'] is not None and world and worlds.get(c['world'], '').lower() != world.lower(): continue
        if grid is not None:
            if c['grid'] == grid and not c['interior'] and (world is None or c['world'] is not None): target = c; break
        elif c['edid'].lower() == cell.lower(): target = c; break
    if target is None: raise SystemExit(f'cell {cell!r} not found')
    # exterior: the cells of the loaded grid (uGridsToLoad 5 = radius 2), plus the world's persistent references
    # (they live in a persistent cell without a grid) whose position falls in the grid
    cells = [target]
    if not target['interior'] and target['grid'] and radius:
        gx, gy = target['grid']
        cells = [c for c in cinfo.values() if c['world'] == target['world'] and c['grid'] and not c['interior']
                 and abs(c['grid'][0] - gx) <= radius and abs(c['grid'][1] - gy) <= radius]
    forms = {c['form'] for c in cells}
    pers = {c['form'] for c in cinfo.values() if c['world'] == target['world'] and c['world'] is not None
            and not c['grid']} if not target['interior'] else set()
    def in_grid(r):
        gx, gy = target['grid']
        cx, cy = math.floor(r['pos'][0] / 4096), math.floor(r['pos'][1] / 4096)
        return abs(cx - gx) <= radius and abs(cy - gy) <= radius
    sel = sorted((r for r in refs.values() if not r['deleted'] and
                  (r['cell'] in forms or (r['cell'] in pers and in_grid(r)))), key=lambda r: r['ref'])
    for r in sel: del r['deleted']
    cell_out = {'form': target['form'], 'edid': target['edid'], 'interior': target['interior'],
                'world': worlds.get(target['world'], '') if target['world'] is not None else '',
                'grid': list(target['grid']) if target['grid'] else None,
                'loaded': sorted(c['form'] for c in cells)}
    return cell_out, sel

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['find', 'cell'])
    ap.add_argument('plugins', nargs='+')
    ap.add_argument('--type', default='WTHR'); ap.add_argument('--edid-re')
    ap.add_argument('--cell'); ap.add_argument('--world'); ap.add_argument('-o')
    ap.add_argument('--radius', type=int, default=0, help='exterior: cells around the target (uGridsToLoad 5 -> 2)')
    ap.add_argument('--scene')
    a = ap.parse_args()
    plugs = load_order(a.plugins)
    if a.cmd == 'find':
        for g, e in sorted(find(plugs, a.type, a.edid_re).items()): print(f'{g:08X}\t{e}')
        return 0
    c, refs = cell_refs(plugs, a.cell, a.world, a.radius)
    st = {'format': 'fnvnx-scenestate/1', 'source': 'esm', 'scene': a.scene, 'plugins': [p.name for p in plugs],
          'cell': c, 'refs': refs}
    s = json.dumps(st, indent=1)
    if a.o: open(a.o, 'w').write(s + '\n')
    else: print(s)
    print(f"{c['edid'] or c['grid']}: {len(refs)} refs", file=sys.stderr)

if __name__ == '__main__':
    sys.exit(main())
