#!/usr/bin/env python3
"""The one inliner of the lifted pack's own headers (the pack's include directory `inc/`), a stage of lift/pack (packstage.py inc):
    prelude.py NAME OUT DIR...      NAME in INC, OUT the file, DIR... the directories the `#include "x"` lines are resolved in, in order
INC holds the generated headers of the pack and the file of the runtime each is the inlined text of:
    fnvl_prelude.h   rt/fnvl.h inlined, in the include guard FNVL_PRELUDE_H (the prelude of every lifted unit and of imp.cpp)
    imp.h            rt/imp.h inlined (the import record types imp.cpp and the runtime share)
inline(dirs, name): the text of the first dir/name that exists with every line `#include "x"` replaced by the (recursively inlined) text of x, found in the same
directories; a line `#include <x>` stays. A name that no directory holds is an error (the stage mounts only its declared inputs, so a header outside them cannot be read).
closure(dirs, name): the files inline() reads, as (dir, name) pairs, for the files that exist in dirs; an include that no directory holds is skipped (it is a generated
header of another root's output, mounted in the stage's last directory, so the caller names that directory in the stage and not in the closure).
There is no other copy of the runtime's headers in the pack: the external holds only what does not depend on the runtime (the bodies u/, entries.tsv, the lists)."""
import os
import sys

INC = {'fnvl_prelude.h': ('fnvl.h', 'FNVL_PRELUDE_H'), 'imp.h': ('imp.h', None)}


def find(dirs, name):
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def inline(dirs, name):
    p = find(dirs, name)
    if p is None:
        raise SystemExit('prelude: %s is in none of %s' % (name, ' '.join(dirs)))
    with open(p, newline='') as f:
        return ''.join(inline(dirs, l.split('"')[1]) if l.startswith('#include "') else l for l in f.read().splitlines(True))


def closure(dirs, name, seen=None):
    seen = {} if seen is None else seen
    p = find(dirs, name)
    if p is None or p in seen:
        return seen
    seen[p] = (next(d for d in dirs if p.startswith(d + os.sep)), name)
    with open(p, newline='') as f:
        for l in f.read().splitlines(True):
            if l.startswith('#include "'):
                closure(dirs, l.split('"')[1], seen)
    return seen


def text(name, dirs):
    src, guard = INC[name]
    body = inline(dirs, src)
    return body if guard is None else '#ifndef %s\n#define %s\n%s\n#endif\n' % (guard, guard, body)


def main(argv):
    if len(argv) < 3 or argv[0] not in INC:
        sys.stderr.write(__doc__ + '\n'); return 2
    with open(argv[1], 'w', newline='') as f:
        f.write(text(argv[0], argv[2:]))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
