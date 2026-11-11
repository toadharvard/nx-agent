#!/usr/bin/env python3
"""instest over SYNTHETIC integer/system encodings that the exe census does not contain (W14), through the same oracle/compare path as test/instest.py.
    instest_extra.py [--only mn,mn] [--k 4] [--n 8] [--tables]
The encodings are listed as hex strings below (one per form variant). Each is placed at its own fake address (0x7d000000 + n*0x20) in a patched Image.
`--tables` instead checks the generated C of the `int imm8` vector table of fnvlift/f_sys.py against its documented rows (no oracle: Unicorn faults on all of them), and (W15) the class rules
of fnvlift/f_sys.py: for every encoding below, the generated C must contain the listed substrings (CLASS_EXPECT: privileged opcodes by KiTrap0D's table, segment loads and far transfers on the
model GDT, descriptor-table reads, hint NOPs, prefix-only variants, UD by absent extension) and none of the forbidden ones. The expectations are written by hand from the SDM / ReactOS table,
not computed by the rule under test. No oracle: Unicorn has no OS, ring 3, Win32 selectors or model GDT.
Everything exe-derived goes to /home/user/l3work/xinstest, never the repo."""
import sys, os, struct
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import instest as T
from capstone import x86 as X
import fnvlift
ENC = '''
imul|f6e9 f66901 f6690c f6ed
mul|f6e1 f66108 66f7e1 66f76108 f6e5
imul|66f7e9 66f76908 66f7ee
div|f6f1 f67108 66f7f1 66f77108 f6f3 66f7f3
idiv|f6f9 f67908 66f7f9 66f77908 f6fb 66f7fb
rcl|d0d0 d0d1 d1d0 d1d2 d2d0 d3d0 d3d2 66d1d0 66d3d0 c0d005 c0d009 c1d005 c1d01f 66c1d00f 66c1d011 d15004 d35004 d25004 d05004
rcr|d0d8 d0d9 d1d8 d1da d2d8 d3d8 d3da 66d1d8 66d3d8 c0d805 c0d809 c1d805 c1d81f 66c1d80f 66c1d811 d15804 d35804 d25804 d05804
shld|660fa4c105 660fa4c110 660fa4c111 660fa5c1 660fa4410401 660fa54104
shrd|660facc105 660fadc1 660facc110 660facc111 660fac410401 660fad4104
push|6650 66ff30 666a01 66680112 06 0e 16 1e 0fa0 0fa8 6606 660fa0 661e
pop|6658 668f00 668f4004
mov|8cd8 668cd8 8c00 8c4004 8c5004 8cc0 668cd0 8ce0 8ce8 8cc8
bt|0fa301 0fa34104 660fa301 660fa34104 0fa30c24
bts|0fab01 0fab4104 660fab01 660fab4104
btr|0fb301 0fb34104 660fb301 660fb34104
btc|0fbb01 0fbb4104 660fbb01 660fbb4104
rol|d14004 d04004 d1c0 d3c0 66d1c0
ror|d14804 d04804 d1c8 d3c8 66d1c8
shl|d16004 d06004 d1e0
lahf|9f
cmc|f5
clc|f8
stc|f9
salc|d6
cwd|6699
xlatb|d7 67d7
pushaw|6660
leave|c9 66c9
movsb|67a4 f367a4
stosb|67aa f367aa
stosd|67ab f367ab
lodsb|67ac
scasb|67ae f267ae
cmpsb|67a6 f367a6
movsd|67a5
popaw|6661
pushal|60
popal|61
pushf|669c
popf|669d
pushfd|9c
popfd|9d
xadd|0fc0c1 0fc1c1 0fc101 0fc04104 660fc1c1 660fc101 0fc1c0
cmpxchg|0fb0c1 0fb1c1 0fb101 0fb04104 660fb1c1 660fb101 0fb1c8
cmpxchg8b|0fc709 0fc74908
bsf|0fbcc1 0fbc01 660fbcc1 660fbc01
bsr|0fbdc1 0fbd01 660fbdc1 660fbd01
aaa|37
aas|3f
daa|27
das|2f
aam|d40a d405 d4ff
aad|d50a d505 d500
enter|c8100000 c8100001 c8100003 c8000002 66c8100000 66c8100001 66c8100003 66c8000002
loop|e210 e2fa
loope|e110 e1fa
loopne|e010 e0fa
bound|6201 666201
into|ce
int|cd03 cd04 cd05 cd06 cd29 cd2c cd2d cd2e
cli|fa
sti|fb
hlt|f4
in|ec ed
out|ee ef
insb|6c
outsb|6e
cpuid|0fa2
rdtsc|0f31
arpl|6308 6301 63c1 6309 63c8
jcxz|67e310 67e3fa
loop|67e210 67e2fa
loope|67e110
loopne|67e010
pause|f390
mfence|0faef0
lfence|0faee8
sfence|0faef8
clflush|0fae38 0fae7804
fldenv|d920 d96004 66d920 66d96004
fnstenv|d930 d97004 66d930 66d97004
fnsave|dd30 dd7004 66dd30 66dd7004
frstor|dd20 dd6004 66dd20 66dd6004
fbld|df20 df6004 df6424fc
fbstp|df30 df7004 df7424fc
fstpnce|d9d8 d9d9 d9da d9df
mov|678b00 678b01 678b02 678b03 678b04 678b05 678b063412 678b07 678b4010 678b4610 678b40f0 678b803412 678b863412 678b80f0ff 678a4710 67a13412 67a03412 67a33412 67a23412 67c700ffff0000 67c6400101 66678b00 66678b4210 678b84ff7f
lea|678d00 678d01 678d4010 678d803412 678d863412 678d40f0 678d80f0ff 66678d00 66678d4210
add|670300 67014010 67028412ff 66670340ff
xchg|678700 678640ff
movzx|670fb600 670fb7801000 670fb6863412
push|67ff30 67ff7010 67ff363412
fld|67d900 67dd4010 67d9863412
movups|670f1000 670f11801234
'''
SPEC = []
for line in ENC.strip().split('\n'):
    mn, hx = line.split('|'); SPEC.extend((mn, bytes.fromhex(h)) for h in hx.split() if len(h) % 2 == 0)
