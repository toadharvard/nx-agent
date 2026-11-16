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
RUNTIME is the one declared non-image module, the namespace of lift/rt's own entries (`FNVNX_EXPORT("!rt", name, symbol, kind)`): it has no spec and no file, its rows come from the
definer's own declared type, it is never in BRIDGE of this rule (LoadLibrary/GetModuleHandle of it is ERROR_MOD_NOT_FOUND like any undeclared name) and no image imports it.
Pure: the caller supplies imports_of(path) -> (normal names, delay-load names) and find(name) -> path | None."""

RUNTIME = "!rt"


def is_runtime(dll):
    """True iff DLL (a record's module or an iat.norm_dll name) names the runtime's own module."""
    return dll in (RUNTIME, RUNTIME + ".dll")


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


def classify(root_imports, imports_of, find, served, root=None):
    """root_imports: (normal, delay) dll names of the root (the exe).  -> (order, kind): order = [(name, path)] of the dlls reached
    from the root in discovery order (found, not bridge; the walk goes through candidates that are images only); kind = {name: 'image' |
    'load-fails' | 'served' | 'absent' | 'root'} for every dll name met. SERVED: the names the platform serves (the declared dll set without a file, platform_served); a name with a file is never served.
    'load-fails': found, whose import closure does not resolve (on Windows its load fails: LoadLibrary returns NULL, error 126). 'absent': neither found nor served."""
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
                p = find(name)
                if p is None:
                    kind0[name] = "served" if name in served else "absent"
                    continue
                kind0[name] = "found"
                n, d = imports_of(p)
                import_of[name] = (p, n, d)
                met.append(name)
                meet([n, d])

    kind0 = {}
    meet(list(root_imports))
    image = images_of({n: v[1] for n, v in import_of.items()}, {n for n, k in kind0.items() if k in ("served", "root")})
    kind = {n: ("image" if n in image else "load-fails") if k == "found" else k for n, k in kind0.items()}
    order, seen = [], set()

    def walk(raw_lists):
        for raw in raw_lists:
            for name in raw:
                if name in seen or kind[name] in ("served", "absent"):
                    continue
                seen.add(name)
                if kind[name] == "image" or kind[name] == "load-fails":
                    order.append((name, import_of[name][0]))
                if kind[name] == "image":
                    walk([import_of[name][1], import_of[name][2]])

    walk(list(root_imports))
    return order, kind
