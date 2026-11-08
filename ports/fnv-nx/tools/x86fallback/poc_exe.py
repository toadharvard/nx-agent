#!/usr/bin/env python3
"""X1-fex step 1: a real exe function through the native|x86 dispatch table, compared with the native build.

One address space, two CPUs on the same host buffers (Unicorn x86-32 = the FEX stand-in, Unicorn ARM64 = our ILP32
code). Three modes run on the same fuzzed memory:
  native  : the caller G and the callee L from our AArch64 object (G's branch relocations to L resolved here)
  mixed   : G's x86 bytes from the player's FalloutNV.exe, L dispatched to native AArch64 (x86 -> native exit)
  x86     : G and L both from the exe (the reference the fallback stands for)
The outcome is the memory image of the arena plus the fault state; all three must agree.

Nothing exe- or decomp-derived is stored: the exe and the object are read at run time (local paths).
Usage: poc_exe.py --exe FalloutNV.exe --obj src_bscore_abstractheap.cpp.o \
         --g 0x00aa9d30:_ZN12AbstractHeap12FUN_00aa9d30EP13HeapFreeBlock --g-size 288 \
         --l 0x00aa9ef0:_ZN12AbstractHeap18IncrementSubBucketEi --l-size 93 --l-pop 4 [--cases 3000]
G and L must be __thiscall with one 32-bit argument (the case shown); L returns void.
"""
import argparse, ctypes, hashlib, random, struct, sys
import pefile
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from unicorn import (Uc, UcError, UC_ARCH_X86, UC_MODE_32, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_BLOCK,
                     UC_PROT_ALL, UC_PROT_READ, UC_PROT_EXEC)
from unicorn.x86_const import UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_ESP, UC_X86_REG_EAX, UC_X86_REG_EIP
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X30, UC_ARM64_REG_SP

EXE_SHA = "89e7020487e80f20ddc92ea641c4433b2c6839a82f45a076e414d167d11dd0fe"
ARENA, ARENA_SZ = 0x03000000, 0x00100000          # objects (shared)
STACK, STACK_SZ = 0x02000000, 0x00100000          # x86 stack and native stack (shared window)
NATIVE, NATIVE_SZ = 0x0a000000, 0x00100000        # our AArch64 code (native CPU only)
RET_X86, RET_A64 = 0x02000010, NATIVE + NATIVE_SZ - 0x10
P32_JUMP26, P32_CALL26 = 20, 21                   # AArch64 ILP32 relocation numbers


def load_native(obj, names):
    """Place the object's code section(s) holding `names` at NATIVE, resolve branch relocations among them."""
    f = ELFFile(open(obj, "rb"))
    syms = list(f.get_section_by_name(".symtab").iter_symbols())
    secs = list(f.iter_sections())
    want = {s.name: s for s in syms if s.name in names and s["st_info"]["type"] == "STT_FUNC"}
    missing = set(names) - set(want)
    if missing: sys.exit(f"not in {obj}: {missing}")
    base, place, img = NATIVE, {}, bytearray()
    for idx in sorted({s["st_shndx"] for s in want.values()}):
        place[idx] = base + len(img); img += secs[idx].data(); img += b"\0" * (-len(img) % 16)
    addr = {n: place[s["st_shndx"]] + s["st_value"] for n, s in want.items()}
    for rs in secs:
        if not isinstance(rs, RelocationSection) or rs["sh_info"] not in place: continue
        for r in rs.iter_relocations():
            s, at = syms[r["r_info_sym"]], place[rs["sh_info"]] + r["r_offset"]
            if not any(addr[n] <= at < addr[n] + want[n]["st_size"] for n in want): continue  # other functions
            if r["r_info_type"] not in (P32_JUMP26, P32_CALL26) or s.name not in addr:
                sys.exit(f"unsupported relocation {r['r_info_type']} -> {s.name!r} in the chosen functions")
            off = at - NATIVE; w = struct.unpack_from("<I", img, off)[0]
            w = (w & 0xFC000000) | (((addr[s.name] - at) >> 2) & 0x03FFFFFF)
            struct.pack_into("<I", img, off, w)
    return bytes(img), addr


