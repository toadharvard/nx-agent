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
DECLARED FILE SET (CLAUDE.md "DirectX redist installed"): the files the declared machine holds are the directories DECLARED_ROOTS, in this order: the game
directory (contract_inputs.json external `exe`) and gamedata/redist (external `redist`, the DLLs the installer's DirectX redistributable puts in the declared
system32). A class: every file of those directories, no name list. This is the only place that names them; images.py, modules.py and gate_contract
take the directories from declared_roots(), nobody passes a directory list of their own. Two audit lists come with it and are reported, never decided here
(conflicts()): a file of the set whose name has a bridge row (the bridge rule above applies: the platform serves it), and a name found in more than one root
(the first root in DECLARED_ROOTS order is the one FOUND uses).
Pure: the caller supplies imports_of(path) -> (normal names, delay-load names) and find(name) -> path | None."""


DECLARED_ROOTS = ("exe", "redist")


def declared_roots(hosts):
    """hosts: {root name: host directory}.  -> [(name, directory)] of the declared file set in rule order.  The names must be exactly DECLARED_ROOTS."""
    if sorted(hosts) != sorted(DECLARED_ROOTS):
        raise ValueError("the declared file set is %s; got %s" % (", ".join(DECLARED_ROOTS), ", ".join(sorted(hosts)) or "nothing"))
    return [(n, hosts[n]) for n in DECLARED_ROOTS]


RULE_DECLARED_MACHINE = ("declared-machine module: its own file cannot load on the declared machine (normal import closure needs a dll that is neither an image, "
                         "bridged nor the root), the platform serves it with the documented unavailable result; never a guest image; owner order of 2026-11-11 23:00 (Galaxy is not lifted)")
RULE_APPDIR = "LoadLibrary search order: the application directory before system32 (Windows docs, Wine 9.0 LdrLoadDll), the first root in DECLARED_ROOTS order is loaded, the later copies are shadowed"
# a bridge-named file that does load is decided by the N1-N4 native-vs-lifted rule (bodysel.py); until that verdict exists for it the row is UNRESOLVED


def bridge_roles(file_imports, image, ok):
    """file_imports: {bridge-named dll that has a file: its normal import names (iat.norm_dll)}, image: the image set (images_of), ok: names that are
    satisfied without being images (bridge dlls, the root).  -> {name: 'declared-machine' | 'loadable'}: declared-machine iff some import of the
    file is neither in image nor in ok (the file itself fails the image rule), else loadable (N1-N4 decides)."""
    return {n: ("loadable" if all(x in image or x in ok for x in imps) else "declared-machine") for n, imps in file_imports.items()}


def conflicts(files, roles):
    """files: {root name: [file names of that root]}, roles: bridge_roles() of the bridge-named files that exist (the keys are all names with a bridge row
    that have a file).
    -> (bridge_named, collisions, unresolved): every row is (dll name, [roots in rule order], rule text).
    bridge_named: the *.dll files of the set that have a bridge row, with RULE_DECLARED_MACHINE when their role is declared-machine; a loadable one has
    rule None and is in unresolved until N1-N4 gives a verdict for it.
    collisions: the *.dll names found in more than one root (case-insensitive), RULE_APPDIR; a root outside DECLARED_ROOTS leaves rule None (unresolved).
    unresolved: every row whose rule is None; the caller makes the build red on a non-empty list."""
    where = {}
    for root, names in files.items():
        for f in names:
            n = f.lower()
            if n.endswith(".dll") and root not in where.setdefault(n, []):
                where[n].append(root)
    order = {r: i for i, r in enumerate(DECLARED_ROOTS)}
    key = lambda v: sorted(v, key=lambda r: order.get(r, len(order)))
    bn = sorted((n, key(r), RULE_DECLARED_MACHINE if roles.get(n) == "declared-machine" else None) for n, r in where.items() if n in roles)
    co = sorted((n, key(r), RULE_APPDIR if all(x in order for x in r) else None) for n, r in where.items() if len(r) > 1)
    un = [x for x in bn + co if x[2] is None]
    return bn, [x for x in co if x[2]], un


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
