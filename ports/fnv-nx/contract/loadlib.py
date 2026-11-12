#!/usr/bin/env python3
"""loadlib - the image set and the module names every LoadLibrary call of the images can load (L1 CONTRACT).

    loadlib.py --exe E --dir D ... --ranges-exe R --ranges-module 'F/%s/ranges.tsv' --bridge bridge.tsv [--sites dynsites.tsv] --out DIR

One rule over the facts of the images (the same argflow.Image and may-write rule as gpa_names.py):
  site     every reference of an imported LoadLibraryA/W/ExA/ExW slot (argflow.uses_of_slot_ref: call [slot], call thunk, call reg
           after mov reg,[slot]); its address is the address of the call instruction, the site L5's dynsites.tsv names (one row per call; a dynsites row whose site is not a call of its API is an item of the gate, `argflow.site_api`; nothing is converted).
  names    the value set of argument 0 of the calls that use the site (argflow.derive_arg): a string constant leaf is a name
           (norm_dll'd); any other leaf, a break, or an escape of the slot reference makes the site open.  A closed site
           loads exactly its names (a name no file answers makes LoadLibrary fail: no module).
  set      images.image_set over the loads (fixpoint): members with range facts are derived once, their closed names enter the
           walk and can add members; the set only grows.  An open site may load any file of the declared set (--dir), so its image
           loads every one of them.  The set is then checked against the pinned one (exepin.IMAGES, digests; a difference exits 2).  A member without range facts has no row (its sites are unknown).
Outputs: loadlib.tsv (module, site, calls, api, status closed|open, names, breaks), images.tsv (name, pe, ranges or '-'), and the
gate file `loadlib`: universe = sites + disagreements, certified = closed sites.
Agreement with L5's dynsites.tsv (--sites): the LoadLibrary rows of every module that has a derivation must be exactly the sites found
here; a site in only one of the two lists is uncertified (the two finders are one rule or the gate is red)."""
import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argflow
import gatefile
import dllkind
import exepin
import gpa_names
import iat
import images


def api_list():
    return sorted(a for a in argflow.LOADERS if not a.startswith('GetModuleHandle'))


def derive(img):
    """[(site, calls, api, status, [names], [breaks])] of the images' LoadLibrary calls, sorted by site.  A site is the address of the
    call instruction (the convention of dynsites.tsv): one row per call found through the uses of an import slot
    (argflow.uses_of_slot_ref); an escape of the slot value (a use that is not a call) is a row at the instruction that escapes,
    open.  calls is the site again (one call per row)."""
    rows = {}
    for api in api_list():
        slots = sorted(s for s, (d, n) in img.imports.items() if n == api)
        wide = argflow.LOADERS[api] == 'w'
        for ref in sorted(r for r in img.relocs if img.word(r) in slots):
            calls, open_ = argflow.uses_of_slot_ref(img, ref)
            for va, why in sorted(set(open_), key=repr):
                rows.setdefault((va, api), ([], ['%x %s' % (va, why)]))
            for call in sorted(set(calls)):
                names, brk = rows.setdefault((call, api), ([], []))
                r = argflow.derive_arg(img, call, 0)
                brk += sorted('%x %s' % b if isinstance(b, tuple) else str(b) for b in r.breaks)
                for kind, val, chain in r.sorted_leaves():
                    if kind == 'str' and val[1 if wide else 0] is not None:
                        names.append(iat.norm_dll(val[1 if wide else 0]))
                    else:
                        brk.append('%x name argument is %s %r' % (call, kind, val))
    out = []
    for (site, api), (names, brk) in sorted(rows.items()):
        brk = sorted(dict.fromkeys(brk))
        out.append((site, [site], api, 'closed' if not brk else 'open', sorted(dict.fromkeys(names)), brk))
    return out


def validate(img, sites_text, module):
    """[(site, reason)] of the LoadLibrary rows of dynsites.tsv for `module` whose site is not a call of that API."""
    bad = []
    for line in sites_text.splitlines():
        f = line.split('\t')
        if len(f) >= 3 and f[0] != 'module' and f[0].lower() == module and f[2].startswith('LoadLibrary'):
            why = argflow.site_api(img, int(f[1], 16), f[2])
            if why:
                bad.append((int(f[1], 16), why))
    return bad


def tsv(per):
    lines = ['module\tsite\tcall\tapi\tstatus\tnames\tbreaks']
    for mod in sorted(per):
        for ref, calls, api, st, names, brk in per[mod]:
            lines.append('\t'.join([mod, '0x%x' % ref, ','.join('%x' % c for c in calls) or '-', api, st, ','.join(names) or '-', ' / '.join(brk) or '-']))
    return '\n'.join(lines) + '\n'


def agree(per, sites_text):
    """[(module, site, 'only here'|'only in dynsites')]: the LoadLibrary rows of dynsites.tsv against the sites derived here."""
    theirs = {}
    for line in sites_text.splitlines():
        f = line.split('\t')
        if len(f) >= 3 and f[0] != 'module' and f[2].startswith('LoadLibrary'):
            theirs.setdefault(f[0].lower(), set()).add(int(f[1], 16))
    diff = []
    for mod in sorted(per):
        ours = {r[0] for r in per[mod]}
        for va in sorted(ours - theirs.get(mod, set())):
            diff.append((mod, va, 'only here'))
        for va in sorted(theirs.get(mod, set()) - ours):
            diff.append((mod, va, 'only in dynsites'))
    return diff


