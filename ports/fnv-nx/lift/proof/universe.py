"""Scope of the form gate: the key set of /home/user/l3work/instest/result.json (keys = mnemonic|operand kinds|opcode bytes) minus the
families that need another reference (x87 `f*`, any xmm/mm/st operand, cpuid/rdtsc/emms/ldmxcsr/stmxcsr/prefetch*), plus the extra
integer/control/string/flag forms of enc_table.py (the 'other entries of the exe' list).  Real encodings of each key come from result.json."""
import json
RESULT = '/home/user/l3work/instest/result.json'
def out_of_scope(k):
    m, o, _ = k.split('|'); t = o.split(',')
    return m.startswith('f') or any(x in ('xmm', 'mm', 'st') for x in t) or m in ('cpuid', 'rdtsc', 'emms', 'ldmxcsr', 'stmxcsr') or m.startswith('prefetch')
def load(path=RESULT, per_key=2):
    """(in-scope keys, {key: [real encoding bytes]})"""
    d = json.load(open(path)); keys = sorted(k for k in d if not out_of_scope(k)); enc = {}
    for k in keys:
        seen = []
        for e in d[k].get('encodings', []):
            b = bytes.fromhex(e['bytes'])
            if b not in seen: seen.append(b)
        enc[k] = seen[:per_key]
    return keys, enc
