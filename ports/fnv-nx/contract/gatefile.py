"""gatefile - the result file of a gate, in the shape the L4 merge verdict reads (verify/verdict.py, `fnvnx-gate/1`).

    write(name, universe, certified, uncertified_ids, detail)  ->  $GATE_OUT/<name>.json, or nothing if GATE_OUT is unset

    {"certified":C,"detail":"...","format":"fnvnx-gate/1","name":N,"status":"green"|"red","uncertified":[ids],"uncertified_n":N,"universe":U}

status is green exactly when certified == universe (completeness of certificates, not a run counter); `uncertified`
is every uncertified id, sorted (strings), and `uncertified_n` its count, so the file is a function of its arguments and gives the whole set.  Ids of game data are written
to $GATE_OUT only, never committed.  The only environment variable read is the declared GATE_OUT.
"""
import json
import os

FORMAT = 'fnvnx-gate/1'


def write(name, universe, certified, uncertified_ids, detail):
    out = os.environ.get('GATE_OUT')
    if not out:
        return None
    r = dict(format=FORMAT, name=name, status='green' if certified == universe else 'red', universe=int(universe),
             certified=int(certified), uncertified=sorted(str(x) for x in uncertified_ids), uncertified_n=len(list(uncertified_ids)), detail=str(detail))
    path = os.path.join(out, name + '.json')
    with open(path, 'w', newline='\n') as fh:
        json.dump(r, fh, sort_keys=True, separators=(',', ':'))
        fh.write('\n')
    return path
