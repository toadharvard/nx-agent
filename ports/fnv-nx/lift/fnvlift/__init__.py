"""x86-32 -> C lifter, split by instruction family. Handlers register themselves in core on import.
Importing the package reads no file of rt/: the runtime prelude is built by prelude(rt_dir), called by the generator with the rt directory it declares as a recipe input."""
import os
from .image import Image
from .core import lift, Unsupported
from . import universe
from . import f_int, f_string, f_flow, f_sys, f_x87, f_sse, f_simd_int  # noqa: F401  (f_x87 before f_sse: fallback order)


def inline(rt_dir, name, gen_dir=None):
    """rt_dir/<name> (or gen_dir/<name>, the generated headers of fnvlift/rtgen.py) with its `#include "x"` lines for files of those directories replaced by their (recursively inlined) text: the lifted .cpp is compiled elsewhere"""
    p = os.path.join(rt_dir, name)
    if not os.path.exists(p) and gen_dir: p = os.path.join(gen_dir, name)
    with open(p, newline='') as f:
        return ''.join(inline(rt_dir, l.split('"')[1], gen_dir) if l.startswith('#include "') else l for l in f.read().splitlines(True))


def prelude(rt_dir, gen_dir=None):
    """the runtime prelude of every lifted translation unit: rt_dir/fnvl.h inlined (generated headers from gen_dir)"""
    if gen_dir is None:
        from . import rtgen
        gen_dir = _GEN[0] = _GEN[0] or rtgen.tmpdir()
    return inline(rt_dir, 'fnvl.h', gen_dir)


_GEN = [None]


def lift_entry(img, a):
    """lift universe entry a (an inner entry from its label inside the parent's range, a function with all its own labels)"""
    ua = universe.entry_args(a)
    if ua is None:
        raise Unsupported('not an entry')
    return lift(img, **ua)


__all__ = ['Image', 'lift', 'lift_entry', 'Unsupported', 'prelude', 'inline', 'universe']
