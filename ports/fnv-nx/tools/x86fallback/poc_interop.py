#!/usr/bin/env python3
"""X1-fex proof of concept: native AArch64 and translated x86-32 code in ONE address space.

Stand-in for FEXCore: Unicorn x86-32 plays the JIT, Unicorn ARM64 plays our native ILP32 code. Both engines map
the SAME host buffers (mem_map_ptr), so objects are shared byte for byte, as they would be with FEX's identity
guest==host mapping. Shown:
  * native -> x86 entry thunk, MSVC __thiscall (ecx = this, args on the stack, callee pops: ret 4);
  * x86 -> native exit through a per-function dispatch table (address 0x00403000 is "native"), __stdcall;
  * the x86 path gives the same result and the same object bytes as the all-native path.
The code is synthetic (hand-encoded, checked by Capstone); no exe bytes. Usage: poc_interop.py [cases]
"""
import ctypes, random, struct, sys
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_PROT_ALL
from unicorn.x86_const import UC_X86_REG_ECX, UC_X86_REG_ESP, UC_X86_REG_EAX, UC_X86_REG_EIP
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X30, UC_ARM64_REG_SP
import capstone

TEXT, DATA, STACK, NATIVE = 0x00400000, 0x01000000, 0x02000000, 0x0a000000   # exe .text, data window, x86 stack, native
SZ = 0x100000
RET_X86, RET_A64 = 0x004ffff0, 0x0a0ffff0                                       # sentinels

# x86 (MSVC shape). G: int __thiscall Obj::G(int k) { int r = this->a + Scale(k); this->c = r; return r; }
X86_G = bytes.fromhex("ff742404" "e8" + "00000000" + "0301" "894108" "c20400")   # push [esp+4]; call Scale; add; mov; ret 4
G_X86, SCALE = 0x00402000, 0x00403000
# AArch64 ILP32 (GCC shape). Scale: int Scale(int v) { return v * 2; }; G as above, all native.
A64 = {
    "scale": [0x531F7800, 0xD65F03C0],                                   # lsl w0,w0,#1 ; ret
    "g":     [0xB9400002, 0x0B010443, 0xB9000803, 0x2A0303E0, 0xD65F03C0],  # ldr w2,[x0]; add w3,w2,w1,lsl#1; str w3,[x0,#8]; mov w0,w3; ret
}
SCALE_A64, G_A64 = NATIVE + 0x0, NATIVE + 0x100
DISPATCH = {G_X86: ("x86", None), SCALE: ("native", (SCALE_A64, "stdcall", 1))}  # the per-function table

def code(words): return b"".join(struct.pack("<I", w) for w in words)

def build():
    bufs = {a: ctypes.create_string_buffer(SZ) for a in (TEXT, DATA, STACK, NATIVE)}
    x86, a64 = Uc(UC_ARCH_X86, UC_MODE_32), Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    for a, b in bufs.items():
        if a != NATIVE: x86.mem_map_ptr(a, SZ, UC_PROT_ALL, ctypes.addressof(b))   # x86 never sees native code
        a64.mem_map_ptr(a, SZ, UC_PROT_ALL, ctypes.addressof(b))
    rel = SCALE - (G_X86 + 4 + 5)
    g = X86_G[:5] + struct.pack("<i", rel) + X86_G[9:]
    x86.mem_write(G_X86, g)
    x86.mem_write(SCALE, b"\xcc")                       # never executed: the dispatcher exits first
    a64.mem_write(SCALE_A64, code(A64["scale"])); a64.mem_write(G_A64, code(A64["g"]))
    a64.mem_write(RET_A64, code([0xD65F03C0]))
    x86._fnv_bufs = bufs                                 # keep the shared buffers alive
    return x86, a64, g

def call_native(a64, addr, args):
    """AAPCS64 call of a native function (w0.. args), as our thunk would."""
    for r, v in zip((UC_ARM64_REG_X0, UC_ARM64_REG_X1), args): a64.reg_write(r, v & 0xffffffff)
    a64.reg_write(UC_ARM64_REG_X30, RET_A64); a64.reg_write(UC_ARM64_REG_SP, STACK + SZ - 0x1000)
    a64.emu_start(addr, RET_A64)
    return a64.reg_read(UC_ARM64_REG_X0) & 0xffffffff

def call_x86_thiscall(x86, a64, entry, this, args, exits):
    """native -> x86 entry thunk: ecx = this, args pushed right to left, return address = sentinel."""
    esp = STACK + SZ - 0x100
    for v in reversed(args): esp -= 4; x86.mem_write(esp, struct.pack("<I", v & 0xffffffff))
    esp -= 4; x86.mem_write(esp, struct.pack("<I", RET_X86))
    x86.reg_write(UC_X86_REG_ESP, esp); x86.reg_write(UC_X86_REG_ECX, this)
    def hook(uc, addr, size, _):                        # the JIT's block lookup: is this guest PC native?
        kind, tgt = DISPATCH.get(addr, ("x86", None))
        if kind != "native": return
        naddr, conv, nargs = tgt
        sp = uc.reg_read(UC_X86_REG_ESP)
        ret, *a = struct.unpack("<%dI" % (1 + nargs), uc.mem_read(sp, 4 * (1 + nargs)))
        uc.reg_write(UC_X86_REG_EAX, call_native(a64, naddr, a))   # x86 -> native exit thunk
        uc.reg_write(UC_X86_REG_ESP, sp + 4 + (4 * nargs if conv == "stdcall" else 0))
        uc.reg_write(UC_X86_REG_EIP, ret); exits.append(addr)
    h = x86.hook_add(UC_HOOK_CODE, hook)
    x86.emu_start(entry, RET_X86); x86.hook_del(h)
    assert x86.reg_read(UC_X86_REG_ESP) == STACK + SZ - 0x100, "callee did not pop per __thiscall"
    return x86.reg_read(UC_X86_REG_EAX)

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    x86, a64, g = build()
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    print("x86 G:", "; ".join(f"{i.mnemonic} {i.op_str}" for i in md.disasm(g, G_X86)))
    ma = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    for k, w in A64.items(): print(f"a64 {k}:", "; ".join(f"{i.mnemonic} {i.op_str}" for i in ma.disasm(code(w), 0)))
    rnd, bad, exits = random.Random(1), 0, []
    for i in range(n):
        this = DATA + 0x100 + 16 * (i % 64)
        a, k = rnd.getrandbits(32), rnd.getrandbits(32)
        obj = struct.pack("<III", a, 0, 0xdeadbeef)
        x86.mem_write(this, obj); r1 = call_x86_thiscall(x86, a64, G_X86, this, [k], exits); m1 = bytes(x86.mem_read(this, 12))
        a64.mem_write(this, obj); r2 = call_native(a64, G_A64, [this, k]); m2 = bytes(a64.mem_read(this, 12))
        if (r1, m1) != (r2, m2): bad += 1; print("DIFF", hex(a), hex(k), hex(r1), hex(r2), m1.hex(), m2.hex())
    print(f"cases {n}  x86-path vs native: {n - bad} equal, {bad} differ; x86->native exits {len(exits)}")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
