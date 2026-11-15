"""tools/segwrites.py writes(): the write sites of ds/es/fs/gs (mov Sreg, pop Sreg, lds/les/lfs/lgs), nothing else (reads, cs/ss loads, memory operands).
    test_segwrites.py"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [os.path.dirname(HERE), os.path.dirname(os.path.dirname(HERE))]
import capstone as C
import segwrites
md = C.Cs(C.CS_ARCH_X86, C.CS_MODE_32); md.detail = True
code = bytes.fromhex('8ed8' '8ec0' '8ee0' '8ee8' '1f' '07' '0fa1' '0fa9' 'c4 00'.replace(' ', '') + 'c5 00'.replace(' ', '') + '0fb4 00'.replace(' ', '') + '0fb5 00'.replace(' ', '') + '8ed0' '17' '8cd8' '0fa8' '1e')
# mov ds/es/fs/gs,ax; pop ds; pop es; pop fs; pop gs; les eax,[eax]; lds eax,[eax]; lfs eax,[eax]; lgs eax,[eax]; mov ss,ax; pop ss; mov eax,ds; push gs; push ds
ins = list(md.disasm(code, 0x1000))
c = segwrites.writes(ins)
assert c == dict(es=3, ds=3, fs=3, gs=3), c
print('segwrites: ok', c)
