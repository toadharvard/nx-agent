"""x86-32 -> C lifter, split by instruction family. Handlers register themselves in core on import.
Importing the package reads no file of rt/: the runtime prelude is built by prelude(rt_dir), called by the generator with the rt directory it declares as a recipe input."""
import os
from .image import Image
from .core import lift, Unsupported
from . import universe
from . import f_int, f_string, f_flow, f_sys, f_x87, f_sse, f_simd_int  # noqa: F401  (f_x87 before f_sse: fallback order)


def inline(rt_dir, name):
    """rt_dir/<name> with its `#include "x"` lines for files of rt_dir replaced by their (recursively inlined) text: the lifted .cpp is compiled elsewhere"""
    with open(os.path.join(rt_dir, name), newline='') as f:
        return ''.join(inline(rt_dir, l.split('"')[1]) if l.startswith('#include "') else l for l in f.read().splitlines(True))


def prelude(rt_dir):
    """the runtime prelude of every lifted translation unit: rt_dir/fnvl.h inlined"""
    return inline(rt_dir, 'fnvl.h')


def lift_entry(img, a):
    """lift universe entry a (an inner entry from its label inside the parent's range, a function with all its own labels)"""
    ua = universe.entry_args(a)
    if ua is None:
        raise Unsupported('not an entry')
    return lift(img, **ua)


__all__ = ['Image', 'lift', 'lift_entry', 'Unsupported', 'prelude', 'inline', 'universe']
