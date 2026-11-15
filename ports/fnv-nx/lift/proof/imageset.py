#!/usr/bin/env python3
"""imageset: THE image set of the lift proofs, read from the loader's module table (contract/modules.py output `modules.tsv`: every lifted module of the may-load
closure over the declared system32, with its assigned base) and nothing else. A tool of lift/proof never names an image: it takes the exe path and the table.

    load(modules_tsv)            -> [(name, file name, preferred base, assigned base)] in table order (name = the file name without `.dll`, lower case: the facts directory of the module)
    find(dirs, file_name)        -> path of the file in the first directory that holds it (names compare ASCII case-insensitively); None if no directory does
    images(exe_path, modules_tsv, dirs) -> [(name, pe path, facts subdirectory or None)]: the exe first (facts at the top of the facts directory), then the table's modules
A module whose file is in no directory is an error (the table and the gamedata must agree)."""
import os


def load(modules_tsv):
    out = []
    for ln in open(modules_tsv):
        if not ln.strip() or ln[0] == '#':
            continue
        f = ln.rstrip('\n').split('\t')
        out.append((f[0][:-4] if f[0].lower().endswith('.dll') else f[0], f[0], int(f[3], 16), int(f[4], 16)))
    return out


def find(dirs, file_name):
    for d in dirs:
        for n in sorted(os.listdir(d)):
            if n.lower() == file_name.lower():
                return os.path.join(d, n)
    return None


def images(exe_path, modules_tsv, dirs):
    out = [(os.path.splitext(os.path.basename(exe_path))[0], exe_path, None)]
    for name, fn, pref, base in load(modules_tsv):
        p = find(dirs, fn)
        if p is None:
            raise SystemExit('imageset: module %s of %s is in none of %s' % (fn, modules_tsv, dirs))
        out.append((name, p, name))
    return out
