"""shape_sha is a pure function of the Form's structure: the same in every process, whatever was built before it.
The universe is a declared input (the PACK output of lift/liftsrc, its shapes.tsv; tests/inputs.json).  Two fresh processes compute the sha of every key of it: one in sorted order, one in reversed order
(so node serials, z3 AST ids and the hash-cons table all differ); every sha must be equal.  The whole universe is compared (no sampling)."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def child(order, out):
    sys.path.insert(0, os.path.join(HERE, '..', '..'))
    sys.path.insert(0, os.path.join(HERE, '..'))
    sys.path.insert(0, HERE)
    import fnvnx.lift.proof.tests.declared_x86sys as declared_x86sys
    declared_x86sys.configure()
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'fnvlift', 'tests'))
    import fnvnx.lift.fnvlift.tests._inputs as _inputs
    _inputs.configure_declared()      # the declared-machine files the Form builders read (x86sys, cpumodel, the certified rcp table)
    from fnvnx.lift.proof import symform
    keys = declared_x86sys.shape_keys()
    sample = keys[::-1] if order == 'reversed' else keys      # the other order is the unrelated batch of every key
    with open(out, 'w') as f:
        for k in sample:
            try: r = symform.shape_sha(k)
            except Exception as e: r = 'ERR ' + type(e).__name__
            f.write('%s %s\n' % (k, r))


def main():
    d = tempfile.mkdtemp(prefix='shacanon-')
    res = {}
    for order in ('sorted', 'reversed'):
        p = os.path.join(d, order)
        subprocess.run([sys.executable, '-I', os.path.abspath(__file__), '--child', order, p], check=True)
        res[order] = dict(l.rstrip('\n').rsplit(' ', 1) if not l.split(' ', 2)[1] == 'ERR' else (l.split(' ', 1)[0], l.split(' ', 1)[1].rstrip('\n')) for l in open(p))
    a, b = res['sorted'], res['reversed']
    built = sum(1 for v in a.values() if not v.startswith('ERR'))
    diff = [k for k in a if a[k] != b.get(k)]
    print('keys %d, built %d, equal %d, differing %d %s' % (len(a), built, len(a) - len(diff), len(diff), diff[:10]))
    sys.exit(0 if built > 0 and not diff and set(a) == set(b) else 1)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--child': child(sys.argv[2], sys.argv[3])
    else: main()
