"""s10_startup.py - prepare rules of stream S10-startup, loaded by tools/prepare.py.

XC1 (startup/tools/dyninit_rewrite.py, docs/STARTUP.md): every generated `??__E` object becomes `extern T x;` plus
an `extern "C" __fnvnx_dyninit_<addr>` function (and `__fnvnx_dynexit_<addr>`), so the startup runner can construct
the image's globals in their original order; copies in flag-variant units become plain extern declarations.
"""
import os
import sys

_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'startup', 'tools')
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

from dyninit_rewrite import RULES  # noqa: E402,F401
