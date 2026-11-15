#!/usr/bin/env python3
"""packentries.py: the trailer is the sha256 of the BRIDGE_TSV bytes; a pack whose own trailer differs (generated against another table) is red; the rows pass through."""
import hashlib
import os
import subprocess
import sys
import tempfile

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'packentries.py')


def run(rows_trailer, bridge):
    with tempfile.TemporaryDirectory() as t:
        e, b, o = (os.path.join(t, n) for n in ('e', 'b', 'o'))
        open(e, 'w').write(rows_trailer); open(b, 'wb').write(bridge)
        r = subprocess.run([sys.executable, '-I', TOOL, e, b, o], capture_output=True, text=True)
        return r.returncode, (open(o).read() if os.path.exists(o) else None), r.stderr


def main():
    bad = []
    br = b'hdr\nrow 1\n'
    tid = hashlib.sha256(br).hexdigest()
    rows = '00401000\tfoo\tcode\n00401010\tbar\timport\n'
    rc, out, _ = run(rows + '# table_id %s\n' % tid, br)
    if rc or out != rows + '# table_id %s\n' % tid: bad.append('matching trailer: rc %s out %r' % (rc, out))
    rc, out, err = run(rows + '# table_id %s\n' % ('0' * 64), br)
    if rc == 0 or 'another bridge table' not in err: bad.append('mismatching trailer accepted: rc %s' % rc)
    rc, out, err = run(rows, br)
    if rc == 0: bad.append('missing trailer accepted')
    for b in bad: print('FAIL', b)
    print('FAIL' if bad else 'PASS', 'packentries')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
