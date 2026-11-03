#!/usr/bin/env python3
"""d3d_boundary.py <decomp> - where the engine touches Direct3D (stream S6n, docs/RENDER_NATIVE.md).

Reuses render/tools/d3d9_inventory.py's source scan and groups its call sites by subsystem (directory), so the
native-renderer replacement boundary can be drawn. Reads the decomp, writes only stdout.
"""
import collections, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
import d3d9_inventory as inv

def subsystem(rel):
    p = rel.split('/')
    if p[0] != 'src': return p[0]
    if p[1] == 'gamebryo2.2': return '/'.join(p[1:4])
    if p[1] in ('staging', 'unmapped'): return p[1] + '/' + p[2]
    return p[1] if len(p) > 2 else 'src(root)/' + p[1]

def main(tree):
    r = inv.scan(tree)
    sites = collections.Counter(); meths = collections.defaultdict(set)
    files = collections.defaultdict(set)
    for key, fs in r['call_files'].items():
        for f in fs:
            s = subsystem(f); files[s].add(f); meths[s].add(key[1])
    # per-file counts are not kept by the scan; count methods and files per subsystem
    print('| subsystem | files calling D3D | distinct methods | methods |')
    print('|---|---|---|---|')
    for s in sorted(files, key=lambda s: -len(meths[s])):
        print('| %s | %d | %d | %s |' % (s, len(files[s]), len(meths[s]), ', '.join(sorted(meths[s]))))
    print()
    print('files naming D3D types by subsystem:',
          ', '.join('%s %d' % kv for kv in collections.Counter(subsystem(f) for f in r['files_d3d']).most_common()))

if __name__ == '__main__':
    if len(sys.argv) != 2: sys.exit(__doc__)
    main(sys.argv[1])
