#!/usr/bin/env python3
"""allocsites - the allocation sites of the image set: calls of an allocator import and, by return flow, of every function that returns one (L1 CONTRACT).

    allocsites.py --images-file images.tsv --bridge bridge.tsv --out allocsites.tsv

Allocator imports (Win32 as documented, size argument index): kernel32 HeapAlloc 2, HeapReAlloc 3, VirtualAlloc 1, LocalAlloc 1, GlobalAlloc 1.
Rule (closure).  A function is an ALLOCATOR if the value in eax at one of its `ret` instructions flows (argflow.Flow, backward over the
function) from the result of a call of an allocator import or of an allocator, or it tail-jumps to an allocator.  It is `pure` when every
leaf of every returned value is such a call result, `mixed` when other leaves (NULL, a parameter, ...) or a break are present (listed, not
dropped).  Allocators are found by a fixpoint over the call graph from the import call sites; nothing is identified by name or signature.
Every direct call of an allocator is an allocation site.  A function whose address is taken is listed `address-taken`: its indirect
callers are not sites (a stated gap, counted).
The size of a site: for a call of an import, the value set of its size argument (argflow.derive_arg); for a call of an allocator whose
inner size argument is exactly its own parameter i (every leaf is param (itself, i)), the value set of argument i at that call site.  A
single immediate is `const_size`, anything else `-` (open).
Output (fnvnx-allocsites/1, sorted): `image site_va allocator_import const_size field_inits`, allocator_import = the import the flow starts
from (`HeapAlloc`) or `via <allocator entry>`, field_inits `-` (not derived).  Header lines `#` list the closure: allocators (pure/mixed),
address-taken allocators and the gap (callers through function pointers).  Exe-derived: container and fnv-nx-build only."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argflow
import gpa_names

ALLOCATORS = {'HeapAlloc': 2, 'HeapReAlloc': 3, 'VirtualAlloc': 1, 'LocalAlloc': 1, 'GlobalAlloc': 1}


def size_of(res):
    """('const', v) for a closed set that is one immediate, else ('open', why)."""
    if res.breaks or not res.leaves:
        return None
    vals = {l[1] for l in res.leaves if l[0] in ('imm', 'ord')}
    if len(vals) == 1 and all(l[0] in ('imm', 'ord') for l in res.leaves):
        return next(iter(vals))
    return None


def param_of(res, ent):
    """i if every leaf of the (unexpanded) value is parameter i of the function ent, else None."""
    ps = {l[1][1] for l in res.leaves if l[0] == 'param' and l[1][0] == ent}
    if res.breaks or len(ps) != 1 or any(l[0] != 'param' or l[1][0] != ent for l in res.leaves):
        return None
    return next(iter(ps))


def run(img):
    """-> (rows [(site, allocator text, size or None)], allocators {entry: (class, root)}, taken [entry], gap count)"""
    sites = {}                       # call va -> (root import, inner callee tag)
    for name in sorted(ALLOCATORS):
        calls, _ = argflow.imports_called(img, name)
        for c in calls:
            sites[c] = (name, None)
    funcs = {}                       # entry -> [class, root, size param or None, inner site]
    work = sorted(sites)
    done_fn = set()
    while work:
        c = work.pop()
        for e in argflow.entries_for(img, c):
            if e in done_fn or e not in img.ranges:
                continue
            fn = img.func(e)
            pure, other, found = True, False, False
            roots = set()
            for va in sorted(fn.nodes):
                if fn.nodes[va].mnemonic != 'ret':
                    continue
                r = argflow.Flow(img, fn, 0, None).run(va, ('reg', 'eax'))
                if r.breaks:
                    other = True
                for kind, val, chain in sorted(r.leaves, key=repr):
                    if kind == 'ret' and val in sites:
                        found = True
                        roots.add(sites[val][0])
                    else:
                        other = True
            if not found:
                continue
            done_fn.add(e)
            funcs[e] = ['mixed' if other else 'pure', sorted(roots)[0] if roots else '-']
            for site, how in img.callers(e):
                if how == 'call':
                    if site not in sites:
                        sites[site] = (funcs[e][1], e)
                        work.append(site)
                else:                # a tail jump: its function returns the allocator's result
                    work.append(site)
                    sites.setdefault(site, (funcs[e][1], e))
    taken = sorted(e for e in funcs if img.address_taken(e))
    rows = []
    for c in sorted(sites):
        root, via = sites[c]
        i = img.insn(c)
        if i is None or i.mnemonic != 'call':
            continue
        if via is None:
            k = ALLOCATORS[root]
            res = argflow.derive_arg(img, c, k)
            rows.append((c, root, size_of(res)))
        else:
            # the size parameter of the allocator `via`: its inner site's argument must be exactly one of its own parameters
            kp = None
            for inner, (r2, v2) in sorted(sites.items()):
                if v2 is None and argflow.entries_for(img, inner) == [via]:
                    k = ALLOCATORS[r2]
                    res = argflow.Flow(img, img.func(via), 0, None).run(inner, ('sk', 4 * k))
                    kp = param_of(res, via)
                    break
            rows.append((c, 'via %x' % via, size_of(argflow.derive_arg(img, c, kp)) if kp is not None else None))
    return rows, funcs, taken, sum(len(img.address_taken(e) or ()) for e in taken)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--images-file', required=True)
    ap.add_argument('--bridge')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    images = gpa_names.load_images(gpa_names.specs_of(a.images_file), a.bridge, (), None, True)
    out, head = [], []
    for nm in sorted(images):
        rows, funcs, taken, gap = run(images[nm])
        for e in sorted(funcs):
            head.append('# allocator\t%s\t%x\t%s\t%s' % (nm, e, funcs[e][0], funcs[e][1]))
        for e in taken:
            head.append('# address-taken allocator\t%s\t%x' % (nm, e))
        head.append('# image\t%s\tsites %d\tconst %d\tallocators %d\taddress-taken %d' % (nm, len(rows), sum(1 for r in rows if r[2] is not None), len(funcs), len(taken)))
        out += [(nm, c, root, '0x%x' % s if s is not None else '-') for c, root, s in rows]
    out.sort(key=lambda r: (r[0], r[1]))
    with open(a.out, 'w') as fh:
        fh.write('# fnvnx-allocsites/1\n' + '\n'.join(head) + '\n' + 'image\tsite_va\tallocator_import\tconst_size\tfield_inits\n')
        for nm, c, root, s in out:
            fh.write('%s\t0x%x\t%s\t%s\t-\n' % (nm, c, root, s))
    print('allocsites: %d sites' % len(out))


if __name__ == '__main__':
    main()