fake = {}
class FImage(fnvlift.Image):
    def bytes(self, a, n):
        if a in fake: return fake[a][:n] + b'\x90' * max(0, n - len(fake[a]))
        return super().bytes(a, n)
orig_imgpage = T.imgpage
def imgpage(p):
    if 0x7d000000 <= p < 0x7d100000:
        b = bytearray(4096)
        for a, r in fake.items():
            if a & ~0xfff == p: b[a - p:a - p + len(r)] = r
        return bytes(b)
    return orig_imgpage(p)
# expected `int imm8` rows (fnvlift/f_sys.py INT_VECTORS): vector -> substring of the generated C
INT_EXPECT = {0x03: 'fnvl_raise(FNVL_BP,0x%xu)', 0x04: 'fnvl_raise(FNVL_INTO,0x%xu)', 0x05: 'fnvl_raise(FNVL_BOUNDS,0x%xu)', 0x2c: 'fnvl_raise(FNVL_ASSERT,0x%xu)',
              0x2d: 'fnvl_raise(FNVL_BP,0x%xu)', 0x2e: 'fnvl_raise_av(0,0xffffffffu,0x%xu)', 0x06: 'fnvl_raise_av(0,0xffffffffu,0x%xu)', 0x29: 'fnvl_raise_av(0,0xffffffffu,0x%xu)'}
