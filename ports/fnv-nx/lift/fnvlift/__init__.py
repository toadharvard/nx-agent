"""x86-32 -> C lifter, split by instruction family. Handlers register themselves in core on import."""
import os
from .image import Image
from .core import lift, Unsupported
from . import f_int, f_string, f_flow, f_x87, f_sse  # noqa: F401  (f_x87 before f_sse: fallback order)

with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'rt', 'fnvl.h'), newline='') as _f:
    PRELUDE = _f.read()

__all__ = ['Image', 'lift', 'Unsupported', 'PRELUDE']
