"""tools/segwrites.py analyse(): write sites of es/ds/fs/gs and the proof that the loaded selector is non-null (backward walk over unique predecessors, greatest fixpoint over S'), on synthetic images.
    test_segwrites.py"""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [os.path.dirname(HERE), os.path.dirname(os.path.dirname(HERE))]
from fnvlift.image import Image
import segwrites

INIT = dict(es=False, ds=False, fs=False, gs=True)      # initial null: gs only (x86sys selectors)


def run(hexcode, entries=(0x401000,)):
    code = bytes.fromhex(hexcode.replace(' ', '')); img = Image.from_bytes(code, 0x401000)
    d = tempfile.mkdtemp(); p = d + '/ranges.tsv'
    open(p, 'w').write('# base=0x401000: test\n00401000\t401000-%x\n' % (0x401000 + len(code)))
    return segwrites.analyse(img, p, {}, {e: 'entry' for e in entries}, INIT)


def st(r, s): return (r[s]['nonnull'], r[s]['maynull'], r[s]['guard'])


r = run('66b82300 8ed8 c3')                  # mov ax,0x23; mov ds,ax
assert st(r, 'ds') == (1, 0, False), r['ds']
r = run('66b80000 8ed8 c3')                  # mov ax,0; mov ds,ax: null
assert st(r, 'ds') == (0, 1, True), r['ds']
r = run('1e 07 c3')                          # push ds; pop es: es non-null iff ds is
assert st(r, 'es') == (1, 0, False) and st(r, 'ds') == (0, 0, False), (r['es'], r['ds'])
r = run('8b03 8ee0 c3')                      # mov eax,[ebx]; mov fs,ax: a memory load
assert st(r, 'fs') == (0, 1, True), r['fs']
r = run('c4 00 c3')                          # les eax,[eax]: a far pointer
assert st(r, 'es') == (0, 1, True), r['es']
r = run('8cd8 8ec0 c3')                      # mov eax,ds; mov es,ax
assert st(r, 'es') == (1, 0, False), r['es']
r = run('8b03 8ed8 1e 07 c3')                # ds written from memory (maybe null): the push ds; pop es copy is not proven non-null
assert st(r, 'ds') == (0, 1, True) and st(r, 'es') == (0, 1, True), (r['ds'], r['es'])
r = run('66b82300 eb02 90 90 8ed8 c3')       # jmp over: the write has one predecessor (the jmp target) but through a jump; still the def is not adjacent: unknown
assert st(r, 'ds')[2] is True, r['ds']
r = run('66b82300 33c9 8ed8 c3')             # an instruction between that writes another register: walked through
assert st(r, 'ds') == (1, 0, False), r['ds']
r = run('66b82300 e8 00000000 8ed8 c3')      # a call between: a barrier
assert st(r, 'ds')[2] is True, r['ds']
r = run('0f a9 c3')                          # pop gs: gs starts null, so it is guarded whatever is written
assert r['gs']['guard'] is True and r['gs']['initial_null'] is True
r = run('50 58 8ed8 c3')                    # push eax; pop eax; mov ds,ax: the value is unknown (eax at entry): possibly null
assert st(r, 'ds') == (0, 1, True), r['ds']
r = run('6a23 58 8ed8 c3')                   # push 0x23; pop eax; mov ds,ax: proven through the stack
assert st(r, 'ds') == (1, 0, False), r['ds']
r = run('8b03 8bd0 8eda c3')                 # mov eax,[ebx]; mov edx,eax; mov ds,dx: the register move carries the unknown load
assert st(r, 'ds') == (0, 1, True), r['ds']
r = run('b823000000 8bd0 8eda c3')           # mov eax,0x23; mov edx,eax; mov ds,dx
assert st(r, 'ds') == (1, 0, False), r['ds']
print('segwrites: ok')
