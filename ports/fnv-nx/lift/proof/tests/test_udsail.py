"""proof/udsail.classify on the output shapes of isla-execute-function (model_sail.split_blocks: a path is a block, `Result: (_ unit)` completes it; model exceptions are `Calling x86_fault<..>(["msg"])`
lines of stderr).  The model's own #UD texts: `(:UD)` and the ACL2 `#UD Encountered!`.
    test_udsail.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from proof import udsail

def blocks(out):
    res, hdr = [], None
    for line in out.split('\n'):
        if line.startswith('(trace'): res.append((hdr, line))
        elif line.strip(): hdr = line
    return res

OK = 'Result: (_ unit)\n(trace\n)\n'
ERR = 'Error Exception\n(trace\n)\n'
f = lambda m, k='fault': '[\x1b[1m1 \x1b[0m]: Calling x86_%s<z4>(["%s"])\n' % (k, m)
UD, UD2 = '(:UD)', '(X86-ILLEGAL-INSTRUCTION \\"#UD Encountered!\\" START-RIP TEMP-RIP X86)'
c = lambda out, err: udsail.classify(out, err, blocks)
assert c(ERR, f(UD))[0] == 'UD' and c(ERR + ERR, f(UD) + f(UD2))[0] == 'UD'
assert c(OK + ERR, f(UD))[0] == 'VALID'                                      # a completing path: defined encoding
assert c(ERR, f('Opcode Unimplemented in x86isa!', 'model_error'))[0] == 'GAP'
assert c(ERR + ERR, f(UD) + f('(:GP)'))[0] == 'GAP'                          # a path that is not a #UD
assert c(ERR + ERR, f(UD))[0] == 'GAP'                                       # a path without its model exception text: not decided
assert c('', '')[0] == 'GAP'
# verdict: the least extent with the tail free; the feature rule short-circuits
runs = []
def run(code, free):
    runs.append(free); return (ERR, f(UD)) if free[0] >= 2 else (OK, '')
v = udsail.verdict(bytes.fromhex('c7d8aabb'), run, blocks, lambda r: None)
assert v[:2] == ('UD', 2) and runs == [(1, 15), (2, 14)], (v, runs)
assert udsail.verdict(bytes.fromhex('c7'), lambda code, free: (OK, ''), blocks, lambda r: None)[0] == 'TRUNCATED'
assert udsail.verdict(bytes.fromhex('c5f8'), None, blocks, lambda r: 'vex')[:3] == ('UD', 2, 'feature:vex')
print('ok')