INT_AT = {0x03: 1, 0x04: 1, 0x05: 0, 0x2c: 0, 0x2d: 1}
A0 = 0x7d000000
RAV = 'fnvl_raise_av(0,0xffffffffu,0x%xu)' % A0
RPRIV = 'fnvl_raise(FNVL_PRIV,0x%xu)' % A0
RUD = 'fnvl_raise(FNVL_UD,0x%xu)' % A0
RLOCK = 'fnvl_raise(FNVL_LOCK,0x%xu)' % A0      # a #UD with an F0 byte among the first four bytes at Eip (KiTrap06Handler scans them all, not only the prefixes): STATUS_INVALID_LOCK_SEQUENCE
GSAV = 'if(SELK(c->sel[SEG_GS])==0){ SYNC_OUT; REGS(); fnvl_raise_av(0,0xffffffffu,0x%xu)' % A0      # a gs: access with GS = the null selector: #GP(0)
GPS = lambda i: 'if(!fnvl_seg_ok(%d,s)){ SYNC_OUT; REGS(); %s' % (i, 'fnvl_raise_av(0,0xffffffffu,0x%xu)' % A0)
NORAISE = '\x00'      # marker: the text must raise nothing (no fnvl_raise(FNVL_*) / fnvl_raise_av; the stub event of falling off the one-instruction function is not a raise)
# hex -> (what, substrings that must all occur; NORAISE: no raise of any kind). KiTrap0D rows (ReactOS traphdlr.c): privileged = F4; 0F 08 09 35 21 23 06 20 22 30 33; 0F 00 /2 (mask 38 == 10), 0F 00 18;
# 0F 01 (mask 38 == 10), 18, 30; CLI STI and the I/O opcodes at CPL 3 > IOPL 0. Everything else that is a #GP(0) at CPL 3: access violation.
CLASS_EXPECT = [
    ('f4', 'hlt', [RPRIV]), ('fa', 'cli', [RPRIV]), ('fb', 'sti', [RPRIV]), ('ec', 'in al,dx', [RPRIV]), ('ed', 'in eax,dx', [RPRIV]), ('e410', 'in al,imm', [RPRIV]), ('ee', 'out dx,al', [RPRIV]),
    ('6c', 'insb', [RPRIV]), ('f36e', 'rep outsb', [RPRIV]), ('6d', 'insd', [RPRIV]), ('66ef', 'out dx,ax', [RPRIV]),
    ('0f08', 'invd', [RPRIV]), ('0f09', 'wbinvd', [RPRIV]), ('f30f09', 'wbnoinvd = wbinvd', [RPRIV]), ('0f06', 'clts', [RPRIV]), ('0f30', 'wrmsr', [RPRIV]), ('0f33', 'rdpmc', [RPRIV]),
    ('0f35', 'sysexit', [RPRIV]), ('0f20c0', 'mov eax,cr0', [RPRIV]), ('0f22d8', 'mov cr3,eax', [RPRIV]), ('0f21c0', 'mov eax,dr0', [RPRIV]), ('0f23f8', 'mov dr7,eax', [RPRIV]),
    ('0f0110', 'lgdt [eax]', [RPRIV]), ('0f0118', 'lidt [eax] (exact ModRM 18 row)', [RPRIV]), ('0f0130', 'lmsw [eax] (exact ModRM 30 row)', [RPRIV]), ('0f0010', 'lldt [eax]', [RPRIV]),
    ('0f0018', 'ltr [eax] (exact ModRM 18 row)', [RPRIV]), ('0f015000', 'lgdt [eax+0]', [RPRIV]),
    ('0f32', 'rdmsr (not in the table)', [RAV]), ('0f0138', 'invlpg [eax] (not in the table)', [RAV]), ('0f011b', 'lidt [ebx] (ModRM 1b is not the exact row)', [RAV]),
    ('0f00d8', 'ltr ax (register form is not the exact row)', [RAV]), ('0f01f0', 'lmsw eax (ModRM f0 is not the exact row)', [RAV]),
    ('1f', 'pop ds', [GPS(3), 'fnvl_seg_set(c,3,s)', 'esp+=4u', 'M32(esp)&0xffffu']), ('07', 'pop es', [GPS(0), 'fnvl_seg_set(c,0,s)']), ('17', 'pop ss', [GPS(2), 'fnvl_seg_set(c,2,s)']),
    ('0fa1', 'pop fs', [GPS(4), 'fnvl_seg_set(c,4,s)', 'fsb=c->fs;']), ('0fa9', 'pop gs', [GPS(5), 'fnvl_seg_set(c,5,s)']), ('661f', 'pop ds (16-bit)', [GPS(3), 'esp+=2u', 'M16(esp)']),
    ('8ed8', 'mov ds,eax', [GPS(3), 'fnvl_seg_set(c,3,s)', '(eax)&0xffffu']), ('8e00', 'mov es,[eax]', [GPS(0), 'M16(']), ('8ee0', 'mov fs,eax', [GPS(4), 'fsb=c->fs;']),
    ('8ed0', 'mov ss,eax', [GPS(2)]), ('8ee8', 'mov gs,eax', [GPS(5)]), ('8ec8', 'mov cs,eax (#UD)', [RUD]),
    ('c401', 'les eax,[ecx]', [GPS(0), 'fnvl_seg_set(c,0,s)', 'M32(ad+4u)' if False else 'u32 s=M16(ad+4u)']), ('c501', 'lds eax,[ecx]', [GPS(3)]), ('0fb201', 'lss eax,[ecx]', [GPS(2)]),
    ('0fb401', 'lfs eax,[ecx]', [GPS(4), 'fsb=c->fs;']), ('0fb501', 'lgs eax,[ecx]', [GPS(5)]), ('66c501', 'lds ax,[ecx]', [GPS(3), 'u32 s=M16(ad+2u)', 'u32 o=M16(ad)']),
    ('ea785634121b00', 'ljmp 1b:12345678', ['u32 s=0x1bu;', 'SELK(s)!=SELK(SEL_CS)', 'c->ra=0x12345678u; goto nonlocal;', RAV]),
    ('66ea78561b00', 'ljmp16 1b:5678', ['c->ra=0x5678u; goto nonlocal;']),
    ('9a785634121b00', 'lcall 1b:12345678', ['SELK(s)!=SELK(SEL_CS)', 'M32(esp)=SEL_CS', 'M32(esp)=0x%xu' % (A0 + 7), 'fnvl_dispatch(t)()']),
    ('669a78561b00', 'lcall16', ['esp-=2u; M16(esp)=SEL_CS', 'M16(esp)=0x%xu' % (A0 + 6)]),
    ('ff2d78563412', 'jmp far [mem] (ff /5)', ['t=M32(ad), s=M16(ad+4u)', 'SELK(s)!=SELK(SEL_CS)', 'c->ra=t; goto nonlocal;']),
    ('ff1d78563412', 'call far [mem] (ff /3)', ['t=M32(ad), s=M16(ad+4u)', 'M32(esp)=SEL_CS', 'fnvl_dispatch(t)()']),
    ('66ff2d78563412', 'jmp far m16:16', ['t=M16(ad), s=M16(ad+2u)']),
    ('cb', 'retf', ['s!=SEL_CS', 'esp+=8u', RAV]), ('ca0400', 'retf 4', ['esp+=12u']), ('66cb', 'retf (16-bit)', ['M16(esp)', 'esp+=4u']),
    ('cf', 'iretd', ['s!=SEL_CS', 'EFL_POP(fl);', 'esp+=12u', 'fl=M32(esp+8u)']), ('66cf', 'iret', ['esp+=6u', 'c->eflx']),
    ('f1', 'int1 = #DB trap, next instruction', ['fnvl_raise(FNVL_STEP,0x%xu)' % (A0 + 1)]),
    ('6301', 'arpl [ecx],ax', ['zf=1', '&~3u']), ('0f02c1', 'lar eax,ecx', ['fnvl_gdt_hi(', '(hi&0x00ffff00u)', 'zf=1', NORAISE]), ('0f03c1', 'lsl eax,ecx', ['fnvl_gdt_limit(hi)']),
    ('0f00e0', 'verr ax', ['zf=hi!=0']), ('0f00e8', 'verw ax', ['!(hi&0x800u)']),
    ('0f0000', 'sldt [eax]', ['FNVL_LDTR_SEL']), ('0f0008', 'str [eax]', ['FNVL_TR_SEL']), ('0f01e0', 'smsw eax', ['FNVL_CR0']), ('0f0100', 'sgdt [eax]', ['FNVL_GDTR_LIMIT', 'FNVL_GDTR_BASE']),
    ('0f0108', 'sidt [eax]', ['FNVL_IDTR_LIMIT', 'FNVL_IDTR_BASE']),
    ('67e3fa', 'jcxz', ['(ecx&0xffffu)==0']), ('67e2fa', 'loop (CX)', ['ecx=(ecx&0xffff0000u)|((ecx-1)&0xffffu)', '(ecx&0xffffu)!=0']),
    ('f390', 'pause', ['yield']), ('0faef0', 'mfence', ['__sync_synchronize']), ('0fae38', 'clflush [eax]', ['M8(']), ('f30fbcc1', 'tzcnt = bsf', ['__builtin_ctz']), ('f30fbdc1', 'lzcnt = bsr', ['__builtin_clz']),
    ('0f1c00', 'cldemote (reserved NOP)', [NORAISE]), ('f30f1efb', 'endbr32', [NORAISE]), ('0f1a00', 'bndldx', [NORAISE]), ('0f0d00', 'prefetch', [NORAISE]), ('0f19c0', 'nop eax,eax', [NORAISE]),
    ('f30f1ec8', 'rdsspd', [NORAISE]),
    ('658b00', 'mov eax,gs:[eax]', [GSAV, 'M32((u32)(FNVL_GS_BASE()+eax))']), ('658900', 'mov gs:[eax],eax: also #GP when GS holds CS', ['if(SELK(c->sel[SEG_GS])==0||SELK(c->sel[SEG_GS])==SELK(SEL_CS)){']),
    ('65a100000000', 'mov eax,gs:[0]', [GSAV, 'M32((u32)(FNVL_GS_BASE()))']), ('65ff30', 'push gs:[eax]', [GSAV]), ('65678b00', 'mov eax,gs:[bx+si]', [GSAV, 'FNVL_GS_BASE()+((ebx+esi*1u)&0xffffu)']),
    ('658d00', 'lea eax,gs:[eax]: no access, no guard', [NORAISE, 'FNVL_GS_BASE()']), ('648b00', 'mov eax,fs:[eax]: base c->fs, no guard', [NORAISE, 'fsb+eax']),
    ('8b00', 'mov eax,[eax]: default segment, no guard', [NORAISE]), ('658b00', 'GS guard comes before the access', ['SELK(c->sel[SEG_GS])==0']),
    ('8cc8', 'mov eax,cs reads the state', ['fnvl_seg_get(c,1)']), ('8ce8', 'mov eax,gs reads the state', ['fnvl_seg_get(c,5)']), ('0fa8', 'push gs reads the state', ['fnvl_seg_get(c,5)']),
    ('678b00', 'mov eax,[bx+si] (16-bit addressing)', ['((ebx+esi*1u)&0xffffu)']), ('678a4710', 'mov al,[bx+0x10]', ['((ebx+0x10u)&0xffffu)']), ('67a13412', 'mov eax,[0x1234]', ['((0x1234u)&0xffffu)']),
    ('678b4600', 'mov eax,[bp]', ['((ebp)&0xffffu)']), ('678b40f0', 'mov eax,[bx+si-16] wraps in 16 bits', ['((ebx+esi*1u+0xfffffff0u)&0xffffu)']), ('678d803412', 'lea eax,[bx+si+0x1234]', ['((ebx+esi*1u+0x1234u)&0xffffu)']),
    ('8b4010', 'mov eax,[eax+0x10]: 32-bit addressing is not masked', ['(u32)(eax+0x10u)']),
    ('f08b0d3cea1d01', 'lock mov (undecodable)', [RLOCK]), ('f0f7d0', 'lock not eax (register destination)', [RLOCK]), ('f00f1f00', 'lock nop [eax]', [RLOCK]), ('f001c8', 'lock add eax,ecx', [RLOCK]),
    ('f0a4', 'lock movsb', [RLOCK]), ('f090', 'lock nop', [RLOCK]), ('f00108', 'lock add [eax],ecx: valid', [NORAISE, 'LK_BEGIN(32)', 'LK32(', 'lk_commit(']), ('f0870a', 'lock xchg [edx],ecx: valid', [NORAISE, 'LK32(', 'lk_commit(']), ('870a', 'xchg [edx],ecx locks without the prefix', ['LK_BEGIN(32)', 'LK32(', 'lk_commit(']), ('87ca', 'xchg edx,ecx: no memory operand, no lock', [NORAISE]),
    ('f00fc70a', 'lock cmpxchg8b: valid', [NORAISE, 'LK_BEGIN(64)', 'LK64(', 'lk_commit(']), ('f00fb10a', 'lock cmpxchg [edx],ecx: valid', [NORAISE]), ('f00fab08', 'lock bts [eax],ecx: valid', [NORAISE, 'LK32(ad)', 'lk_commit(']), ('f0ff08', 'lock dec [eax]: valid', [NORAISE]),
    ('f001c801', 'lock add eax,ecx + tail', [RLOCK]), ('f2f001c8', 'xacquire lock add eax,ecx', [RLOCK]),
    ('0f0b|f0', 'ud2, F0 is the third byte at Eip', [RLOCK]), ('0f0b|90f0', 'ud2, F0 is the fourth byte at Eip', [RLOCK]), ('0f0b|909090f0', 'ud2, F0 is the fifth byte: outside the 4-byte scan', [RUD]),
    ('f2700a', 'bnd jo', ['if(of)']), ('f2c3', 'bnd ret', ['return;']), ('3effe0', 'notrack jmp eax', ['c->ra=eax']), ('f2f00108', 'xacquire lock add [eax],ecx', ['LK32(']),
    ('f30fb8c1', 'popcnt', [RUD]), ('0f38f001', 'movbe (F0 is its opcode byte: KiTrap06 reports a lock sequence)', [RLOCK]), ('f20f38f1c1', 'crc32', [RUD]), ('0fc7f0', 'rdrand (F0 = its ModRM byte)', [RLOCK]), ('0f01d0', 'xgetbv', [RUD]), ('0fae20', 'xsave', [RUD]), ('0f05', 'syscall', [RUD]),
    ('0f07', 'sysret', [RUD]), ('0f01f8', 'swapgs', [RUD]), ('0f01c1', 'vmcall', [RUD]), ('0f01c8', 'monitor', [RUD]), ('0fb9c1', 'ud1', [RUD]), ('0fff00', 'ud0', [RUD]), ('c5f877', 'vzeroupper', [RUD]),
    ('c5e1e3c0', 'vpavgw', [RUD]), ('c4e171e300', 'vpavgw 3-byte VEX, memory', [RUD]), ('0f380000', 'pshufb', [RUD]), ('660f3a0f0000', 'palignr', [RUD]), ('f20f7c00', 'haddps', [RUD]), ('c4e272f7c1', 'sarx', [RUD]), ('62f17c4858c0', 'evex vaddps', [RUD]),
    ('db08', 'fisttp m32 (x87 opcode gated by SSE3: f_cpufeat)', [RUD]), ('dd08', 'fisttp m64 (SSE3)', [RUD]), ('df08', 'fisttp m16 (SSE3)', [RUD]), ('f20ff000', 'lddqu (SSE3: has an SSE handler, the CPUID-UD rule comes first; its opcode byte F0 is KiTrap06 lock-sequence data, like movbe above)', [RLOCK]),
    ('db18', 'fistp m32 (x87, no feature gate)', [NORAISE]), ('f30f6f00', 'movdqu (SSE2, in the model)', [NORAISE]), ('0f6f00', 'movq mm0,[eax] (MMX, in the model)', [NORAISE]), ('0f77', 'emms (MMX, in the model)', [NORAISE]),
    ('c7f800000000', 'xbegin', [RUD]), ('0f0b', 'ud2', ['fnvl_raise(FNVL_UD,']), ('0f01d6', 'xtest', [RUD]), ('0faee0', 'ptwrite? (F3 0F AE /4 needs F3: this is 0F AE E0 = xsave-like #UD)', []),
]
# reserved-NOP bytes capstone refuses: (hex, expected instruction length or None). Lengths cross-checked against the Unicorn oracle over the whole 0F 0D / 0F 18..1F ModRM grid (340 encodings, 0 mismatches).
NOPGAP = [('0f1ed0', 3), ('0f1a20', 3), ('0f1b3800', 3), ('660f1c4004', 5), ('0f1b0500000000', 7), ('0f1d0424', 4), ('0f1d042500000000', 8), ('670f1c4000', 5), ('670f1c0600', 6), ('0f0d18', 3),
          ('0f0dc0', None), ('0f18c0', None), ('f00f1ed0', None), ('0f1fd0', 3)]
