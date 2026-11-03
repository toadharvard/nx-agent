"""a64prep.py - the candidate side's link step: S2's resolve/apply on a subset of AArch64 objects.

    a64prep.py objects <out objects.tsv> [--require-x86] [--max N]   list units with a finished AArch64 object
    a64prep.py compile <units.list> <obj dir> [jobs]                  compile missing units (tools/link/compile.sh)
    a64prep.py link <link dir>                                         fnvlink resolve + apply on <link dir>/objects.tsv

The objects come from the prepared tree compiled with the current flags (tools/link/compile.sh, incl. J-0034's
MSVC-like semantics). resolve binds every undefined symbol by original address exactly as for the game link
(`__fnv_<addr>`, bridges, data addresses), apply rewrites the objects; difffuzz then links one rewritten unit at a
time into a small image (a64image.py).
"""
import json
import os
import subprocess
import sys

import config

FNVLINK = config.LINKTOOLS / 'fnvlink.py'
EXTERN_TEXT = config.SCR / 's4/addr_ids.txt'


def finished(obj):
    log = obj.with_suffix('.log')
    return obj.exists() and obj.stat().st_size > 0 and (not log.exists() or log.stat().st_mtime <= obj.stat().st_mtime
                                                        + 5)


def cmd_objects(out, require_x86=True, limit=None):
    units = json.load(open(config.TREE / 'build/units.json'))['units']
    rows, seen = [], set()
    for u in units:
        src = u['source']
        if src in seen:
            continue
        seen.add(src)
        o = config.a64_object(src)
        if o is None or not finished(o):
            continue
        rows.append((src, str(o)))
    if limit:
        rows = rows[:limit]
    with open(out, 'w') as f:
        for src, o in rows:
            f.write('%s\t%s\tok\n' % (src, o))
    print('%d objects' % len(rows))


def cmd_link(d):
    d = os.path.abspath(d)
    out, fixed = os.path.join(d, 'out'), os.path.join(d, 'fixed')
    env = dict(os.environ, SSE2NEON=str(config.SSE2NEON))
    args = [sys.executable, str(FNVLINK), 'resolve', str(config.TREE / 'build/units.json'),
            os.path.join(d, 'objects.tsv'), out]
    if EXTERN_TEXT.exists():
        args += ['--extern-text', str(EXTERN_TEXT)]
    subprocess.run(args, check=True, env=env)
    subprocess.run([sys.executable, str(FNVLINK), 'apply', out, fixed], check=True, env=env)


def cmd_compile(lst, objdir, jobs='2'):
    env = dict(os.environ, SSE2NEON=str(config.SSE2NEON))
    subprocess.run(['bash', str(config.LINKTOOLS / 'compile.sh'), str(config.TREE), objdir, lst, jobs], check=True,
                   env=env)


if __name__ == '__main__':
    c = sys.argv[1]
    if c == 'objects':
        cmd_objects(sys.argv[2], limit=int(sys.argv[3]) if len(sys.argv) > 3 else None)
    elif c == 'link':
        cmd_link(sys.argv[2])
    elif c == 'compile':
        cmd_compile(*sys.argv[2:])
    else:
        print(__doc__)
