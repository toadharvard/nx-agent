"""The inputs of the lifter, stated once and explicitly. RULE: no lifter module reads an environment variable or a default path; every file it reads outside
its own tree is one of the names below, set by the program that runs the lifter (a recipe stage passes /in or /uses paths; a test passes its own files) through
configure(). A name that was not configured is an error at the point of use: there is no ambient default and no silent "missing file means empty".

  universe   the entry universe (tools/universe.py output): path, or None for the empty universe (a lift of a byte buffer that is not a module)
  fpstate    the directory of the fp-state facts (invariants.tsv; tools/fpstate.py)
  fpdepth    the x87 stack-depth facts (depth.tsv; tools/fpmodes.py)
  x86sys     the declared x86 system record (platform X86SYS output)
  base       the load base the loader assigned to the module (int), or None for the preferred base of the image
Proof: a lookup is get(name); configure() accepts only these names; a second configure() of a name with another value is an error (one process, one input set)."""
NAMES = ('universe', 'fpstate', 'fpdepth', 'x86sys', 'base')
_I = {}


def configure(**kw):
    for k, v in kw.items():
        if k not in NAMES:
            raise ValueError('inputs: %r is not a lifter input (%s)' % (k, ', '.join(NAMES)))
        if k in _I and _I[k] != v:
            raise ValueError('inputs: %s is already %r; one process has one input set' % (k, _I[k]))
        _I[k] = v


def get(name):
    if name not in NAMES:
        raise ValueError('inputs: %r is not a lifter input (%s)' % (name, ', '.join(NAMES)))
    if name not in _I:
        raise RuntimeError('inputs: %s was not configured: the program that runs the lifter passes it explicitly (fnvlift/inputs.py)' % name)
    return _I[name]


def reset():
    """for a test that runs several input sets in one process"""
    _I.clear()
