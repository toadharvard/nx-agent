"""The integer `#define FNVL_<NAME> <number>` constants of lift/rt/fnvl.h (hardware exception codes passed to fnvl_raise, the declared machine's descriptor-table
constants), READ from the header: the one source.  Nothing in proof/ types them a second time.
  D            {'FNVL_DE0': 1, ..., 'FNVL_GDTR_BASE': 0x80b95000, ...}      every object-like macro of the header whose body is one integer literal
  code(name)   the value of FNVL_<name>; KeyError when the header has no such macro (a fault kind of dsl.FAULTS without a code is an error, never a default)
Rule: a number that the header defines is read here, by name; the header's sha256 is HEADER_SHA (recorded for the reports)."""
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from fnvlift import dsl      # noqa: E402

HEADER = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'rt', 'fnvl.h')
_DEF = re.compile(r'^#define\s+(FNVL_[A-Z0-9_]+)\s+(0[xX][0-9a-fA-F]+|[0-9]+)[uU]?\s*(?:/\*.*)?$')


def _read(path=HEADER):
    with open(path, 'rb') as fh:
        raw = fh.read()
    out = {}
    for ln in raw.decode().split('\n'):
        m = _DEF.match(ln)
        if m:
            out[m.group(1)] = int(m.group(2), 0)
    return out, hashlib.sha256(raw).hexdigest()


D, HEADER_SHA = _read()


KIND = {n: k for k, n in dsl.FAULTS.items()}      # the DSL's fault kind (Form.fault code) by name: dsl.FAULTS is the one table


def code(name):
    return D['FNVL_' + name]
