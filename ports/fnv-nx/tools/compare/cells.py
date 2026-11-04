#!/usr/bin/env python3
"""cells.py - the sweep's scene list: every CELL of the load order that has an editor id (`coc` works on those), as
interior or exterior with its worldspace, in a fixed order, and the scenes.json-style scene of each.

    cells.py list <plugin>... [--shard k/N]                  tsv: id, kind, cell editor id, world editor id
    cells.py scene <plugin>... --cell <editor id> [--night]   one scene entry (json) for wine_ref.sh

Exteriors get a day scene (gamehour 12) and a night scene (gamehour 23); interiors one scene. The id is
<cell>[_night]. Shards take every N-th scene by its position in the fixed order (k from 1). Reads only the public
record structure (esm.py); prints editor ids, which name game content: keep the lists private.
"""
import argparse, json, struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import esm


def cells(paths):
    plugs = esm.load_order(paths)
    worlds, out, seen = {}, [], set()
    for pl in plugs:
        for t, fid, flags, data, groups in pl.walk({b'WRLD'}):
            worlds[esm.gid(pl, fid)] = esm.edid(esm.fields(data))
    for pl in plugs:
        for t, fid, flags, data, groups in pl.walk({b'CELL'}):
            if flags & esm.F_DELETED:
                continue
            f = esm.fields(data)
            e = esm.edid(f)
            if not e or e.lower() in seen:
                continue
            seen.add(e.lower())
            d = f.get(b'DATA', b'\0'); d = d[0] if isinstance(d, list) else d
            interior = bool(d[0] & 1) if d else False
            world = ''
            for gtype, label in groups:
                if gtype == 1:      # world children: label = the world's form id
                    world = worlds.get(esm.gid(pl, struct.unpack('<I', label)[0]), '')
            out.append(('interior' if interior else 'exterior', e, world))
    out.sort(key=lambda c: (c[0] != 'interior', c[1].lower()))
    scenes = []
    for kind, e, w in out:
        scenes.append((e, kind, e, w))
        if kind == 'exterior':
            scenes.append((e + '_night', kind, e, w))
    return scenes


def scene(cell, kind, night=False):
    hour = 23 if night else (12 if kind == 'exterior' else 9)
    cmds = [f'coc {cell}', 'wait:20', 'tai', 'tcai', f'set gamehour to {hour}', 'player.setangle x 0',
            'player.setangle z 90', 'wait:5']
    return {'id': cell + ('_night' if night else ''), 'title': f'{kind} {cell}' + (' night' if night else ''),
            'cell': cell, 'frames': 3, 'class': 'deterministic', 'cmds': cmds}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['list', 'scene'])
    ap.add_argument('plugins', nargs='+')
    ap.add_argument('--shard'); ap.add_argument('--cell'); ap.add_argument('--night', action='store_true')
    a = ap.parse_args()
    sc = cells(a.plugins)
    if a.cmd == 'list':
        k, n = map(int, a.shard.split('/')) if a.shard else (1, 1)
        for i, s in enumerate(sc):
            if i % n == k - 1:
                print('\t'.join(s))
        return 0
    m = [s for s in sc if s[0] == a.cell + ('_night' if a.night else '') or (s[2] == a.cell and not a.night)]
    if not m:
        print(f'cells: no cell {a.cell}', file=sys.stderr); return 1
    print(json.dumps(scene(m[0][2], m[0][1], a.night)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
