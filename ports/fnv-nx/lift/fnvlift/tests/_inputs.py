"""The inputs of the byte-buffer tests (fnvlift/inputs.py): the declared x86 system record is the tracked file platform/declared/x86sys.json (the platform root's X86SYS, byte for byte), found from this file's own place in the checkout. A buffer is no module, so the universe and the depth facts are empty files; the fp-state directory has no invariants.tsv (the lookups answer "no fact file", so those forms are Unsupported)."""
import os, sys, tempfile
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LIFT)
X86SYS = os.path.join(os.path.dirname(LIFT), 'platform', 'declared', 'x86sys.json')


_CPUFEAT = []


def cpufeat_arg():
    """the table path: `--cpufeat PATH` of the test's own command line (removed from argv); no default, a missing argument is an error"""
    if _CPUFEAT:
        return _CPUFEAT[0]
    if '--cpufeat' not in sys.argv or sys.argv.index('--cpufeat') + 1 >= len(sys.argv):
        raise SystemExit('missing --cpufeat PATH (the CPUID feature-bit table, L3b cpuid_feature_bits.tsv)')
    i = sys.argv.index('--cpufeat'); p = sys.argv[i + 1]; del sys.argv[i:i + 2]; _CPUFEAT.append(p); return p


def configure(cpufeat=None):
    from fnvlift import inputs
    d = tempfile.mkdtemp(prefix='lifter-inputs-')
    open(d + '/universe.tsv', 'w').close(); open(d + '/depth.tsv', 'w').close()
    import json
    json.dump({'format': 'fnvnx-segwrites/2', 'images': {'buf': {n: {'guard': n == 'gs'} for n in ('es', 'ds', 'fs', 'gs')}}}, open(d + '/segwrites.json', 'w'))      # a buffer has no write site of a segment register
    inputs.configure(universe=d + '/universe.tsv', fpstate=d, fpdepth=d + '/depth.tsv', x86sys=X86SYS, cpufeat=cpufeat or cpufeat_arg(), segwrites=d + '/segwrites.json', image='buf')
    from fnvlift import core; from proof import lift_dsl; core.set_emit(lift_dsl.emit_form)      # the proven Forms are the one path for the mnemonics they cover
