"""The inputs of the byte-buffer tests (fnvlift/inputs.py): argv[1] is the platform root's X86SYS output (a declared input of the test root). A buffer is no module, so the universe and the depth facts are empty files; the fp-state directory has no invariants.tsv (the lookups answer "no fact file", so those forms are Unsupported)."""
import os, sys, tempfile
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LIFT)


def configure(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) < 2:
        sys.exit('usage: %s X86SYS.json   (the X86SYS output of the platform root)' % os.path.basename(argv[0]))
    x = argv.pop(1)
    from fnvlift import inputs
    d = tempfile.mkdtemp(prefix='lifter-inputs-')
    open(d + '/universe.tsv', 'w').close(); open(d + '/depth.tsv', 'w').close()
    inputs.configure(universe=d + '/universe.tsv', fpstate=d, fpdepth=d + '/depth.tsv', x86sys=x)