def targets(per):
    """{module: sorted names its closed sites load} (open sites add nothing here; they are in the open list)."""
    return {m: sorted({n for r in per[m] for n in r[4]}) for m in sorted(per)}


def declared_files(dirs):
    """[normalised names] of every dll file of the declared file set DIRS (the exe's directory and the installed redist): what an open LoadLibrary site may load."""
    out = set()
    for d in dirs:
        out |= {iat.norm_dll(f) for f in os.listdir(d) if f.lower().endswith('.dll') and os.path.isfile(os.path.join(d, f))}
    return sorted(out)


def fixpoint(exe_path, dirs, bridge, ranges_of, derive_image, may_load=()):
    """The least image set under imports and resolved LoadLibrary names (images.py) and the derivation it needs.
    ranges_of(name) -> ranges path | None (the image has facts); derive_image(name, pe path, ranges path) -> derive() rows.
    -> (members [(name, pe path)], per {name: rows} for the members that have facts).  Every member is derived once; the loads of a
    derived member can add members, whose loads can add more; the set only grows (loads only grow), so the loop ends within the
    number of dll files.  A member without facts has no row (its sites are unknown: the gate lists it).
    MAY_LOAD: the declared file set's names.  An open site (a name the rule cannot derive) may load any file of the declared set, so a member with
    an open site loads all of MAY_LOAD (the set stays a sound superset; classification drops bridge and declared names as for imports)."""
    per, loads = {}, {}
    while True:
        members = images.image_set(exe_path, dirs, bridge, loads)
        todo = [(n, p) for n, p in members if n not in per and ranges_of(n)]
        if not todo:
            return members, per
        for n, p in todo:
            per[n] = derive_image(n, p, ranges_of(n))
            loads[n] = sorted({x for r in per[n] for x in r[4]} | (set(may_load) if any(r[3] != 'closed' for r in per[n]) else set()))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True)
    ap.add_argument('--dir', action='append', required=True, help='where dll files are looked for (first match in list order)')
    ap.add_argument('--ranges-exe', required=True, help='ranges.tsv of the exe')
    ap.add_argument('--ranges-module', required=True, help='ranges.tsv of a module, %%s = the dll stem')
    ap.add_argument('--bridge', help='bridge.tsv (L5): its dlls are never images')
    ap.add_argument('--escape', action='append', default=[])
    ap.add_argument('--writes')
    ap.add_argument('--globext')
    ap.add_argument('--sites', help='dynsites.tsv of the platform manifest (L5)')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    me = os.path.basename(a.exe).lower()

    def ranges_of(name):
        p = a.ranges_exe if name == me else a.ranges_module % name.rsplit('.', 1)[0]
        return p if os.path.isfile(p) else None

    paths, invalid = {}, []
    sites_text = open(a.sites).read() if a.sites else ''

    def derive_image(name, pe, rg):
        paths[name] = (pe, rg)
        img = gpa_names.load_images(['%s=%s,%s' % (name, pe, rg)], a.bridge, a.escape, a.writes, False, a.globext)[name]
        invalid.extend((name, va, why) for va, why in validate(img, sites_text, name))
        return derive(img)

    bridge = {iat.norm_dll(r['dll']) for r in iat.bridge_rows(a.bridge)[0]} if a.bridge else set()
    members, per = fixpoint(a.exe, a.dir, bridge, ranges_of, derive_image, declared_files(a.dir))
    try:   # the declared machine's image set is the pinned one (contract/exepin.py IMAGES: digests of every guest image DLL): checked here, where the set is made
        exepin.check_images([(n, os.path.getsize(p), hashlib.sha256(open(p, 'rb').read()).hexdigest()) for n, p in members if n != me])
    except exepin.ExePinError as e:
        print('loadlib: %s' % e, file=sys.stderr)
        return 2
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'loadlib.tsv'), 'w', newline='\n') as fh:
        fh.write(tsv(per))
    with open(os.path.join(a.out, 'images.tsv'), 'w', newline='\n') as fh:
        fh.write('name\tpe\tranges\n' + ''.join('%s\t%s\t%s\n' % (n, p, ranges_of(n) or '-') for n, p in members))
    diff = []
    if a.sites:
        with open(a.sites) as fh:
            diff = agree(per, fh.read())
    sites = [(m, r) for m in sorted(per) for r in per[m]]
    nofacts = [n for n, _ in members if n not in per]
    openr = [(m, r) for m, r in sites if r[3] != 'closed']
    unc = ['%s@%x' % (m, r[0]) for m, r in openr] + ['%s@%x:%s' % (m, v, w) for m, v, w in diff] + ['%s@%x:%s' % x for x in invalid]
    gatefile.write('loadlib', len(sites) + len(diff) + len(invalid), len(sites) - len(openr), unc,
                   'LoadLibrary sites with a derived module name set; dynsites rows agreeing with them (images without facts: the caller lists them)')
    print('loadlib: %d images (%d without facts), %d sites, %d open, %d disagree with dynsites, %d dynsites rows are not calls of their API' % (len(members), len(nofacts), len(sites), len(openr), len(diff), len(invalid)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
