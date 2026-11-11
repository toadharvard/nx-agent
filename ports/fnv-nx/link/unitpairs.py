#!/usr/bin/env python3
"""unitpairs - the declared inputs shared by the L2 object gates (gate_names, gate_argorder, gate_r7) and their result files.

    gate_X.py --gcc PATH [--gcc PATH ...] --cl PATH [--cl PATH ...] [--units units.json]

PATH is a directory (its `*.o` for --gcc, its `*.obj` for --cl: the regular files directly in it, sorted by name) or one file
of that suffix.  The unit of an object is its stem (the name without the suffix).  A GCC object `S.o` is paired with the cl
object `S.obj`.  With --units (the prepared tree's build/units.json, {"units": [{"source": "dir/file.cpp", ...}, ...]}) a cl
object `u<N>.obj` (N canonical decimal) stands for the unit N of that list, whose stem is its source path with `/` replaced by `_` (`a/b.cpp` -> `a_b.cpp`, GCC object `a_b.cpp.o`).
Errors (GateInputError): a path that does not exist or is a file of the wrong suffix, two objects with one stem on a side,
a `u<N>` with N outside the list, two units with one stem, a units file that is not that JSON shape.  A cl object without
a GCC twin is not a unit of the gate (nothing of it is certified).

  pairs(gcc, cl, units)  -> [(stem, gcc path, cl path or None)] sorted by stem, one per GCC object
  finish(name, universe, red, detail) -> exit status; writes $GATE_OUT/<name>.json (contract/gatefile.py) and the full
      sorted list of the uncertified ids to $GATE_OUT/<name>.ids (one per line; the gate file keeps the first 50); prints detail
  fail(name, message) -> exit status: the gate file of a gate that could not read its inputs: red, universe 1, certified 0,
      the one id `error: <message>`
Exit status of a gate: 0 green, 1 red (also an input error), 2 bad command line.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import gatefile


class GateInputError(ValueError):
    pass


def _collect(paths, suffix, what):
    out = {}
    for p in paths:
        if os.path.isdir(p):
            files = [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.endswith(suffix) and os.path.isfile(os.path.join(p, f))]
        elif os.path.isfile(p):
            if not p.endswith(suffix):
                raise GateInputError('%s %s: not a %s file' % (what, p, suffix))
            files = [p]
        else:
            raise GateInputError('%s %s: no such file or directory' % (what, p))
        for f in files:
            stem = os.path.basename(f)[:-len(suffix)]
            if stem in out:
                raise GateInputError('%s: two objects with the unit name %s: %s and %s' % (what, stem, out[stem], f))
            out[stem] = f
    return out


def _units(path):
    try:
        with open(path) as fh:
            doc = json.load(fh)
        stems = [u['source'].replace('/', '_') for u in doc['units']]
        if not all(isinstance(s, str) and s for s in stems):
            raise TypeError('source')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise GateInputError('units %s: not {"units": [{"source": ...}, ...]} (%s)' % (path, e))
    if len(set(stems)) != len(stems):
        raise GateInputError('units %s: two units with one stem' % path)
    return stems


def pairs(gcc, cl, units=None):
    g = _collect(gcc, '.o', 'gcc')
    c = _collect(cl, '.obj', 'cl')
    if units is not None:
        stems = _units(units)
        renamed = {}
        for stem, path in c.items():
            if stem[:1] == 'u' and stem[1:].isascii() and stem[1:].isdigit() and str(int(stem[1:])) == stem[1:]:
                if int(stem[1:]) >= len(stems):
                    raise GateInputError('cl: %s names unit %s, the units list has %d' % (path, stem[1:], len(stems)))
                stem = stems[int(stem[1:])]
            if stem in renamed:
                raise GateInputError('cl: two objects with the unit name %s' % stem)
            renamed[stem] = path
        c = renamed
    return [(s, g[s], c.get(s)) for s in sorted(g)]


def parse_args(argv, prog, doc):
    ap = argparse.ArgumentParser(prog=prog, description=doc.split('\n')[0])
    ap.add_argument('--gcc', action='append', default=[], required=True, help='directory of GCC objects, or one .o (repeatable)')
    ap.add_argument('--cl', action='append', default=[], required=True, help='directory of cl objects, or one .obj (repeatable)')
    ap.add_argument('--units', help='build/units.json of the prepared tree: u<N>.obj stands for its unit N')
    return ap.parse_args(argv)


def _write_ids(name, ids):
    out = os.environ.get('GATE_OUT')
    if not out:
        return
    with open(os.path.join(out, name + '.ids'), 'w', newline='\n') as fh:
        fh.write(''.join(i + '\n' for i in sorted(str(x) for x in ids)))


def finish(name, universe, red, detail):
    gatefile.write(name, universe, universe - len(red), red, detail)
    _write_ids(name, red)
    print(detail)
    return 1 if red else 0


def fail(name, message):
    return finish(name, 1, ['error: ' + message], 'input error: ' + message)
