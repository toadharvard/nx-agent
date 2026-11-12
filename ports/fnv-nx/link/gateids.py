"""gateids - the full list of the uncertified ids of a gate, next to its gate file (gatefile.py keeps only the first 50).

    write_ids(name, ids)  ->  $GATE_OUT/<name>.ids: one id per line, sorted, empty when the gate has none; nothing if GATE_OUT is unset

The file is a function of its arguments.  Like the ids in the gate file, it may name game data: it stays in $GATE_OUT, never in the tree.
"""
import os


def write_ids(name, ids):
    out = os.environ.get('GATE_OUT')
    if not out:
        return None
    path = os.path.join(out, name + '.ids')
    with open(path, 'w', newline='\n') as fh:
        fh.write(''.join(i + '\n' for i in sorted(str(x) for x in ids)))
    return path
