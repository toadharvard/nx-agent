"""udsail stage driver (a recipe run step, `exec /usr/bin/python3 -I`): unpacks the pinned sail-x86-from-acl2 source, runs proof/udsail.py over the image BADDECODE and SHAPES files, writes the `udsail` input file.
usage: python3 -I udsail_stage.py --lift-root DIR --isla ISLA --z3lib Z3LIB --model-tar TAR --sail-ir X86IR --x86sys X86SYS.json --sdmrows SDMROWS.json --cpumodel CPUMODEL.json --out UDSAIL.tsv [--bad F]... [--shapes F]...
Every path is an argument (the recipe mounts them); nothing is read from the environment or the filesystem outside them.  The step bound is isla's own (model_sail), not a wall clock."""
import os, sys, tarfile, tempfile, argparse

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    for n in ('lift-root', 'isla', 'z3lib', 'model-tar', 'sail-ir', 'x86sys', 'sdmrows', 'cpumodel', 'out'): ap.add_argument('--' + n, required=True)
    ap.add_argument('--bad', action='append', default=[]); ap.add_argument('--shapes', action='append', default=[])
    a = ap.parse_args(argv)
    sys.path.insert(0, os.path.abspath(a.lift_root))
    work = tempfile.mkdtemp(prefix='udsail')
    with tarfile.open(a.model_tar) as t:
        t.extractall(os.path.join(work, 'src'), filter='data')
    cands = [r for r, ds, fs in os.walk(os.path.join(work, 'src')) if os.path.basename(r) == 'model' and 'step.sail' in fs]
    if len(cands) != 1: raise SystemExit('udsail_stage: the model tarball has %d model/ directories with step.sail, expected 1' % len(cands))
    os.environ['LD_LIBRARY_PATH'] = os.path.join(os.path.abspath(a.z3lib), 'lib')      # isla's libz3 (the lift/proof/isla root output)
    from proof import udsail
    args = ['--sail-work', work, '--sail-ir', a.sail_ir, '--isla', a.isla, '--x86sys', a.x86sys, '--cpumodel', a.cpumodel, '--sdmrows', a.sdmrows, '--model-src', cands[0], '--out', a.out]
    for f in a.bad: args += ['--bad', f]
    for f in a.shapes: args += ['--shapes', f]
    rc = udsail.main(args)
    if rc not in (0, 1): return rc
    return 0       # a sequence the model does not decide as #UD is simply absent from the output; its site stays red in the lifter ("udsail" lookup), never silently accepted

if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
