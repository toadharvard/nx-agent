"""x86-32 -> C lifter, split by instruction family. Handlers register themselves in core on import.
Importing the package reads no file of rt/: the lifter emits text; the runtime headers of the pack are inlined by tools/prelude.py (a stage of lift/pack), never here."""
import os
from .image import Image
from .core import lift, Unsupported
from . import inputs, universe
from . import f_int, f_flow, f_sys, f_x87, f_sse, f_simd_int  # noqa: F401  (f_x87 before f_sse: shape_handler order)


def lift_entry(img, a):
    """lift universe entry a (an inner entry from its label inside the parent's range, a function with all its own labels)"""
    ua = universe.entry_args(a)
    if ua is None:
        raise Unsupported('not an entry')
    return lift(img, **ua)


__all__ = ['Image', 'lift', 'lift_entry', 'Unsupported', 'universe', 'inputs']
