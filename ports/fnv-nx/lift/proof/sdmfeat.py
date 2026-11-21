#!/usr/bin/env python3
"""The Intel SDM text reader shared by proof/sdmpriv.py and proof/sdm_rows.py (the generators of the committed privileged-set and test-fixture rows): the text, its sha256, the instruction-reference sections and the page of a line.
The CPUID feature gate of the lifter does not read the SDM: a decoded site's feature is the capstone group against the declared CPU record (proof/cpufeat.py, verify/cpuid_model.json)."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import hashlib
import re

PAGE = re.compile(r'Vol\. (\d[A-D]?) (\d+-\d+)\s*$')
HEAD = re.compile(r'^([A-Z][A-Za-z0-9/,\.\* \-]*?)—(.+?)\s*$')
MODEHEAD = re.compile(r'^(Real-Address Mode|Real Address Mode|Virtual-8086 Mode|Virtual 8086 Mode|Protected Mode|Compatibility Mode|64-Bit Mode|Exceptions \(All|Other Exceptions|Numeric Exceptions|SIMD Floating-Point Exceptions|Intel C/C\+\+|Flags Affected|Operation|Description|Opcode|Instruction Operand)')


def sha_file(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()



def load(path):
    return open(path, encoding='utf-8', errors='replace').read().replace('\x0c', '').split('\n')



def region(lines):
    """the instruction reference: from the first real AAA heading to the end of the text"""
    a = next(i for i, ln in enumerate(lines) if ln.startswith('AAA—ASCII Adjust After Addition') and i > 20000)
    return a


def sections(lines):
    """[(heading, start line, end line)] of the instruction reference"""
    a = region(lines); out = []
    for i in range(a, len(lines)):
        ln = lines[i]
        if PAGE.search(ln):
            continue
        m = HEAD.match(ln)
        if m and not ln.startswith(' ') and 'Vol.' not in ln and len(ln) < 160 and re.match(r'^[A-Za-z][A-Za-z0-9/,\.\* \-]*—[A-Z]', ln) and not ln.rstrip().endswith(('.', '”')):
            out.append([ln.strip(), i, None])
    for k in range(len(out)):
        out[k][2] = out[k + 1][1] if k + 1 < len(out) else len(lines)
    return out


def page_after(lines, j):
    for k in range(j, min(j + 1500, len(lines))):
        m = PAGE.search(lines[k])
        if m:
            return 'Vol. %s %s' % (m.group(1), m.group(2))
    return '?'
