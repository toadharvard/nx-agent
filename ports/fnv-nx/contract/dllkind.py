#!/usr/bin/env python3
"""dllkind - the one rule that says what kind a dll of the exe's import graph is (L1 CONTRACT); images.py and modules.py both call it.

RULE (names are iat.norm_dll names; BRIDGE = the dlls with rows in bridge.tsv; FOUND = a file of that name in the given directories,
first directory in list order, case-insensitive, two files of one directory that differ only in case are an error):
  bridge    a dll in BRIDGE: the platform implements its entries (bridge rows); it is never a guest image, even if a file is found, and the
            walk does not continue through it.
  image     a dll that is FOUND, not in BRIDGE, and whose whole normal import closure resolves to images or bridge dlls (the greatest
            such set: a dependency cycle of images stays; a delay-load import is bound at run time and does not decide loadability).
  declared  a dll that is FOUND, not in BRIDGE, and not an image: the declared machine cannot load it (its closure needs a dll that is
            neither found nor bridged).  Its entries must be served by bridge rows; the walk does not continue through it.
  platform  a name that is neither FOUND nor in BRIDGE: a platform import of gate `imports` (owner L5), not a module.
Reach: from the root's import directories (normal, then delay-load, in directory order), depth first through images only.
Pure: the caller supplies imports_of(path) -> (normal names, delay-load names) and find(name) -> path | None."""


def images_of(normal_imports, ok):
    """normal_imports: {name: normal import names} of the found, non-bridge dlls; OK: names that are satisfied (bridge dlls, the root).
    -> the greatest set of names whose every import is in the set or in OK."""
    image = set(normal_imports)
    changed = True
    while changed:
        changed = False
        for name in sorted(image):
            if any(x not in ok and x not in image for x in normal_imports[name]):
                image.discard(name)
                changed = True
    return image


def classify(root_imports, imports_of, find, bridge, root=None):
    """root_imports: (normal, delay) dll names of the root (the exe).  -> (order, kind): order = [(name, path)] of the dlls reached
    from the root in discovery order (found, not bridge; the walk goes through candidates that are images only); kind = {name: 'image' |
    'declared' | 'bridge' | 'platform' | 'root'} for every dll name met."""
    import_of = {}                       # name -> (path, normal, delay) of the found non-bridge dlls reached through found dlls
    met = []                             # discovery order of every name met

    def meet(raw_lists):
        for raw in raw_lists:
            for name in raw:
                if name in kind0:
                    continue
                if name == root:
                    kind0[name] = "root"
                    continue
                if name in bridge:
                    kind0[name] = "bridge"
                    continue
                p = find(name)
                if p is None:
                    kind0[name] = "platform"
                    continue
                kind0[name] = "found"
                n, d = imports_of(p)
                import_of[name] = (p, n, d)
                met.append(name)
                meet([n, d])

    kind0 = {}
    meet(list(root_imports))
    image = images_of({n: v[1] for n, v in import_of.items()}, {n for n, k in kind0.items() if k in ("bridge", "root")})
    kind = {n: ("image" if n in image else "declared") if k == "found" else k for n, k in kind0.items()}
    order, seen = [], set()

    def walk(raw_lists):
        for raw in raw_lists:
            for name in raw:
                if name in seen or kind[name] in ("platform",):
                    continue
                seen.add(name)
                if kind[name] == "image" or kind[name] == "declared":
                    order.append((name, import_of[name][0]))
                if kind[name] == "image":
                    walk([import_of[name][1], import_of[name][2]])

    walk(list(root_imports))
    return order, kind


def read_loads(text):
    """loadlib.tsv (module, site, calls, api, status, names, breaks) -> {module: sorted names its sites resolve to}.  An open site has no
    names (its breaks are the `loadlib` gate's); a name list is comma separated."""
    out = {}
    for line in text.splitlines():
        f = line.split('\t')
        if len(f) < 6 or f[0] == 'module':
            continue
        out.setdefault(f[0], set()).update(x for x in f[5].split(',') if x and x != '-')
    return {m: sorted(v) for m, v in sorted(out.items())}
