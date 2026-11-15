"""The inputs of the byte-buffer tests (fnvlift/inputs.py): argv[1] is the platform root's X86SYS output (a declared input of the test root). A buffer is no module, so the universe is
empty (None, stated); there is no fp-state fact (an empty directory: the invariant lookups answer "no fact file", so those forms are Unsupported) and no depth fact."""
import os, sys, tempfile
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LIFT)


def configure(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) < 2:
        sys.exit('usage: %s X86SYS.json   (the X86SYS output of the platform root)' % os.path.basename(argv[0]))
    x = argv.pop(1)
    from fnvlift import inputs
    inputs.configure(universe=None, fpstate=tempfile.mkdtemp(prefix='fpstate-'), fpdepth=None, x86sys=x)
