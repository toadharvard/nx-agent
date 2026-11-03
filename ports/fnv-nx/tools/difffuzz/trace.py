"""trace.py - instruction traces of both machines for one repro (debugging aid, `difffuzz.py repro --trace`)."""
import capstone
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_ESP
from unicorn import arm64_const as A

import world
import x86emu
import arm64emu

CX = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
CA = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)


def _x86_hook(uc, address, size, n):
    if n[0] > 400:
        return
    n[0] += 1
    code = bytes(uc.mem_read(address, size))
    ins = next(CX.disasm(code, address), None)
    print('  x86 %08x %-40s eax=%08x ecx=%08x edx=%08x esp=%08x' % (
        address, (ins.mnemonic + ' ' + ins.op_str) if ins else code.hex(), uc.reg_read(UC_X86_REG_EAX),
        uc.reg_read(UC_X86_REG_ECX), uc.reg_read(UC_X86_REG_EDX), uc.reg_read(UC_X86_REG_ESP)))


def _a64_hook(uc, address, size, n):
    if n[0] > 400:
        return
    n[0] += 1
    code = bytes(uc.mem_read(address, 4))
    ins = next(CA.disasm(code, address), None)
    print('  a64 %08x %-40s x0=%x x1=%x x2=%x x8=%x' % (
        address, (ins.mnemonic + ' ' + ins.op_str) if ins else code.hex(), uc.reg_read(A.UC_ARM64_REG_X0),
        uc.reg_read(A.UC_ARM64_REG_X1), uc.reg_read(A.UC_ARM64_REG_X2), uc.reg_read(A.UC_ARM64_REG_X8)))


def attach(pair, target):
    orig_run = pair.run

    def run(case, max_steps=200000):
        w = world.World(case, target.x86_code, pair.pinned)
        pair.xe = x86emu.X86Engine(w, target.x86_code, target.callee_info)
        pair.ae = arm64emu.Arm64Engine(w, target.image, target.stub_orig, getattr(target, 'vtables', None))
        pair.xe.uc.hook_add(UC_HOOK_CODE, _x86_hook, user_data=[0])
        pair.ae.uc.hook_add(UC_HOOK_CODE, _a64_hook, user_data=[0])
        pair.run = orig_run
        return orig_run(case, max_steps)
    pair.run = run
