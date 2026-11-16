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
# verdict: the least extent with the tail free; the feature rule short-circuits; the model's default-arm opcodes are DEFAULT; every system variant must say #UD
runs = []
def run(code, free, ctrs):
    runs.append((free, ctrs)); return (ERR, f(UD)) if free[0] >= 2 else (OK, '')
V = [[0] * 17, [4] + [0] * 16]
DEF = {'1': {0xd6}, '0f': {0x04, 0xff}}
v = udsail.verdict(bytes.fromhex('c7d8aabb'), run, blocks, lambda r: None, DEF, V)
assert v[:2] == ('UD', 2) and v[3] == 'sail' and [r[0] for r in runs] == [(1, 15)] * 2 + [(2, 14)] * 2, (v, runs)
assert udsail.verdict(bytes.fromhex('c7'), lambda code, free, c: (OK, ''), blocks, lambda r: None, DEF, V)[0] == 'FETCH_OUT'
assert udsail.verdict(bytes.fromhex('c5f8'), None, blocks, lambda r: 'vex', DEF, V)[:4] == ('UD', 2, 'feature:vex', 'feature')
assert udsail.verdict(bytes.fromhex('d6'), None, blocks, lambda r: None, DEF, V)[0] == 'DEFAULT'
assert udsail.verdict(bytes.fromhex('660f04ff'), None, blocks, lambda r: None, DEF, V)[0] == 'DEFAULT'
# a variant that is not #UD makes the sequence not decided: the verdict depends on a system bit
mixed = lambda code, free, ctrs: (ERR, f(UD)) if ctrs[0] == 0 else (ERR, f('(:GP)'))
assert udsail.verdict(bytes.fromhex('c7d8'), mixed, blocks, lambda r: None, DEF, V)[0] == 'GAP'
# default_opcodes / bitfield / sysvariants read the model source text
import tempfile
d = tempfile.mkdtemp()
arm = lambda n: '          %d => {\n              x86_fault("(:UD)")\n          },\n' % n
mat = lambda arms: '        (match opcode {\n' + ''.join(arm(n) for n in arms) + '          _ => x86_fault("(X86-ILLEGAL-INSTRUCTION \\"#UD Encountered!\\" START-RIP TEMP-RIP X86)")\n        }) : unit\n'
open(d + '/step.sail', 'w').write(mat([x for x in range(256) if x != 0xd6]))
open(d + '/two_byte_opcodes_dispatch.sail', 'w').write(mat([x for x in range(256) if x not in (4, 0xff)]))
open(d + '/structures.sail', 'w').write('bitfield cr0bits : bits(32) =\n  {\n    ts : 3..3,\n    em : 2..2,\n    mp : 1..1,\n    pe : 0..0\n  }\nbitfield cr4bits : bits(22) =\n  {\n    osfxsr : 9..9,\n    tsd : 2..2,\n    vme : 0..0\n  }\n')
assert udsail.default_opcodes(d) == {'1': {0xd6}, '0f': {4, 0xff}}
rec = {'cr0': {'em': {'status': 'undecided'}, 'ts': {'status': 'undecided'}}, 'cr4': {'tsd': {'value': 1}}, 'cr4_sse': {'osfxsr': {'status': 'undecided'}}}
vs = udsail.sysvariants(rec, d)
assert vs[0][0] == 0 and vs[0][4] == 4 and any(v[4] == 4 | 0x200 for v in vs) and any(v[0] == 4 for v in vs) and any(v[0] == 8 for v in vs) and all(len(v) == 17 for v in vs), vs
# class (a) from the SDM rows: xbegin (C7 F8, flag RTM), an unflagged row, an `executed as` row, an ambiguous match
from proof import sdmfeat
def row(op, flag, modc='any', exec_as=()): return {'op': op, 'p': sdmfeat.parse_opcode(op), 'flag': flag, 'modc': modc, 'exec_as': list(exec_as)}
TABLE = sdmfeat.Table([dict(canonical=n, sdm_name=n, apm_name='', mirror_of='', leaf=l, sub=0, reg=r, bit=b) for n, l, r, b in (('RTM', 7, 'EBX', 11), ('SSE2', 1, 'EDX', 26))])      # the feature table of sdmfeat (records of unify)
CPU = {(0, 'EAX'): 0x16, (0x80000000, 'EAX'): 0x80000008, (7, 'EBX'): 0, (1, 'EDX'): 1 << 26}
ROWS = [row('C7 F8', 'RTM'), row('C7 /0', ''), row('0F 28 /r', 'SSE2'), row('0F BC /r', 'RTM', exec_as=('BSF',))]
assert udsail.sdm_feature(bytes.fromhex('c7f8aabbccdd'), ROWS, TABLE, CPU) == ('RTM', 2)
assert udsail.sdm_feature(bytes.fromhex('c7c0'), ROWS, TABLE, CPU) is None             # C7 /0: no flag
assert udsail.sdm_feature(bytes.fromhex('0f28c0'), ROWS, TABLE, CPU) is None           # SSE2 present on the declared CPU
assert udsail.sdm_feature(bytes.fromhex('0fbcc0'), ROWS, TABLE, CPU) is None           # executed as another instruction
assert udsail.sdm_feature(bytes.fromhex('c7f8'), ROWS, TABLE, CPU, nopflags={'RTM'}) is None
assert udsail.sdm_feature(bytes.fromhex('f3c7f8'), ROWS, TABLE, CPU)[1] == 3           # the prefix is part of the extent
print('ok')
