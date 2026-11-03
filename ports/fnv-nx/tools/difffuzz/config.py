"""config.py - where the inputs live. Everything here is local, derived from the decomp: never committed.

Environment variables (defaults are the developer VM's scratch layout):

  FNVNX_DECOMP     pristine fnv_decomp checkout (build/units.json, read only)
  FNVNX_X86OBJ     MSVC 2008 objects of the pristine decomp, one per unit: u<index in units.json>.obj (S11's
                   equivalence build); with FNVNX_ORACLE=clang the stand-in objects made by `difffuzz.py oracle-clang`
  FNVNX_TREE       prepared tree (tools/prepare.py output) the AArch64 objects were compiled from
  FNVNX_A64OBJ     ':'-separated directories of AArch64 ILP32 objects (tools/link/compile.sh layout), first hit wins
  FNVNX_DFWORK     work directory (link step, per-unit images, corpora, results)
"""
import os
from pathlib import Path

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
SSE2NEON = Path(os.environ.get('SSE2NEON', SCR / 'ext/sse2neon'))
LINKTOOLS = PORT / 'tools' / 'link'


def obj_name(source):
    return source.replace('/', '_') + '.o'


def a64_object(source):
    for d in A64OBJ:
        p = d / obj_name(source)
        if p.exists() and p.stat().st_size > 0:
            return p
    return None