def nopgap():
    bad = 0
    for hx, n in NOPGAP:
        x = fnvlift.f_sys.nop_gap(bytes.fromhex(hx) + b'\0' * 8); got = x.size if x else None; ok = got == n; bad += not ok; print('nop_gap %-18s %s %s' % (hx, 'ok' if ok else 'FAIL', got))
    return bad
LOCKS = [('f00104', 'add', True), ('f0ff08', 'dec', True), ('f0870a', 'xchg', True), ('f00fb10a', 'cmpxchg', True), ('f00fc70a', 'cmpxchg8b', True), ('f00fab08', 'bts', True), ('f00f1f00', 'nop', False),
         ('f08b0d3cea1d01', 'mov', False), ('f0f7d0', 'not', False), ('f0ff4008', 'inc', True), ('f00108', 'add', True), ('f00fc108', 'xadd', True)]
def lockcheck():
    bad = 0
    for hx, m, want in LOCKS:
        g = list(fnvlift.core.md.disasm(bytes.fromhex(hx) + b'\0' * 8, A0, 1))
        if not g: print('lock_ok %-16s undecodable (the lifter makes it #UD)' % hx); bad += want; continue
        mm = g[0].mnemonic.split(' ', 1)[-1] if g[0].mnemonic.startswith('lock ') else g[0].mnemonic; got = fnvlift.f_sys.lock_ok(g[0], mm); ok = got == want and mm == m; bad += not ok
        print('lock_ok %-16s %-10s %s' % (hx, mm, 'ok' if ok else 'FAIL got %s want %s' % (got, want)))
    return bad
