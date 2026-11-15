"""The generated headers of rt/ (rt/gen: fnvl_faults.h, seh_faults.inc, fnvl_cpuref.h, rcpref.h, cpumodel_tab.inc, fnvl_sys.h) for readers outside the recipe (the lift prelude, the fp gate, the certifying program).
They are produced by the SAME tools/gen_*.py stages that rt/build.json runs (same inputs, same bytes); nothing is committed."""
import os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE); ROOT = os.path.dirname(os.path.dirname(LIFT))      # ports/fnv-nx/lift, ports/fnv-nx -> repo root is dirname^2 of LIFT's parent
REC = os.environ.get('FNVNX_X86SYS') or os.path.join(os.path.dirname(LIFT), 'platform', 'declared', 'x86sys.json')


def generate(outdir):
    """run the three generator stages of lift/rt/build.json into outdir; returns outdir"""
    os.makedirs(outdir, exist_ok=True)
    for tool, args in (('gen_faults.py', [outdir]), ('gen_cpuref.py', [outdir]), ('gen_sys.py', [REC, outdir])):
        subprocess.run([sys.executable, '-I', os.path.join(LIFT, 'tools', tool)] + args, check=True)
    return outdir


def tmpdir():
    return generate(tempfile.mkdtemp(prefix='fnvl_gen_'))
