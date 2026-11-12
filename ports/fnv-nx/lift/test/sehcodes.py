#!/usr/bin/env python3
"""C-level check of rt/seh.cc's exception-code mapping and of the segment state (rt/fnvl.h fnvl_seg_*): rt/seh.cc is compiled for AArch64 ILP32 and run under Unicorn with a driver.
    sehcodes.py
The driver calls fnvl_raise(code, at) / fnvl_raise_av on a struct cpu laid out in guest memory with an EMPTY SEH chain (fs:[0] = -1), so the record and the CONTEXT go to the unhandled hook of the
driver, which keeps their addresses. Compared against hand-written rows (NTSTATUS, ExceptionAddress, number of parameters, Context.Eip): the Windows 7 trap handlers as ReactOS transcribes them
(KiTrap00/01/03/04/05/06/0D, KiRaiseAssertion). Also: the segment selectors in the CONTEXT are the CURRENT ones (c->sel after fnvl_seg_set), FS load re-bases c->fs, and FNVL_GS_BASE() follows the GS selector."""
import os, struct, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE); sys.path.insert(0, HERE); sys.path.insert(0, LIFT)
import instest as T, fnvlift as L
from unicorn import *
from unicorn.arm64_const import *
from elftools.elf.elffile import ELFFile
W = '/home/user/l3work/sehcodes'; CPU, STK, FSB, RET, SEL = 0xE1000000, 0x10100000, 0x7FFDF000, 0xE1FF0000, 400
DRV = L.PRELUDE + '''
extern "C" { u32 g_rec, g_ctx; u32 fnvl_os_unhandled(u32 r, u32 x){ g_rec=r; g_ctx=x; return 0; } void (*fnvl_dispatch(u32 a))(void){ return 0; }
  void drv_raise(u32 code, u32 at){ fnvl_rt::fnvl_raise(code, at); }
  void drv_av(u32 at){ fnvl_rt::fnvl_raise_av(0, 0xffffffffu, at); }
  void drv_seg(u32 idx, u32 sel){ fnvl_seg_set(c, idx, sel); eax = c->fs; ecx = fnvl_seg_get(c, idx); edx = FNVL_GS_BASE(); }
  void drv_ctx(u32 idx, u32 sel){ fnvl_seg_set(c, idx, sel); fnvl_rt::fnvl_raise(FNVL_UD, 0x401000u); } }
'''
STUBS = ('.text\n.globl memset\nmemset: mov x3,x0\n1: cbz x2,2f\n strb w1,[x3],#1\n sub x2,x2,#1\n b 1b\n2: ret\n.globl memcpy\nmemcpy: mov x3,x0\n1: cbz x2,2f\n ldrb w4,[x1],#1\n strb w4,[x3],#1\n'
         ' sub x2,x2,#1\n b 1b\n2: ret\n')


def build():
    os.makedirs(W, exist_ok=True); fl = [f for f in T.FLAGS if f != '-DFNVL_FLAGS_IO'] + ['-I', LIFT + '/rt']
    open(W + '/drv.cpp', 'w').write(DRV); open(W + '/stubs.S', 'w').write(STUBS)
    for src, extra in ((W + '/drv.cpp', []), (LIFT + '/rt/seh.cc', ['-x', 'c++'])):
        subprocess.check_call(['aarch64-linux-gnu-g++'] + fl + extra + [src, '-o', W + '/' + os.path.basename(src) + '.o'])
    subprocess.check_call(['aarch64-linux-gnu-gcc', '-mabi=ilp32', '-c', W + '/stubs.S', '-o', W + '/stubs.S.o'])
    subprocess.check_call(['aarch64-linux-gnu-ld', '-m', 'aarch64elf32', '-Ttext=0xE0000000', '-e', '0', '-o', W + '/drv.elf'] + [W + '/%s.o' % n for n in ('drv.cpp', 'seh.cc', 'stubs.S')])
    with open(W + '/drv.elf', 'rb') as fh:
        e = ELFFile(fh); segs = [(p['p_vaddr'], p.data()) for p in e.iter_segments() if p['p_type'] == 'PT_LOAD']
        return segs, {x.name: x['st_value'] for x in e.get_section_by_name('.symtab').iter_symbols() if x.name}


