"""The inputs of the byte-buffer tests (fnvlift/inputs.py): the declared x86 system record is the tracked file platform/declared/x86sys.json (the platform root's X86SYS, byte for byte), found from this file's own place in the checkout. A buffer is no module, so the universe and the depth facts are empty files; the fp-state directory has no invariants.tsv (the lookups answer "no fact file", so those forms are Unsupported)."""
import os, sys, tempfile
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LIFT)
X86SYS = os.path.join(os.path.dirname(LIFT), 'platform', 'declared', 'x86sys.json')


def configure():
    from fnvlift import inputs
    d = tempfile.mkdtemp(prefix='lifter-inputs-')
    open(d + '/universe.tsv', 'w').close(); open(d + '/depth.tsv', 'w').close()
    inputs.configure(universe=d + '/universe.tsv', fpstate=d, fpdepth=d + '/depth.tsv', x86sys=X86SYS)
