"""udsail stage driver (a recipe run step, `exec /usr/bin/python3 -I`): reads the patched Sail model (the lift/proof/sailmodel root output MODEL, the model of the pinned IR), runs proof/udsail.py over the image BADDECODE files, writes the `udsail` input file.
usage: python3 -I udsail_stage.py --lift-root DIR --isla ISLA --z3lib Z3LIB --model MODEL --sail-ir X86IR --x86sys X86SYS.json --cpumodel CPUMODEL.json --work SCRATCHDIR --out UDSAIL.tsv [--bad F]...
Every path is an argument (the recipe mounts them); nothing is read from the environment or the filesystem outside them.  The step bound is isla's own (model_sail), not a wall clock."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os, sys, argparse

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    for n in ('lift-root', 'isla', 'z3lib', 'model', 'sail-ir', 'x86sys', 'cpumodel', 'out', 'work'): ap.add_argument('--' + n, required=True)
    ap.add_argument('--bad', action='append', default=[])
    a = ap.parse_args(argv)
    sys.path.insert(0, os.path.abspath(a.lift_root))
    work = os.path.abspath(a.work)      # the step's scratch directory (RECIPE run `scratch`): the stage has no other writable path
    os.makedirs(work, exist_ok=True)
    md = os.path.join(os.path.abspath(a.model), 'model')
    if not os.path.isfile(os.path.join(md, 'step.sail')): raise SystemExit('udsail_stage: %s has no model/step.sail' % a.model)
    os.environ['LD_LIBRARY_PATH'] = os.path.join(os.path.abspath(a.z3lib), 'lib')      # isla's libz3 (the lift/proof/isla root output)
    from fnvnx.lift.proof import udsail
    args = ['--sail-work', work, '--sail-ir', a.sail_ir, '--isla', a.isla, '--x86sys', a.x86sys, '--cpumodel', a.cpumodel, '--model-src', md, '--out', a.out]
    for f in a.bad: args += ['--bad', f]
    rc = udsail.main(args)
    if rc not in (0, 1): return rc
    return 0       # a sequence the model does not decide as #UD is simply absent from the output; its site stays red in the lifter ("udsail" lookup), never silently accepted

if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
