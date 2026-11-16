"""The declared inputs of every lift/proof test that runs the Sail model (the proof toolchain of farm/sail_setup.sh): FNVNX_SAIL_WORK, FNVNX_SAIL_IR, FNVNX_ISLA, FNVNX_ISLA_CFG, passed explicitly to proof.model_sail.configure.
One module for all tests (CLAUDE.md rule 2): no test restates the names or the configure call.  Without them the test classes are skipped with the reason, never run against a default."""
import os
import sys
import unittest

LIFT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
if LIFT not in sys.path:
    sys.path.insert(0, LIFT)
NAMES = ('FNVNX_SAIL_WORK', 'FNVNX_SAIL_IR', 'FNVNX_ISLA', 'FNVNX_ISLA_CFG')
MISSING = [n for n in NAMES if not os.environ.get(n)]
REASON = 'declared input not set: %s (the Sail proof toolchain: farm/sail_setup.sh)' % ', '.join(MISSING)
needs_sail = unittest.skipIf(bool(MISSING), REASON)      # the class decorator of a test that proves against the Sail model


def configure():
    if MISSING: return False
    from proof import model_sail
    model_sail.configure(*(os.environ[n] for n in NAMES))
    return True