def class_tables(img):
    bad = nopgap() + lockcheck()
    for hx, what, subs in CLASS_EXPECT:
        hx, _, tail = hx.partition('|'); raw = bytes.fromhex(hx); a = A0; fake[a] = raw + bytes.fromhex(tail) + b'\x90' * 8      # `fn|tail`: bytes after the function that the F0 scan of KiTrap06 still sees
        try: txt = fnvlift.lift(img, a, len(raw), strict=False)[0]
        except Exception as e: print('%-22s %-44s FAIL lift: %s' % (hx, what, e)); bad += 1; continue
        miss = [x for x in subs if x != NORAISE and x not in txt] + (['no raise of any kind'] if NORAISE in subs and ('fnvl_raise(FNVL' in txt or 'fnvl_raise_av' in txt) else [])
        bad += bool(miss); print('%-22s %-44s %s' % (hx, what, 'ok' if not miss else 'FAIL missing/forbidden: %s' % miss))
    return bad
# ---- X1: x87 environment loads, packed BCD, fstpnce, VEX (vpavgw) ----------------------------------------------------------------------------------------------------
# fldenv / frstor: the oracle is Unicorn. Assumption A-EMPTY (f_x87_ctl.py env()): a register the loaded tag word marks non-empty while it was empty holds the real indefinite in the
# lifted code, a stale value in the oracle (QEMU keeps the register file). Those registers are masked in the comparison and COUNTED ("masked fields" below), never silently equal.
# The FIP / FCS / FOP / FDP / FDS words of the image are not part of any state the lifted code keeps (open class question Q-FIP); a load discards them and the oracle's are checked by
# nobody because the only observers are fnstenv / fnsave, which are not lifted (no case here).
# fbstp: QEMU's helper does not implement the packed-BCD indefinite (SDM: NaN / infinity / |x| >= 10^18 after rounding store FFFF C000 0000 0000 0000), so the expected bytes come from an
# independent Python reference over the exact 80-bit value (Fraction arithmetic, RC from the control word).
# VEX (vpavgw): the reference CPU model reports AVX = 0 and OSXSAVE = 0 (rt/cpumodel.c, leaf 1 ECX = 0), so every VEX-encoded instruction raises #UD: the expected oracle outcome is the
# fault class #UD (trap4) with the architectural state unchanged, whatever Unicorn does with the bytes.
import fractions
MASKED = 'masked'
def is_vex(raw):
    i = 0
    while i < len(raw) and raw[i] in T.PFX: i += 1
    return i + 1 < len(raw) and raw[i] in (0xc4, 0xc5) and raw[i + 1] & 0xc0 == 0xc0       # in 32-bit mode C4/C5 with ModRM.mod == 3 is VEX (else LES / LDS)
