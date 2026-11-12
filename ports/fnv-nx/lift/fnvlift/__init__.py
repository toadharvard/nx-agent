"""x86-32 -> C lifter, split by instruction family. Handlers register themselves in core on import."""
import os
from .image import Image
from .core import lift, Unsupported
from . import universe
from . import f_int, f_string, f_flow, f_sys, f_x87, f_sse, f_simd_int  # noqa: F401  (f_x87 before f_sse: fallback order)

_RT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'rt')


def _inline(name):
    """rt/<name> with its `#include "x"` lines for files of rt/ replaced by their (recursively inlined) text: the lifted .cpp is compiled elsewhere"""
    with open(os.path.join(_RT, name), newline='') as f:
        return ''.join(_inline(l.split('"')[1]) if l.startswith('#include "') else l for l in f.read().splitlines(True))


PRELUDE = _inline('fnvl.h')


def lift_entry(img, a):
    """lift universe entry a (an inner entry from its label inside the parent's range, a function with all its own labels)"""
    ua = universe.entry_args(a)
    if ua is None:
        raise Unsupported('not an entry')
    return lift(img, **ua)


__all__ = ['Image', 'lift', 'lift_entry', 'Unsupported', 'PRELUDE', 'universe']
