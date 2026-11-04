"""config.py - where the inputs live. Everything here is local, derived from the decomp: never committed.

Environment variables (defaults are the developer VM's scratch layout):

  FNVNX_DECOMP     pristine fnv_decomp checkout (build/units.json, read only)
  FNVNX_X86OBJ     MSVC 2008 objects of the pristine decomp, one per unit: u<index in units.json>.obj (S11's
                   equivalence build); with FNVNX_ORACLE=clang the stand-in objects made by `difffuzz.py oracle-clang`
  FNVNX_TREE       prepared tree (tools/prepare.py output) the AArch64 objects were compiled from
  FNVNX_A64OBJ     ':'-separated directories of AArch64 ILP32 objects (tools/link/compile.sh layout), first hit wins
  FNVNX_DFWORK     work directory (per-unit images, corpora, results)
  FNVNX_DFLINK     link directory of the candidate objects (a64prep.py link: out/, fixed/), default WORK/../link
"""
import os
from pathlib import Path

# FNVNX_DFPROFILE: a file of KEY=VALUE lines (`#` comments) giving defaults for the variables above, so one object
# set (e.g. a link run of S2c) is selected by one setting
if os.environ.get('FNVNX_DFPROFILE'):
    for _line in open(os.environ['FNVNX_DFPROFILE']):
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.replace('export ', '').strip(), _v.strip())

SCR = Path('/tmp/claude-0/-home-user-nx-agent/9349aed9-849c-5755-9ca4-b338a8b8ed63/scratchpad')
HERE = Path(__file__).resolve().parent
PORT = HERE.parent.parent

DECOMP = Path(os.environ.get('FNVNX_DECOMP', SCR / 'ext/fnv_decomp'))
X86OBJ = Path(os.environ.get('FNVNX_X86OBJ', SCR / 's13/x86obj'))
ORACLE = os.environ.get('FNVNX_ORACLE', 'msvc')
TREE = Path(os.environ.get('FNVNX_TREE', SCR / 's2c/fresh/tree'))
A64OBJ = [Path(p) for p in os.environ.get('FNVNX_A64OBJ', '%s:%s' % (SCR / 's13/obj', SCR / 's2c/fresh/obj2')).split(':')
          if p]
WORK = Path(os.environ.get('FNVNX_DFWORK', SCR / 's13/work'))
LINK = Path(os.environ.get('FNVNX_DFLINK', WORK.parent / 'link'))     # a64prep.py link output (out/, fixed/)
A64WORK = Path(os.environ.get('FNVNX_DFA64', WORK / ('a64' if LINK.name == 'link' else 'a64_' + LINK.name)))
SSE2NEON = Path(os.environ.get('SSE2NEON', SCR / 'ext/sse2neon'))
LINKTOOLS = PORT / 'tools' / 'link'
# S1b's patches/units_renames.tsv (J-0141): the MSVC names a patch gives a definition (`def`) or a reference (`ref`);
# FNVNX_UNITS_RENAMES=<tsv> another table, `none` off
RENAMES = os.environ.get('FNVNX_UNITS_RENAMES', str(PORT / 'patches' / 'units_renames.tsv'))
_RENAMES = None


def units_renames():
    """[(source, kind, address, old, new)] of RENAMES for the units whose prepared source under TREE carries the S1b
    patch (fnvlink.load_renames: a unit compiled without the patch keeps its pristine names)."""
    global _RENAMES
    if _RENAMES is None:
        import sys
        if str(LINKTOOLS) not in sys.path:
            sys.path.insert(0, str(LINKTOOLS))
        from fnvlink import load_renames
        _RENAMES = load_renames(RENAMES, str(TREE)) if TREE.exists() else []
    return _RENAMES


def obj_name(source):
    return source.replace('/', '_') + '.o'


def a64_object(source):
    for d in A64OBJ:
        p = d / obj_name(source)
        if p.exists() and p.stat().st_size > 0:
            return p
    return None
