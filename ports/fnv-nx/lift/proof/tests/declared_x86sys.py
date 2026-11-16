"""The declared input of every lift/proof test that builds a Form: the tree's declared x86 system record (platform/declared/x86sys.json), passed explicitly to the one input of the lifter
(fnvlift.inputs 'x86sys' through proof.x86sys.configure).  One function for all tests (CLAUDE.md rule 2): no test restates the path or the configure call."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.normpath(os.path.join(HERE, '..', '..'))
PATH = os.path.normpath(os.path.join(LIFT, '..', 'platform', 'declared', 'x86sys.json'))
if LIFT not in sys.path:
    sys.path.insert(0, LIFT)


def configure():
    """sets the x86sys input (a second call with the same value is a no-op: fnvlift.inputs); returns the record's sha256, as proof.x86sys.configure"""
    from fnvnx.lift.proof import x86sys
    return x86sys.configure(PATH)


def shape_keys():
    """the shape universe: the keys (first column) of shapes.tsv of the mounted produced input PACK of root lift/liftsrc (tests/inputs.json; FNVNX_INPUT_PACK), the shapes the lifter built for every image.  No default: an unmounted input is an error"""
    p = os.environ.get('FNVNX_INPUT_PACK')
    if not p:
        raise SystemExit('FNVNX_INPUT_PACK is not mounted (declare the produced input PACK of root lift/liftsrc in tests/inputs.json)')
    with open(os.path.join(p, 'shapes.tsv')) as f:
        return sorted({l.split('\t')[0] for l in f if l.strip() and not l.startswith('#')})


def configure_segwrites():
    """the segwrites input (the selector sets of the code-selector store guard, core.sel_set): the mounted produced input SEGWRITES of root lift/facts (tests/inputs.json; FNVNX_INPUT_SEGWRITES).  No default: an unmounted input is an error"""
    p = os.environ.get('FNVNX_INPUT_SEGWRITES')
    if not p:
        raise SystemExit('FNVNX_INPUT_SEGWRITES is not mounted (declare the produced input SEGWRITES of root lift/facts in tests/inputs.json)')
    from fnvnx.lift.fnvlift import inputs
    inputs.configure(segwrites=p)
