#!/usr/bin/env python3
"""CS-store rule (core.gs_guard): a store whose effective segment is CS raises #GP(0) = STATUS_ACCESS_VIOLATION {0, 0xFFFFFFFF} at the instruction (FNVL_RAISE_AV, the f_sys.gp_cond row; Wine 9.0
dlls/ntdll/signal_i386.c segv_handler, case TRAP_x86_PROTFLT); a load through CS is legal and emits no guard. Every write-capable form is covered by the operand's capstone access, not by a list.
    cs_store_test.py     (exit 0 = all pass)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import capstone
from fnvlift import core
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
A0, FAILS = 0x400000, []


def guard(hx):
    i = next(md.disasm(bytes.fromhex(hx), A0))
    cx = core.Ctx(A0, i.size, {A0: i}, {}, [])
    cx.start, cx.a, cx.nxt, cx.m, cx.pfx, cx.ops = A0, A0, A0 + i.size, i.mnemonic.split()[-1], '', i.operands
    cx.raises = set()
    core.gs_guard(cx, i)
    return '\n'.join(cx.L)


def check(n, ok):
    print('%-66s %s' % (n, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(n)


AV = 'FNVL_RAISE_AV(0,0xffffffffu,0x%xu)' % A0
for hx, what in (('2e8901', 'mov cs:[ecx], eax'), ('2e0100', 'add cs:[eax], eax'), ('2eff00', 'inc cs:[eax]'), ('2e8701', 'xchg cs:[ecx], eax'), ('2ec60100', 'mov byte cs:[ecx], 0'),
                 ('2e0fab08', 'bts cs:[eax], ecx'), ('2ef7d0', 'not eax (no memory operand)')[:2] if False else ('2ef710', 'not cs:[eax]')):
    check('store  %-24s -> AV {0, 0xffffffff} at the instruction' % what, AV in guard(hx))
for hx, what in (('2e8b01', 'mov eax, cs:[ecx]'), ('2e3b01', 'cmp eax, cs:[ecx]'), ('2eff30', 'push cs:[eax]'), ('2e0301', 'add eax, cs:[ecx]'), ('2e0fb601', 'movzx eax, byte cs:[ecx]'), ('8901', 'mov [ecx], eax (no override)'),
                 ('368901', 'mov ss:[ecx], eax'), ('3e8901', 'mov ds:[ecx], eax'), ('268901', 'mov es:[ecx], eax'), ('648901', 'mov fs:[ecx], eax')):
    check('no guard %-24s' % what, 'FNVL_RAISE_AV' not in guard(hx))
check('gs store keeps its own guard (null / code selector)', 'SELK(c->sel[SEG_GS])' in guard('658901') and 'FNVL_RAISE_AV' in guard('658901'))
check('gs load keeps the null-selector guard only', 'SELK(SEL_CS)' not in guard('658b01') and 'SELK(c->sel[SEG_GS])==0' in guard('658b01'))
sys.exit(1 if FAILS else 0)
