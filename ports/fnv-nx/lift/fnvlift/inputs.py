"""The inputs of the lifter, stated once and explicitly. RULE: no lifter module reads an environment variable or a default path; every file it reads outside
its own tree is one of the names below, set by the program that runs the lifter (a recipe stage passes /in or /uses paths; a test passes its own files) through
configure(). A name that was not configured is an error at the point of use: there is no ambient default and no silent "missing file means empty".

  universe   the entry universe (tools/universe.py output): a file; an empty universe is an empty file
  fpstate    the directory of the fp-state facts (invariants.tsv; tools/fpstate.py)
  fpdepth    the x87 stack-depth facts (depth.tsv; tools/fpmodes.py): a file; no facts is an empty file
  x86sys     the declared x86 system record (platform X86SYS output)
  sdmrows    the SDM opcode rows and flag table (proof/sdmfeat.py dumprows output, JSON): the one source of the CPUID feature gate (cpufeat.py)
  cpumodel   the declared CPU's data (verify/cpuid_model.json): the words of the CPUID feature gate (cpufeat.py)
  segwrites  the per-image write-site counts of the data segment registers (lift/facts SEGWRITES output, tools/segwrites.py): a file
  udsail     the Sail #UD verdicts of the byte sequences capstone refuses (proof/udsail.py output): a file; read only when such a sequence is met
  rcptab     the certified RCPSS/RSQRTSS table source (lift/rt/rcptab.c): a file; read by dsl._rcp_tables, which checks it against rcpcert
  rcpcert    the certificate of that table (lift/rt/rcptab.cert.json): a file
  image      the key of the image being lifted in that file ('exe', or the module name without .dll, lower case)
The load base of a module is not an input of this kind: it is the argument `base` of Image(path, base), taken from the module table row.
Proof: a lookup is get(name); configure() accepts only these names; a second configure() of a name with another value is an error (one process, one input set)."""
NAMES = ('universe', 'fpstate', 'fpdepth', 'x86sys', 'sdmrows', 'cpumodel', 'segwrites', 'udsail', 'rcptab', 'rcpcert', 'image')
_I = {}


def _same_file(a, b):
    """two spellings of one input file (a path through ports/fnvnx, the module path's link, or through ports/fnv-nx): the same file, so the same value"""
    import os
    return isinstance(a, str) and isinstance(b, str) and os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)


def configure(**kw):
    for k, v in kw.items():
        if k not in NAMES:
            raise ValueError('inputs: %r is not a lifter input (%s)' % (k, ', '.join(NAMES)))
        if k in _I and _I[k] != v and not _same_file(_I[k], v):
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
