#!/usr/bin/env python3
"""Real-data check of the import-key table (exe-derived inputs; outputs only under --work, never in the repository).

usage: iat_check.py --bridge bridge.tsv --facts facts.json --iat iat.tsv --work DIR

Generates keys.tsv and import_keys.c with contract/iat.py into DIR, compiles them with loader/src/importkey.c and a
driver, and looks every import slot of the exe up the way the loader does (dll and name as spelled in the image, or
the ordinal).  For every slot of iat.tsv (kind key or data) the C value must equal the value column; the slots the
table does not resolve are listed by count (expected: module slots, which are not platform functions).
"""
import argparse, os, subprocess, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')
sys.path.insert(0, os.path.join(ROOT, 'contract'))
import iat, store

DRIVER = r'''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fnvnx_importkey.h"
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    char ln[512]; FILE *f = fopen(argv[1], "r");
    while (fgets(ln, sizeof ln, f)) { /* slot dll name */
        char *dll = strchr(ln, '\t'), *name, *nl;
        if (!dll) return 2;
        *dll++ = 0; name = strchr(dll, '\t'); *name++ = 0;
        if ((nl = strchr(name, '\n'))) *nl = 0;
        printf("%s\t%x\n", ln, name[0] == '#' ? fnvnx_import_key(dll, NULL, (uint32_t)strtoul(name + 1, NULL, 10)) : fnvnx_import_key(dll, name, FNVNX_BY_NAME));
    }
    return 0;
}
'''


def main():
    ap = argparse.ArgumentParser()
    for n in ('bridge', 'facts', 'iat', 'work'):
        ap.add_argument('--' + n, required=True)
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    w = lambda n: os.path.join(a.work, n)
    p = subprocess.run([sys.executable, '-I', os.path.join(ROOT, 'contract', 'iat.py'), '--facts', a.facts, '--bridge', a.bridge,
                        '--out', w('iat.regen.tsv'), '--keys-out', w('keys.tsv'), '--c-out', w('import_keys.c')], capture_output=True, text=True)
    print(p.stdout.splitlines()[0] if p.stdout else p.stderr[-300:])
    rows, _ = iat.bridge_rows(a.bridge)
    imports = store.load(a.facts, 'facts')['imports']
    with open(w('slots.tsv'), 'w') as fh:
        for slot, (dll, name) in sorted(imports.items()):
            fh.write('%08x\t%s\t%s\n' % (slot, dll, name))
    open(w('drv.c'), 'w').write(DRIVER)
    subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-I' + os.path.join(ROOT, 'loader', 'include'), w('drv.c'), w('import_keys.c'),
                    os.path.join(ROOT, 'loader', 'src', 'importkey.c'), '-o', w('drv')], check=True)
    got = {}
    for ln in subprocess.run([w('drv'), w('slots.tsv')], capture_output=True, text=True, check=True).stdout.splitlines():
        s, v = ln.split('\t')
        got[s] = int(v, 16)
    want = {}
    with open(a.iat) as fh:
        for ln in fh:
            s, dll, name, kind, v = ln.rstrip('\n').split('\t')
            want[s] = (kind, v)
    mism = [s for s, (k, v) in sorted(want.items()) if k in ('key', 'data') and (v == '' or got.get(s) != int(v, 16)) and not (k == 'data' and v == '')]
    unresolved = [s for s in got if got[s] == 0]
    regen = open(w('iat.regen.tsv')).read().splitlines()
    ref = open(a.iat).read().splitlines()
    nb = sum(1 for r in rows if r['conv'] != 'data')
    print('bridge rows %d, keys %d, data rows %d (without object %d)' % (len(rows), nb, len(rows) - nb, sum(1 for r in rows if r['conv'] == 'data' and r['native_symbol'] == '-')))
    print('exe slots %d, resolved %d, unresolved %d, in iat.tsv %d, mismatches %d, regenerated iat.tsv == given: %s' % (
        len(got), len(got) - len(unresolved), len(unresolved), len(want), len(mism), regen == ref))
    print('unresolved not in iat.tsv: %d' % len([s for s in unresolved if s not in want]))
    sys.exit(1 if mism or [s for s in unresolved if s in want] else 0)


if __name__ == '__main__':
    main()