class World:
    def __init__(self, exe_text, text_lo, native_img):
        self.bufs = {}
        self.x86, self.a64 = Uc(UC_ARCH_X86, UC_MODE_32), Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        for base, size in ((ARENA, ARENA_SZ), (STACK, STACK_SZ)):         # shared: same host bytes
            b = self.bufs[base] = ctypes.create_string_buffer(size)
            self.x86.mem_map_ptr(base, size, UC_PROT_ALL, ctypes.addressof(b))
            self.a64.mem_map_ptr(base, size, UC_PROT_ALL, ctypes.addressof(b))
        lo = text_lo & ~0xfff; hi = (text_lo + len(exe_text) + 0xfff) & ~0xfff
        self.x86.mem_map(lo, hi - lo, UC_PROT_READ | UC_PROT_EXEC)             # the exe's x86 bytes, x86 only
        self.x86.mem_write(text_lo, exe_text)
        self.a64.mem_map(NATIVE, NATIVE_SZ, UC_PROT_ALL)                       # native code, native only
        self.a64.mem_write(NATIVE, native_img)
        self.a64.mem_write(RET_A64, struct.pack("<I", 0xD65F03C0))
        self.x86.mem_write(RET_X86, b"\xf4")
        self.dispatch, self.exits = {}, 0
        self.x86.hook_add(UC_HOOK_BLOCK, self._on_block)

    def _on_block(self, uc, address, size, _):           # FEX's block lookup: is this guest PC native?
        row = self.dispatch.get(address)
        if not row: return
        native, pop = row
        esp = uc.reg_read(UC_X86_REG_ESP)
        ret, arg = struct.unpack("<II", bytes(uc.mem_read(esp, 8)))
        self.call_native(native, uc.reg_read(UC_X86_REG_ECX), arg)            # __thiscall -> AAPCS64 x0, w1
        uc.reg_write(UC_X86_REG_ESP, esp + 4 + pop)                           # callee pops (ret N)
        for r in (UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX): uc.reg_write(r, 0xBADC0DE5)  # caller-saved
        uc.reg_write(UC_X86_REG_EIP, ret); self.exits += 1

    def call_native(self, addr, this, arg):
        a = self.a64
        a.reg_write(UC_ARM64_REG_X0, this); a.reg_write(UC_ARM64_REG_X1, arg)
        a.reg_write(UC_ARM64_REG_X30, RET_A64); a.reg_write(UC_ARM64_REG_SP, STACK + STACK_SZ - 0x8000)
        a.emu_start(addr, RET_A64, count=200000)

    def call_x86(self, addr, this, arg):
        esp = STACK + STACK_SZ - 0x40000
        self.x86.mem_write(esp, struct.pack("<II", RET_X86, arg))
        self.x86.reg_write(UC_X86_REG_ESP, esp); self.x86.reg_write(UC_X86_REG_ECX, this)
        self.x86.emu_start(addr, RET_X86, count=200000)
        if self.x86.reg_read(UC_X86_REG_ESP) != esp + 8: raise RuntimeError("stack not balanced")

    def arena(self): return bytes(self.bufs[ARENA].raw)


def fill(rnd, w):
    """Arena words: small integers, in-arena pointers (4-aligned) or 0, so linked structures are walkable."""
    words = []
    for _ in range(ARENA_SZ // 4 // 16):                 # fuzz the first 64 KiB, rest stays 0
        c = rnd.random()
        words.append(rnd.randrange(0, 64) if c < .4 else ARENA + 4 * rnd.randrange(0, 0x4000) if c < .85 else 0)
    ctypes.memmove(w.bufs[ARENA], struct.pack(f"<{len(words)}I", *words), 4 * len(words))
    ctypes.memset(ctypes.addressof(w.bufs[ARENA]) + 4 * len(words), 0, ARENA_SZ - 4 * len(words))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True); ap.add_argument("--obj", required=True)
    ap.add_argument("--g", required=True); ap.add_argument("--g-size", type=lambda s: int(s, 0), required=True)
    ap.add_argument("--l", required=True); ap.add_argument("--l-size", type=lambda s: int(s, 0), required=True)
    ap.add_argument("--l-pop", type=int, default=4); ap.add_argument("--cases", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=1)
    o = ap.parse_args()
    data = open(o.exe, "rb").read()
    if hashlib.sha256(data).hexdigest() != EXE_SHA: sys.exit("not the pinned FalloutNV.exe")
    pe = pefile.PE(data=data, fast_load=True)
    (g_va, g_sym), (l_va, l_sym) = [(int(a, 16), s) for a, s in (x.split(":") for x in (o.g, o.l))]
    lo, hi = min(g_va, l_va), max(g_va + o.g_size, l_va + o.l_size)
    text = pe.get_data(lo - pe.OPTIONAL_HEADER.ImageBase, hi - lo)
    img, naddr = load_native(o.obj, [g_sym, l_sym])
    rnd, exits = random.Random(o.seed), 0
    tally = {"equal": 0, "both-fault": 0, "mixed!=x86": 0, "native!=x86 (div0 class)": 0, "native!=x86 (other)": 0}
    for i in range(o.cases):
        this = ARENA + 4 * rnd.randrange(0, 0x3f00)
        arg = ARENA + 4 * rnd.randrange(0, 0x3f00) if rnd.random() < .9 else rnd.randrange(0, 64)
        out = {}
        for mode in ("native", "mixed", "x86"):
            w = World(text, lo, img)                       # a fresh world per run: no engine state carried over
            fill(random.Random(o.seed * 1000003 + i), w)
            w.dispatch = {l_va: (naddr[l_sym], o.l_pop)} if mode == "mixed" else {}
            try:
                (w.call_native(naddr[g_sym], this, arg) if mode == "native" else w.call_x86(g_va, this, arg))
                out[mode] = ("ok", hashlib.sha1(w.arena()).hexdigest())
            except (UcError, RuntimeError) as e:            # fault address reporting differs per engine
                out[mode] = ("x86-#DE" if mode != "native" and "UC_ERR_EXCEPTION" in str(e) else "fault", None)
            exits += w.exits
        fault = {m: v[0] != "ok" for m, v in out.items()}
        if out["mixed"] != out["x86"]:
            tally["mixed!=x86"] += 1; print("DIFF mixed/x86 case", i, hex(this), hex(arg), out)
        elif all(fault.values()): tally["both-fault"] += 1
        elif out["native"] == out["x86"]: tally["equal"] += 1
        else:                                               # the fallback reproduces the exe; the native build differs
            k = "native!=x86 (div0 class)" if out["x86"][0] == "x86-#DE" and not fault["native"] else "native!=x86 (other)"
            tally[k] += 1
            if k.endswith("(other)") and tally[k] <= 5: print("native/x86 case", i, hex(this), hex(arg), out)
    print(f"G {g_va:#010x} x86 from exe, L {l_va:#010x} native: cases {o.cases}  {tally}  x86->native exits {exits}")
    return 1 if tally["mixed!=x86"] or tally["native!=x86 (other)"] else 0


if __name__ == "__main__":
    sys.exit(main())
