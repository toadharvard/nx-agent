#!/usr/bin/env python3
"""udt_table - the kind (class | struct | union) of every named record type, from the CodeView type tables of MSVC objects (stage tool, pure).

    udt_table.py OUT LABEL=DIR [LABEL=DIR ...]

Inputs: each DIR is a set of cl objects (every `*.obj` below it, recursively); the objects of one LABEL are one set (for example the /Z7 build of
the prepared tree, the upstream pristine build). The one reader of `.debug$T` is link/codeview.py (Table.complete_kinds); this tool parses nothing.

RULE (owner: L2 lead, S23): the kind of a name is the kind of its COMPLETE records. A record with the forward-reference bit does not vote. Names whose
complete records all have one kind get that kind; names whose complete records disagree (across objects or sets) are conflicts and get no kind.
A name that appears only as a forward reference has no kind here (it never reaches the table; the rule that consumes the table reports it).

Output OUT/: udt_kinds.tsv (`name<TAB>kind`, sorted: the names with one kind), udt_conflicts.tsv (`name<TAB>kinds<TAB>object count<TAB>first 3 objects`),
udt_summary.json (per set: objects, with and without `.debug$T`, objects that do not parse; names, by kind, conflicts). An object that does not parse is
an error (exit 2), never skipped."""
import glob
import json
import multiprocessing
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'link'))
import coffobj  # noqa: E402
import codeview  # noqa: E402


def scan(path):
    """-> (path, number of .debug$T sections, [(name, kind)]) or (path, -1, message)"""
    try:
        blobs = list(coffobj.read(path).blobs(b'.debug$T'))
        out = set()
        for b in blobs:
            out.update(codeview.Table(b, path).complete_kinds())
        return path, len(blobs), sorted(out)
    except (ValueError, OSError, codeview.CVError) as e:
        return path, -1, '%s' % e


def main(argv):
    if len(argv) < 2:
        sys.stderr.write(__doc__ + '\n')
        return 2
    out, sets = argv[0], []
    for a in argv[1:]:
        label, _, d = a.partition('=')
        sets.append((label, d))
    kinds, summary, bad = {}, {}, []
    with multiprocessing.Pool() as pool:
        for label, d in sets:
            files = sorted(glob.glob(os.path.join(d, '**', '*.obj'), recursive=True))
            with_t = without = 0
            for path, n, res in pool.imap(scan, files, chunksize=8):
                if n < 0:
                    bad.append('%s: %s' % (path, res))
                    continue
                if n:
                    with_t += 1
                else:
                    without += 1
                for name, kind in res:
                    kinds.setdefault(name, {}).setdefault(kind, []).append(os.path.relpath(path, d))
            summary[label] = {'objects': len(files), 'with_debug_T': with_t, 'without_debug_T': without}
    if bad:
        sys.stderr.write('udt_table: %d objects do not parse:\n%s\n' % (len(bad), '\n'.join(bad[:20])))
        return 2
    os.makedirs(out, exist_ok=True)
    one, conf = [], []
    for name in sorted(kinds):
        k = kinds[name]
        if len(k) == 1:
            one.append((name, next(iter(k))))
        else:
            objs = sorted(o for v in k.values() for o in v)
            conf.append((name, ','.join(sorted(k)), len(objs), ' '.join(objs[:3])))
    with open(os.path.join(out, 'udt_kinds.tsv'), 'w') as f:
        f.write(''.join('%s\t%s\n' % r for r in one))
    with open(os.path.join(out, 'udt_conflicts.tsv'), 'w') as f:
        f.write(''.join('%s\t%s\t%d\t%s\n' % r for r in conf))
    by = {}
    for _, k in one:
        by[k] = by.get(k, 0) + 1
    summary['names'] = len(kinds)
    summary['with_one_kind'] = len(one)
    summary['by_kind'] = by
    summary['conflicts'] = len(conf)
    with open(os.path.join(out, 'udt_summary.json'), 'w') as f:
        json.dump(summary, f, sort_keys=True, indent=1)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