def bcd_ref(m, se, rc):
    sg, e = se >> 15, se & 0x7fff; ind = bytes([0] * 7 + [0xc0, 0xff, 0xff])
    if e == 0x7fff or (e and not m >> 63): return ind
    mag = fractions.Fraction(m) * fractions.Fraction(2) ** ((e or 1) - 16383 - 63); fl = mag.numerator // mag.denominator; fr = mag - fl
    up = (fr > fractions.Fraction(1, 2) or (fr == fractions.Fraction(1, 2) and fl % 2 == 1)) if rc == 0 else (bool(sg) and fr > 0) if rc == 1 else (not sg and fr > 0) if rc == 2 else False
    r = fl + up
    if r >= 10 ** 18: return ind
    return bytes((((r // 100 ** i) % 100 // 10) << 4) | ((r // 100 ** i) % 10) for i in range(9)) + bytes([sg << 7])
orig_x87_range, orig_compare, orig_run = T.x87_range, T.compare, T.Oracle.run
def x87_range(E):
    b, mr = E['base'], E['ins'].modrm
    if b == 'fbld': return 0, 7                                 # pushes
    if b in ('fldenv', 'frstor', 'fnstenv', 'fnsave'): return 0, 8   # replace / read the whole state
    if b == 'fstpnce': return max(1, (mr & 7) + 1), 8           # pops, needs st(i)
    return orig_x87_range(E)
def oracle_run(s, E, st):
    if is_vex(E['raw']):
        return dict(regs=list(st['regs']), eip='trap4', fl=st['fl'] & 0xcd5, eflx=st['eflx'] & 0x240000, mem={}, fp=None, xmm=list(st['xmm']), mm=None, mxcsr=st['mxcsr'], wr=[])
    return orig_run(s, E, st)
# fnstenv / fnsave (X1b): the image fields FIP FCS FOP FDP FDS depend on the last non-control x87 instruction, which Unicorn/QEMU does not model (its helper stores 0 there). The lifted side is
# seeded with a known last-instruction state (c->fip, c->fdp, c->sel[] written into the harness CPU block before the run); the expected fields come from an independent Python model of SDM
# Vol 1 8.1.8 (FIP = the instruction address, FCS = CS, FOP = (first opcode byte & 7) << 8 | second opcode byte after the prefixes, FDP = the operand offset, FDS = DS when FDP != 0; FIP = 0
# is the FNINIT state, all five fields 0) and replace the oracle's bytes: every such field is COUNTED in "oracle-unmodelled fields", never silently equal. The sticky exception bits of the FSW word
# follow the fnstsw convention (not compared) and are counted as "fsw sticky bits masked". An empty register's image in fnsave (stale value in QEMU, real indefinite in the lifted code: A-EMPTY) is
# counted in the A-EMPTY masks. Everything else of the image is compared against the oracle byte for byte.
CODE0 = 0x7d0f0000
SEEDS = [(0, 0, b''), (CODE0, 0x30001234, bytes.fromhex('d94508')), (CODE0 + 0x10, 0x7ffdf00c, bytes.fromhex('66dd4508')), (CODE0 + 0x20, 0x1000, bytes.fromhex('dec1')),
         (CODE0 + 0x30, 0xffffff00, bytes.fromhex('3ed9c0')), (CODE0 + 0x40, 0x4, bytes.fromhex('f2dd45f8'))]
for a_, d_, r_ in SEEDS:
    if r_: fake[a_] = r_
PFXS = (0x66, 0x67, 0xf0, 0xf2, 0xf3, 0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65)
def fseed(E, st): return SEEDS[st['wk'][0] % len(SEEDS)]
def model_fields(seed, d32):
    """[(offset, size, value)] of FIP, FCS(+FOP), FDP, FDS in the image"""
    ip, dp, code = seed
    i = 0
    while code and code[i] in PFXS: i += 1
    fop = ((code[i] & 7) << 8 | code[i + 1]) if ip else 0
    cs, ds = (0x1b if ip else 0), (0x23 if dp else 0)
    return [(12, 4, ip), (16, 4, cs | fop << 16), (20, 4, dp), (24, 4, ds)] if d32 else [(6, 2, ip & 0xffff), (8, 2, cs), (10, 2, dp & 0xffff), (12, 2, ds)]
orig_arm_run = T.Arm.run
def arm_run(s, E, st):
    ip, dp, _ = fseed(E, st)
    s.uc.mem_write(T.CPU + 400, struct.pack('<6I', 0x23, 0x1b, 0x23, 0x23, 0x3b, 0)); s.uc.mem_write(T.CPU + 428, struct.pack('<2I', ip, dp))
    return orig_arm_run(s, E, st)
T.Arm.run = arm_run
def mset(mem, st, a, v):
    if v == T.origbyte(st, a): mem.pop(a, None)
    else: mem[a] = v
def mget(mem, st, a): return mem.get(a, T.origbyte(st, a))
def env_compare(E, st, o, c):
    """fnstenv / fnsave: returns (unmodelled fields, sticky bits masked, A-EMPTY registers masked)"""
    a0 = min(a for a, n in o['wr']); d32 = E['raw'][0] != 0x66; om, cm = dict(o['mem']), dict(c['mem']); u = 0
    for off, n, v in model_fields(fseed(E, st), d32):
        for k in range(n): mset(om, st, a0 + off + k, v >> 8 * k & 0xff)
        u += 1
    fo = 4 if d32 else 2
    for mem in (om, cm):
        mset(mem, st, a0 + fo, mget(mem, st, a0 + fo) & ~0xff & 0xff); mset(mem, st, a0 + fo + 1, mget(mem, st, a0 + fo + 1) & ~0x82 & 0xff)
    e = 0
    if E['base'] == 'fnsave':
        h = 28 if d32 else 14
        for i in range(8):
            if not st['valid'] >> ((st['ftop'] + i) & 7) & 1:
                for k in range(10): mset(om, st, a0 + h + 10 * i + k, mget(cm, st, a0 + h + 10 * i + k))
                e += 1
    o['mem'], c['mem'] = om, cm
    return u, 2, e
def compare(E, st, o, c, X):
    b, masked = E['base'], 0
    if b == 'fbstp' and o.get('wr') and 'fault' not in c:
        a0 = min(a for a, n in o['wr']); rc = st['fcw'] >> 10 & 3; m, se = img80(*st['fx'][st['ftop']])
        want = bcd_ref(m, se, rc); o['mem'] = {a0 + k: v for k, v in enumerate(want) if v != T.origbyte(st, a0 + k)}
    if b in ('fldenv', 'frstor') and o.get('fp') and 'fault' not in c and c.get('fp'):
        for p in range(8):
            if o['fp']['valid'] >> p & 1 and not st['valid'] >> p & 1 and b == 'fldenv' and c['fp']['valid'] >> p & 1:
                c['fp']['st'][p] = (o['fp']['st'][p][0], o['fp']['st'][p][1], 6); masked += 1
    u = sk = 0
    if b in ('fnstenv', 'fnsave') and o.get('wr') and 'fault' not in c:
        u, sk, e = env_compare(E, st, o, c); masked += e
    r = orig_compare(E, st, o, c, X)
    return (r[0], MASKED + ':%d/%d/%d' % (masked, u, sk) if r[0] == 'equal' and (masked or u) else r[1]) + tuple(r[2:])
T.x87_range, T.compare, T.Oracle.run = x87_range, compare, oracle_run
img80 = T.img80
def masked_report(work):
    import json
    t = [0, 0, 0]
    for v in json.load(open(work + '/result.json')).values():
        for e in v['encodings']:
            for r in e['cases']:
                if r[0] == 'equal' and r[1].startswith(MASKED + ':'):
                    for k, x in enumerate(r[1].split(':')[1].split('/')): t[k] += int(x)
    print('masked fields: %d  (A-EMPTY: fldenv registers revived by the tag word, fnsave images of empty registers)' % t[0])
    print('oracle-unmodelled fields: %d  (FIP/FCS/FOP/FDP/FDS of fnstenv/fnsave images: asserted against the SDM model of this file, QEMU stores 0)' % t[1])
    print('fsw sticky bits masked: %d  (fnstsw convention, exception bits / ES / B / C1 are not compared)' % t[2])
def tables():
    img = FImage(os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')); bad = 0
    PRIVS = [('fa', 'FNVL_PRIV'), ('fb', 'FNVL_PRIV'), ('f4', 'FNVL_PRIV'), ('ee', 'FNVL_PRIV'), ('e6 10'.replace(' ', ''), 'FNVL_PRIV'), ('6c', 'FNVL_PRIV'), ('f36e', 'FNVL_PRIV'), ('ce', 'FNVL_INTO'), ('cc', 'FNVL_BP')]
    for v, sub in sorted(INT_EXPECT.items()):
        a = 0x7d000000; fake[a] = bytes([0xcd, v]); txt = fnvlift.lift(img, a, 2, strict=False)[0]
        want = sub % (a + INT_AT.get(v, 0)); ok = want in txt; bad += not ok; print('int 0x%02x %s %s' % (v, 'ok' if ok else 'FAIL', want))
    bad += class_tables(img)
    for hx, code in PRIVS:
        a = 0x7d000000; fake[a] = bytes.fromhex(hx); txt = fnvlift.lift(img, a, len(fake[a]), strict=False)[0]; ok = code in txt; bad += not ok; print('%s %s %s' % (hx, 'ok' if ok else 'FAIL', code))
    # a direct transfer under prefix bytes (capstone prints them as prefix words: `repne jne`, `bnd jmp`, `repe jmp`): the target is reached by the body's descent
    # exactly as for the bare instruction, so the label the emitted `goto` names is defined. Expectation by hand: the target instruction (inc eax) is decoded.
    for hx in ('f27502', 'f37502', 'f2eb02', 'f3eb02', 'f2e902000000', 'f30f8502000000', '2e7502', '3e7502', 'f2f37502'):
        a = 0x7d000000; raw = bytes.fromhex(hx); fake[a] = raw + b'\xc3\xc3\x40\xc3'; n = len(fake[a]); t = a + len(raw) + 2
        ins = fnvlift.core.explore(img, a, a + n, img.bytes(a, n))[0]; txt = fnvlift.lift(img, a, n, strict=False)[0]
        ok = t in ins and ins[t].mnemonic == 'inc' and ('L_%08x:' % t) in txt; bad += not ok; print('prefixed transfer %s %s' % (hx, 'ok' if ok else 'FAIL'))
    sys.exit(1 if bad else 0)
def main():
    only = sys.argv[sys.argv.index('--only') + 1].split(',') if '--only' in sys.argv else None
    forms = {}; idx = 0
    for mn, raw in SPEC:
        if only and mn not in only: continue
        a = 0x7d000000 + idx * 0x20; idx += 1; fake[a] = raw
        g = list(fnvlift.core.md.disasm(raw, a))
        if not g or g[0].size != len(raw): print('BAD ENCODING', mn, raw.hex(), file=sys.stderr); continue
        key = T.form_key(g[0], X)[0]; f = forms.setdefault(key, [1, 1, {}]); f[2][raw] = a
    mnf = {k.split('|')[0]: 1 for k in forms}
    T.imgpage = imgpage; fnvlift.Image = FImage; T.LOWOK = True      # 16-bit addressing forms reach the low 64 KiB
    T.load_forms = lambda a, rows: dict(forms=forms, mnf=mnf, nfuncs=1, undecodable=0)
    sys.argv = [sys.argv[0], '--work', '/home/user/l3work/xinstest', '--k', '40'] + [x for x in sys.argv[1:] if x not in ('--only',) and not (only and x == ','.join(only))]
    T.main()
    masked_report(sys.argv[len(sys.argv) - sys.argv[::-1].index('--work')])      # the last --work wins (argparse)
if __name__ == '__main__':
    if '--tables' in sys.argv: tables()
    else: main()
