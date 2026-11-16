"""s10_startup.py - rule XC1 (dyninit_rewrite.py): per-initializer functions for the dynamic-initializer objects."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import fnvnx.rules.dyninit_rewrite as dyninit_rewrite

RULES = dyninit_rewrite.RULES
