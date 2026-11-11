"""The entry universe (tools/universe.py output, FNVNX_UNIVERSE, default /home/user/l3facts/universe.tsv): entry -> (kind, extent, parent).
lift() consults it for the defaults: an entry with a parent is lifted from its label inside the parent's range, a function gets all its own labels."""
import collections, os

_U = {}


def load(path=None):
    path = path or os.environ.get('FNVNX_UNIVERSE', '/home/user/l3facts/universe.tsv')
    if path not in _U:
        ent, kids = {}, collections.defaultdict(list)
        if os.path.exists(path):
            for l in open(path):
                if l[0] == '#':
                    continue
                a, kind, ext, fl = l.rstrip('\n').split('\t')
                par = None
                for f in fl.split(','):
                    if f.startswith('parent='):
                        par = int(f[7:], 16)
                ent[int(a, 16)] = (kind, int(ext), par)
            for a, (k, e, p) in ent.items():
                if p is not None:
                    kids[p].append(a)
        _U[path] = (ent, dict(kids))
    return _U[path]


def entry_args(a, path=None):
    """-> lift() keyword arguments of universe entry a (None when a is not an entry)"""
    ent, kids = load(path)
    if a not in ent:
        return None
    kind, ext, par = ent[a]
    base, size = (par, ent[par][1]) if par is not None and par in ent else (a, ext)
    return dict(addr=base, size=size, start=a, owns=kids.get(base, ()))
