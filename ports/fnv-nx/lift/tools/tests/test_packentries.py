#!/usr/bin/env python3
"""packentries.py: the trailer is the sha256 of the BRIDGE_TSV bytes; import rows (impgen's output) are merged into the external's rows by address; an impgen output of another table,
an import row inside the external and an address in both are red. The SAME external with two different bridge tables gives two entry lists that differ only in the import rows
and the trailer (the external is independent of the loader table)."""
import hashlib
import os
import subprocess
import sys
import tempfile

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'packentries.py')


def run(ext, imp, bridge):
    with tempfile.TemporaryDirectory() as t:
        e, i, b, o = (os.path.join(t, n) for n in ('e', 'i', 'b', 'o'))
        open(e, 'w').write(ext); open(i, 'w').write(imp); open(b, 'wb').write(bridge)
        r = subprocess.run([sys.executable, '-I', TOOL, e, i, b, o], capture_output=True, text=True)
        return r.returncode, (open(o).read() if os.path.exists(o) else None), r.stderr


def tid(b): return hashlib.sha256(b).hexdigest()


def main():
    bad = []
    ext = '00401000\tL_00401000\tcode\n000fffd0\tfnvl_x\truntime\n'
    b1, b2 = b'hdr\nrow 1\n', b'hdr\nrow 1\nrow 2\n'
    i1 = '# table_id %s\n00100000\tI_00100000\tk\tn\tsym\n' % tid(b1)
    i2 = '# table_id %s\n00100000\tI_00100000\tk\tn\tsym\n00100010\tI_00100010\tk\tm\tsym\n' % tid(b2)
    rc, o1, _ = run(ext, i1, b1)
    if rc or o1 != '000fffd0\tfnvl_x\truntime\n00100000\tI_00100000\timport\n00401000\tL_00401000\tcode\n# table_id %s\n' % tid(b1):
        bad.append('merge with table 1: rc %s out %r' % (rc, o1))
    rc, o2, _ = run(ext, i2, b2)
    if rc or o2 is None: bad.append('merge with table 2: rc %s' % rc)
    else:
        code = lambda o: [l for l in o.splitlines() if l.endswith(('code', 'runtime'))]
        if code(o1) != code(o2) or o1 == o2: bad.append('the external rows must be equal and the import rows and trailer must differ between the two tables')
    rc, out, err = run(ext, i1, b2)
    if rc == 0 or 'another bridge table' not in err: bad.append('impgen output of another table accepted: rc %s' % rc)
    rc, out, err = run('00100000\tI_00100000\timport\n', i1, b1)
    if rc == 0 or 'import row in the external' not in err: bad.append('import row in the external accepted: rc %s' % rc)
    rc, out, err = run('00100000\tL_x\tcode\n', i1, b1)
    if rc == 0 or 'row of the external and an import row' not in err: bad.append('address in both accepted: rc %s' % rc)
    for b in bad: print('FAIL', b)
    print('FAIL' if bad else 'PASS', 'packentries')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
