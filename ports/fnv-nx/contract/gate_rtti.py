#!/usr/bin/env python3
"""gate_rtti - the game's code uses the exe's RTTI only (MASTER_PLAN4 K16 under per-unit names).

    gate_rtti.py --objects <dir of game objects> --readelf <aarch64 readelf>

`dynamic_cast` and `typeid` go through the exe's RTTI at vptr[-4] (`__RTDynamicCast`, `__RTtypeid`), as in the
exe.  GCC's own RTTI is bound by name across units, which the link no longer does.  So in every code section
of every game object there must be
  0 relocations naming `_ZTI*` or `_ZTS*`,
  0 calls of `__dynamic_cast`, `__cxa_bad_cast`, `__cxa_bad_typeid` or `std::type_info::operator==` / `before`.
Exit status 1 otherwise.  Counts only on stdout.
Gate file `rtti` ($GATE_OUT/rtti.json, gatefile.py): universe = the objects, certified = the objects without a row,
uncertified = the basenames of the others (sorted).
"""
import argparse, collections, glob, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gatefile

SEC = re.compile(r"Relocation section '\.rela(\.text[^']*)'")
CALLS = ('__dynamic_cast', '__cxa_bad_cast', '__cxa_bad_typeid', '_ZNKSt9type_infoeqERKS_', '_ZNKSt9type_info6beforeERKS_')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--objects', required=True)
    ap.add_argument('--readelf', required=True, help='path of the aarch64 readelf (no PATH lookup)')
    a = ap.parse_args()
    files = sorted(glob.glob(os.path.join(a.objects, '*.o')))
    c = collections.Counter()
    objs = set()
    for i in range(0, len(files), 100):
        out = subprocess.run([a.readelf, '-rW'] + files[i:i + 100], capture_output=True, text=True).stdout
        code = False
        cur = ''
        for ln in out.splitlines():
            if ln.startswith('File: '):
                cur = ln[6:]
            elif ln.startswith('Relocation section'):
                code = bool(SEC.match(ln))
            elif code and ln[:1] in '0123456789abcdef':
                p = ln.split()
                if len(p) < 5:
                    continue
                name = p[4]
                if name.startswith(('_ZTI', '_ZTS')) and 'GOT_PAGE' not in p[2]:
                    c['code references to _ZTI/_ZTS'] += 1
                    objs.add(cur)
                elif name in CALLS and ('CALL26' in p[2] or 'JUMP26' in p[2]):
                    c['calls of ' + name] += 1
                    objs.add(cur)
    for k, v in sorted(c.items()):
        print('%7d  %s' % (v, k))
    print('%7d  objects with a row (of %d)' % (len(objs), len(files)))
    gatefile.write('rtti', len(files), len(files) - len(objs), [os.path.basename(o) for o in sorted(objs)],
                   '; '.join('%d %s' % (v, k) for k, v in sorted(c.items())))
    sys.exit(1 if c else 0)


if __name__ == '__main__':
    main()
