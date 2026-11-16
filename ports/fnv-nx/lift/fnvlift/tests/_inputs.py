"""The inputs of the byte-buffer tests (fnvlift/inputs.py): the declared x86 system record is the tracked file platform/declared/x86sys.json (the platform root's X86SYS, byte for byte), found from this file's own place in the checkout. A buffer is no module, so the universe and the depth facts are empty files; the fp-state directory has no invariants.tsv (the lookups answer "no fact file", so those forms are Unsupported)."""
import os, sys, tempfile
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LIFT)
X86SYS = os.path.join(os.path.dirname(LIFT), 'platform', 'declared', 'x86sys.json')
CPUMODEL = os.path.join(os.path.dirname(LIFT), 'verify', 'cpuid_model.json')
RCPTAB = os.path.join(LIFT, 'rt', 'rcptab.c')
RCPCERT = os.path.join(LIFT, 'rt', 'rcptab.cert.json')


def sdmrows_arg():
    """the SDM opcode-row table: the mounted produced input SDMROWS of tests/inputs.json ({"root": "ports/fnv-nx/lift/sdmrows", "output": "SDMROWS"}, the selftests gate builds it through the recipe and mounts it as FNVNX_INPUT_SDMROWS); no default, an unmounted input is an error"""
    p = os.environ.get('FNVNX_INPUT_SDMROWS')
    if not p:
        raise SystemExit('FNVNX_INPUT_SDMROWS is not mounted (declare the produced input SDMROWS of root lift/sdmrows in tests/inputs.json)')
    return os.path.join(p, 'sdmrows.json') if os.path.isdir(p) else p


def configure(sdmrows=None, udsail=None):
    from fnvlift import inputs
    d = tempfile.mkdtemp(prefix='lifter-inputs-')
    open(d + '/universe.tsv', 'w').close(); open(d + '/depth.tsv', 'w').close()
    import json
    if udsail is None:
        udsail = d + '/udsail.tsv'; open(udsail, 'w').write('# fnvnx-udsail/2\n')      # no refused or undefined sequence in a buffer test
    json.dump({'format': 'fnvnx-segwrites/2', 'images': {'buf': {n: {'guard': n == 'gs'} for n in ('es', 'ds', 'fs', 'gs')}}}, open(d + '/segwrites.json', 'w'))      # a buffer has no write site of a segment register
    inputs.configure(universe=d + '/universe.tsv', fpstate=d, fpdepth=d + '/depth.tsv', x86sys=X86SYS, sdmrows=sdmrows or sdmrows_arg(), cpumodel=CPUMODEL, rcptab=RCPTAB, rcpcert=RCPCERT, udsail=udsail, segwrites=d + '/segwrites.json', image='buf')
    from fnvlift import core; from proof import lift_dsl, cpufeat; core.set_emit(lift_dsl.emit_form, cpufeat.missing)      # the proven Forms are the one path for the mnemonics they cover


def configure_declared():
    """the declared-machine files only (x86sys, cpumodel, the certified rcp table): for the proof tests that build Forms and read no module facts"""
    from fnvlift import inputs
    inputs.configure(x86sys=X86SYS, cpumodel=CPUMODEL, rcptab=RCPTAB, rcpcert=RCPCERT)


def configure_image(universe, image):
    """the lifter's inputs for a test that lifts a real image (the exe, or a module of the loader) from the facts of the product roots: the same declared inputs and mounts as liftsrc's shard stages
    (FNVNX_INPUT_FPSTATE, FNVNX_INPUT_FPDEPTH of lift/fpfacts, FNVNX_INPUT_SEGWRITES of lift/facts, FNVNX_INPUT_SDMROWS of lift/sdmrows, tests/inputs.json), `universe` the image's universe.tsv, `image` its name ('exe' or the module's)"""
    from fnvlift import inputs
    env = {}
    for k in ('FPSTATE', 'FPDEPTH', 'SEGWRITES'):
        v = os.environ.get('FNVNX_INPUT_' + k)
        if not v: raise SystemExit('FNVNX_INPUT_%s is not mounted (declare the produced input %s in tests/inputs.json)' % (k, k))
        env[k] = v
    inputs.configure(universe=universe, fpstate=env['FPSTATE'], fpdepth=env['FPDEPTH'], x86sys=X86SYS, sdmrows=sdmrows_arg(), cpumodel=CPUMODEL, rcptab=RCPTAB, rcpcert=RCPCERT, segwrites=env['SEGWRITES'], image=image)
    from fnvlift import core; from proof import lift_dsl, cpufeat; core.set_emit(lift_dsl.emit_form, cpufeat.missing)
