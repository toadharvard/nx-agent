#!/usr/bin/env python3
"""declared_gen: the rows of declared.json are GENERATED, not typed.
    python3 -I declared_gen.py SDM.txt OUT.json        (check: `declared_gen.py --check SDM.txt` compares with the committed file's rows)
Inputs, both hashed into the output header:
  rt/cpumodel.c      the exported interface: `void fnvl_cpuid(u32 leaf, u32 sub, u32 o[N])` (N outputs, o[i] = the i-th register of the SDM's output tuple) and `u64 fnvl_rdtsc(void)` (a 64-bit result);
  the SDM text       the Operation lines of CPUID (`(EAX, EBX, ECX, EDX) := CPUID_INFO(EAX, ECX)`) and RDTSC (`EDX:EAX := TimeStampCounter`), which name the output registers and their order.
A function of cpumodel.c's interface whose SDM instruction line is absent, or whose output count differs from the SDM tuple, makes the stage fail.  The file holds no machine fact (cpl, iopl, selectors, CR4 bits): those are the rows of platform/declared/x86sys.json, read by x86sys.py."""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CPUMODEL = os.path.join(HERE, '..', 'rt', 'cpumodel.c')
OUT = os.path.join(HERE, 'declared.json')


def sha(b): return hashlib.sha256(b).hexdigest()


def rows(sdm_text, cpumodel):
    out = []
    m = re.search(r'void fnvl_cpuid\(u32 leaf, u32 sub, u32 o\[(\d+)\]\)', cpumodel)
    n = int(m.group(1)); line = re.search(r'^\(((?:E[A-D]X(?:, )?)+)\) := CPUID_INFO\(EAX, ECX\)', sdm_text, re.M)
    regs = [r.lower() for r in line.group(1).split(', ')]
    if len(regs) != n: raise SystemExit('cpumodel.c fnvl_cpuid has %d outputs, the SDM CPUID tuple has %d' % (n, len(regs)))
    out.append({'function': 'cpuid', 'ufs': ['cpuid%d' % i for i in range(n)], 'source': 'rt/cpumodel.c', 'basis': 'SDM vol. 2A CPUID Operation: %s' % line.group(0), 'outputs': regs})
    if not re.search(r'u64 fnvl_rdtsc\(void\)|unsigned long long fnvl_rdtsc\(void\)', cpumodel): raise SystemExit('cpumodel.c has no fnvl_rdtsc')
    t = re.search(r'^\s*(E[A-D]X):(E[A-D]X) := TimeStampCounter', sdm_text, re.M)
    out.append({'function': 'rdtsc', 'ufs': ['rdtsc'], 'source': 'rt/cpumodel.c', 'basis': 'SDM vol. 2B RDTSC Operation: %s := TimeStampCounter (64-bit result of fnvl_rdtsc)' % t.group(0).split(' := ')[0].strip(),
                'outputs': sorted([t.group(1).lower(), t.group(2).lower()])})
    return out


def build(sdm_path):
    sdm = open(sdm_path, encoding='utf-8', errors='replace').read(); cm = open(CPUMODEL).read()
    return {'format': 'fnvnx-declared/1', 'generated_from': {'rt/cpumodel.c': sha(cm.encode()), 'sdm_sha256': sha(sdm.encode())}, 'rows': rows(sdm, cm)}


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--check':
        g = build(sys.argv[2]); cur = json.load(open(OUT))
        sys.exit(0 if g['rows'] == cur['rows'] and g.get('generated_from') == cur.get('generated_from') else 1)
    json.dump(build(sys.argv[1]), open(sys.argv[2], 'w'), indent=1); open(sys.argv[2], 'a').write('\n')
