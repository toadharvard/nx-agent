"""The inputs of the byte-buffer tests (fnvlift/inputs.py): the declared x86 system record is the tracked file platform/declared/x86sys.json (the platform root's X86SYS, byte for byte), found from this file's own place in the checkout. A buffer is no module, so the universe and the depth facts are empty files; the fp-state directory has no invariants.tsv (the lookups answer "no fact file", so those forms are Unsupported)."""
import os, sys, tempfile
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LIFT)
X86SYS = os.path.join(os.path.dirname(LIFT), 'platform', 'declared', 'x86sys.json')
CPUMODEL = os.path.join(os.path.dirname(LIFT), 'verify', 'cpuid_model.json')


_SDMROWS = []


def sdmrows_arg():
    """the table path: `--sdmrows PATH` of the test's own command line (removed from argv); no default, a missing argument is an error"""
    if _SDMROWS:
        return _SDMROWS[0]
    if '--sdmrows' not in sys.argv or sys.argv.index('--sdmrows') + 1 >= len(sys.argv):
        raise SystemExit('missing --sdmrows PATH (sdmfeat.py dumprows JSON)')
    i = sys.argv.index('--sdmrows'); p = sys.argv[i + 1]; del sys.argv[i:i + 2]; _SDMROWS.append(p); return p


def configure(sdmrows=None, udsail=None):
    from fnvlift import inputs
    d = tempfile.mkdtemp(prefix='lifter-inputs-')
    open(d + '/universe.tsv', 'w').close(); open(d + '/depth.tsv', 'w').close()
    import json
    if udsail is None:
        udsail = d + '/udsail.tsv'; open(udsail, 'w').write('# fnvnx-udsail/2\n')      # no refused or undefined sequence in a buffer test
    json.dump({'format': 'fnvnx-segwrites/2', 'images': {'buf': {n: {'guard': n == 'gs'} for n in ('es', 'ds', 'fs', 'gs')}}}, open(d + '/segwrites.json', 'w'))      # a buffer has no write site of a segment register
    inputs.configure(universe=d + '/universe.tsv', fpstate=d, fpdepth=d + '/depth.tsv', x86sys=X86SYS, sdmrows=sdmrows or sdmrows_arg(), cpumodel=CPUMODEL, udsail=udsail, segwrites=d + '/segwrites.json', image='buf')
    from fnvlift import core; from proof import lift_dsl; core.set_emit(lift_dsl.emit_form)      # the proven Forms are the one path for the mnemonics they cover
