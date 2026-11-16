"""The declared input of every lift/proof test that builds a Form: the tree's declared x86 system record (platform/declared/x86sys.json), passed explicitly to the one input of the lifter
(fnvlift.inputs 'x86sys' through proof.x86sys.configure).  One function for all tests (CLAUDE.md rule 2): no test restates the path or the configure call."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.normpath(os.path.join(HERE, '..', '..'))
PATH = os.path.normpath(os.path.join(LIFT, '..', 'platform', 'declared', 'x86sys.json'))
if LIFT not in sys.path:
    sys.path.insert(0, LIFT)


def configure():
    """sets the x86sys input (a second call with the same value is a no-op: fnvlift.inputs); returns the record's sha256, as proof.x86sys.configure"""
    from proof import x86sys
    return x86sys.configure(PATH)