def run(segs, sym, fn, args, sel):
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM); uc.mem_map(0xE0000000, 0x2000000); uc.mem_map(0x10000000, 0x200000); uc.mem_map(FSB, 0x1000)
    for va, d in segs: uc.mem_write(va, d)
    uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)
    uc.mem_write(CPU, b'\0' * 504); uc.mem_write(CPU + 32, struct.pack('<I', FSB)); uc.mem_write(CPU + 40, struct.pack('<I', 0x27f)); uc.mem_write(CPU + SEL, struct.pack('<7I', *sel, FSB))
    uc.mem_write(FSB, struct.pack('<I', 0xffffffff))
    uc.reg_write(UC_ARM64_REG_X27, CPU); uc.reg_write(UC_ARM64_REG_X23, STK)
    for k, v in zip((UC_ARM64_REG_X0, UC_ARM64_REG_X1), args): uc.reg_write(k, v)
    uc.reg_write(UC_ARM64_REG_SP, 0xE1F00000); uc.reg_write(UC_ARM64_REG_X30, RET)
    uc.emu_start(sym[fn], RET, timeout=20000000, count=2000000)
    rd = lambda a, n=1: struct.unpack('<%dI' % n, bytes(uc.mem_read(a, 4 * n)))
    return uc, rd


def main():
    segs, sym = build(); bad = 0; PROC = (0x23, 0x1b, 0x23, 0x23, 0x3b, 0)       # ES CS SS DS FS GS of the process (fnvl_seg_init)
    A = 0x401000
    # code -> (NTSTATUS, ExceptionAddress, NumberParameters, Context.Eip)
    ROWS = {1: (0xC0000094, A, 0, A), 5: (0xC0000095, A, 0, A), 3: (0x80000003, A, 3, A), 4: (0xC000001D, A, 0, A), 6: (0xC0000096, A, 0, A), 7: (0xC000008C, A, 0, A),
            8: (0xC0000095, A, 0, A + 1), 9: (0xC0000420, A, 0, A), 10: (0x80000004, A, 0, A), 11: (0xC000001E, A, 0, A),
            12: (0x80000003, A + 3, 3, A + 2)}   # 3: int3 and 12: int 2d with eax = 0: Wine 9.0 unix/signal_i386.c trap_handler / handle_interrupt (rt/seh.cc cites them)
    for code, want in sorted(ROWS.items()):
        uc, rd = run(segs, sym, 'drv_raise', (code, A), PROC); rec = rd(rd(sym['g_rec'])[0], 5); ctx = rd(rd(sym['g_ctx'])[0] + 184)[0]; got = (rec[0], rec[3], rec[4], ctx)
        bad += got != want; print('fnvl_raise(%2d) %s %s' % (code, 'ok' if got == want else 'FAIL', ' '.join('%x' % x for x in got)))
    uc, rd = run(segs, sym, 'drv_av', (A,), PROC); r = rd(rd(sym['g_rec'])[0], 7); got = (r[0], r[3], r[4], r[5], r[6]); want = (0xC0000005, A, 2, 0, 0xffffffff); bad += got != want
    print('fnvl_raise_av  %s %s' % ('ok' if got == want else 'FAIL', ' '.join('%x' % x for x in got)))
    # segment state: (idx, selector) -> (c->fs after, c->sel[idx], FNVL_GS_BASE() with the GS selector as loaded; 0 for another register: GS stays null)
    for idx, sel, fs, gsb in ((4, 0x23, 0, 0), (4, 0x3b, FSB, 0), (4, 0, 0, 0), (5, 0x3b, FSB, FSB), (5, 0x23, FSB, 0), (5, 0x1b, FSB, 0), (3, 0x23, FSB, 0)):
        uc, rd = run(segs, sym, 'drv_seg', (idx, sel), PROC); got = tuple(uc.reg_read(k) & 0xffffffff for k in (UC_ARM64_REG_X19, UC_ARM64_REG_X20, UC_ARM64_REG_X21))
        want = (fs, sel, gsb if idx == 5 else 0); bad += got != want; print('seg_set(%d, %#x) fs/sel/gsbase %s %s' % (idx, sel, 'ok' if got == want else 'FAIL', ' '.join('%x' % x for x in got)))
    uc, rd = run(segs, sym, 'drv_ctx', (5, 0x23), PROC); ctx = rd(rd(sym['g_ctx'])[0] + 140, 16); got = {'gs': ctx[0], 'fs': ctx[1], 'es': ctx[2], 'ds': ctx[3], 'cs': ctx[12], 'ss': ctx[15]}
    want = dict(gs=0x23, fs=0x3b, es=0x23, ds=0x23, cs=0x1b, ss=0x23); bad += got != want; print('CONTEXT segments after mov gs,0x23 %s %s' % ('ok' if got == want else 'FAIL', {k: hex(v) for k, v in got.items()}))
    sys.exit(1 if bad else 0)


if __name__ == '__main__': main()
