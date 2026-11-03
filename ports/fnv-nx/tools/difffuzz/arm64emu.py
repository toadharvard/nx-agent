"""arm64emu.py - run one native AArch64 ILP32 function under Unicorn in the same shared world.

The unit image (a64image.Image) is loaded at A64_BASE; world pages (data window, argument buffers, heap, .text read
as data) are mapped on first touch with the same bytes as on the x86 side. The entry is the native definition whose
address symbol `__fnv_<addr>` names the original address. Arguments per AAPCS64 (ILP32) from conv.Conv: this and
integers/pointers in x0..x7 (zero-extended 32-bit pointers), float/double in v0..v7, aggregates per their class
(G1/G2 in x registers, HFA in v registers, larger ones by reference), overflow on the stack, hidden result in x8.

Outgoing calls are intercepted by original address: `__fnv_<addr>` still undefined after the unit link is
CODE_STUB + addr; ctor/dtor variants, runtime and C++ names are named stubs. Both regions hold `brk #0`; a block
starting there is a call: x0..x7, v0..v7 and x8 are recorded, x0/v0 get the canned result for call k (x8 memory too
for an indirect result), caller-saved registers get garbage, and execution resumes at LR. Bridges (tools/link
`__fnvnx_br_*`) are real code in the image and run before the stub, so the stub sees the definition's convention.
"""
import struct

from unicorn import (Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_BLOCK, UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_WRITE,
                     UC_HOOK_INTR, UC_PROT_ALL, UC_PROT_READ, UC_PROT_WRITE, UcError)
from unicorn import arm64_const as A
import world
import a64image

PAGE = world.PAGE
XR = [A.UC_ARM64_REG_X0 + i for i in range(29)] + [A.UC_ARM64_REG_X29, A.UC_ARM64_REG_X30]
VR = [A.UC_ARM64_REG_V0 + i for i in range(32)]
STUB_LO, STUB_HI = a64image.CODE_STUB, a64image.NAMED_STUB + 0x00800000
BRK = struct.pack('<I', 0xd4200000)


class Call:
    __slots__ = ('target', 'args', 'fargs', 'x8')

    def __init__(self, target, args, fargs, x8):
        self.target, self.args, self.fargs, self.x8 = target, args, fargs, x8


class Result:
    def __init__(self):
        self.ret = None
        self.ret_ext = None
        self.calls = []
        self.writes = {}
        self.error = None
        self.blocks = set()
        self.saved_ok = True


class Arm64Engine:
    def __init__(self, world_obj, image, stub_orig):
        self.world = world_obj
        self.image = image
        self.stub_orig = stub_orig
        self.mapped = set()
        self.dynamic = set()
        uc = self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.reg_write(A.UC_ARM64_REG_CPACR_EL1, 3 << 20)    # FP/SIMD on
        uc.mem_map(world.STACK[0], world.STACK[1] - world.STACK[0], UC_PROT_READ | UC_PROT_WRITE)
        self.mapped.update(range(world.STACK[0], world.STACK[1], PAGE))
        for base, data in image.segments:
            for p in range(world.page_of(base), base + len(data), PAGE):
                if p not in self.mapped:
                    uc.mem_map(p, PAGE, UC_PROT_ALL)
                    self.mapped.add(p)
            uc.mem_write(base, data)
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._on_unmapped)
        uc.hook_add(UC_HOOK_INTR, self._on_intr)
        self._fault = None

    def _map(self, page, prot=UC_PROT_ALL):
        if page in self.mapped:
            return
        self.uc.mem_map(page, PAGE, prot)
        self.uc.mem_write(page, self.world.page(page))
        self.mapped.add(page)
        self.dynamic.add(page)

    def reset(self, world_obj):
        """Forget the previous case's world pages and restore the image's writable data."""
        for p in self.dynamic:
            self.uc.mem_unmap(p, PAGE)
            self.mapped.discard(p)
        self.dynamic = set()
        self.world = world_obj
        for base, data in self.image.segments:
            if base >= self.image.data_start:
                self.uc.mem_write(base, data)

    def _ensure(self, addr, size=1):
        for p in range(world.page_of(addr), addr + size, PAGE):
            self._map(p)

    def _on_unmapped(self, uc, access, address, size, value, data):
        if STUB_LO <= address < STUB_HI and access == 21:     # UC_MEM_FETCH_UNMAPPED: a call into a stub
            p = world.page_of(address)
            uc.mem_map(p, PAGE, UC_PROT_ALL)
            uc.mem_write(p, BRK * (PAGE // 4))
            self.mapped.add(p)
            return True
        if not world.mappable(address, 'a64') or not world.mappable(address + size - 1, 'a64'):
            kind = {20: 'w', 21: 'x'}.get(access, 'r')
            self._fault = 'fault:%s:%08x' % (kind, address & 0xffffffff) if address < world.MAX else \
                'fault:%s:ilp32-high:%x' % (kind, address)
            return False
        self._ensure(address, size)
        return True

    def _on_intr(self, uc, intno, data):
        self._fault = 'exc:%d@%08x' % (intno, uc.reg_read(A.UC_ARM64_REG_PC))
        uc.emu_stop()

    def entry(self, orig_addr):
        return next((n for n, o in self.image.entries.items() if o == orig_addr), None)

    def run(self, conv, case, placement, orig_addr, max_steps=200000):
        uc = self.uc
        r = Result()
        native = self.entry(orig_addr)
        if native is None:
            r.error = 'no-native-entry'
            return r
        uc.mem_write(world.SP0 - 0x8000, bytes(0x7000) + world.stack_fill(case.seed ^ 0xa5, 0x1000))
        uc.mem_write(world.SP0, bytes(world.STACK[1] - world.SP0))
        for i in range(31):
            uc.reg_write(XR[i], world.garbage32(case.seed, 200 + i) | (world.garbage32(case.seed, 300 + i) << 32))
        for i in range(32):
            uc.reg_write(VR[i], world.garbage32(case.seed, 400 + i) | (world.garbage32(case.seed, 500 + i) << 32))
        self._place_args(conv, case, placement, world.SP0 & ~0xf)
        uc.reg_write(A.UC_ARM64_REG_LR, world.SENTINEL)
        self._ensure(world.SENTINEL, 4)
        saved = [uc.reg_read(XR[i]) for i in range(19, 30)] + [uc.reg_read(A.UC_ARM64_REG_SP)]
        hs = uc.hook_add(UC_HOOK_BLOCK, self._on_stub, user_data=(case, r), begin=STUB_LO, end=STUB_HI - 1)
        hc = uc.hook_add(UC_HOOK_BLOCK, self._on_cov, user_data=r, begin=a64image.A64_BASE,
                         end=max(self.image.end, a64image.A64_BASE + 4) - 1)
        hws = [uc.hook_add(UC_HOOK_MEM_WRITE, self._on_write, user_data=r, begin=lo, end=hi - 1)
               for lo, hi in world.COMPARED]
        self._fault = None
        try:
            uc.emu_start(native, world.SENTINEL, count=max_steps)
        except UcError as e:
            r.error = self._fault or 'fault:uc:%s' % e
        finally:
            for h in [hs, hc] + hws:
                uc.hook_del(h)
        pc = uc.reg_read(A.UC_ARM64_REG_PC)
        if r.error is None and self._fault:
            r.error = self._fault
        if r.error is None and pc != world.SENTINEL:
            r.error = 'timeout'
        if r.error is None:
            r.ret = self._read_ret(conv, placement)
            now = [uc.reg_read(XR[i]) for i in range(19, 30)] + [uc.reg_read(A.UC_ARM64_REG_SP)]
            r.saved_ok = now == saved
        return r

    def _place_args(self, conv, case, placement, sp):
        import conv as convmod
        uc = self.uc
        xregs, vregs, stack_chunks = {}, {}, []
        for v in conv.vals:
            ptr = v.label in placement and (v.is_ptr or v.label == 'this')
            if ptr:
                data = struct.pack('<I', placement[v.label])
            else:
                data = case.args.get(v.label, b'').ljust(v.size, b'\0')[:v.size]
            for voff, cont, coff, ln in conv.a64[v.label]:
                piece = data[voff:voff + ln].ljust(ln, b'\0')
                if cont[0] == 'x':
                    if v.kind == 'gpr' and not ptr:
                        piece = convmod.extend(v, piece, case.pad)
                    xregs[cont[1]] = piece
                elif cont[0] == 'v':
                    vregs[cont[1]] = piece
                elif cont[0] == 'stk':
                    if v.kind == 'gpr' and not ptr:
                        piece = convmod.extend(v, piece, case.pad)
                    stack_chunks.append((cont[1] + coff, piece))
                elif cont[0] == 'mem':
                    # by reference: the caller's copy lives in the argument buffer at placement[label]
                    buf = placement[v.label]
                    self._ensure(buf, v.size)
                    uc.mem_write(buf, data)
                    reg = cont[1]
                    if reg[0] == 'x':
                        xregs[reg[1]] = struct.pack('<I', buf)
                    else:
                        stack_chunks.append((reg[1], struct.pack('<I', buf)))
        for i, b in xregs.items():
            uc.reg_write(XR[i], int.from_bytes(b[:8].ljust(8, b'\0'), 'little'))
        for i, b in vregs.items():
            uc.reg_write(VR[i], int.from_bytes(b[:16].ljust(16, b'\0'), 'little'))
        if conv.hidden or any(c == ('mem', 'x8') for _, c, _, _ in conv.a64_ret):
            import x86emu
            uc.reg_write(A.UC_ARM64_REG_X8, placement.get('sret', x86emu.RESULT_BUF))
        if stack_chunks:
            top = max(o + len(d) for o, d in stack_chunks)
            frame = bytearray((top + 15) // 16 * 16)
            for o, d in stack_chunks:
                frame[o:o + len(d)] = d
            sp -= len(frame)
            uc.mem_write(sp, bytes(frame))
        uc.reg_write(A.UC_ARM64_REG_SP, sp)

    def _on_write(self, uc, access, address, size, value, r):
        for i in range(size):
            r.writes[address + i] = (value >> (8 * i)) & 0xff

    def _on_cov(self, uc, address, size, r):
        r.blocks.add(address)

    def _on_stub(self, uc, address, size, user):
        case, r = user
        if address < a64image.NAMED_STUB:
            target = address - a64image.CODE_STUB
        else:
            target = self.stub_orig.get(address, -address)
        k = len(r.calls)
        r.calls.append(Call(target, [uc.reg_read(XR[i]) for i in range(8)], [uc.reg_read(VR[i]) for i in range(8)],
                            uc.reg_read(A.UC_ARM64_REG_X8)))
        rb = case.ret_bytes(k)
        for i in list(range(1, 18)):
            uc.reg_write(XR[i], world.garbage32(case.seed, 600 + i + 32 * k))
        for i in list(range(1, 8)) + list(range(16, 32)):
            uc.reg_write(VR[i], world.garbage32(case.seed, 700 + i + 32 * k))
        uc.reg_write(XR[0], int.from_bytes(rb[:8], 'little'))
        uc.reg_write(VR[0], int.from_bytes(rb[:16], 'little'))
        uc.reg_write(A.UC_ARM64_REG_PC, uc.reg_read(A.UC_ARM64_REG_LR))

    def _read_ret(self, conv, placement):
        uc = self.uc
        if conv.ret is None:
            return None
        out = bytearray(conv.ret.size)
        for voff, cont, coff, ln in conv.a64_ret:
            if cont[0] == 'x':
                b = (uc.reg_read(XR[cont[1]]) & ((1 << 64) - 1)).to_bytes(8, 'little')
            elif cont[0] == 'v':
                b = (uc.reg_read(VR[cont[1]]) & ((1 << 128) - 1)).to_bytes(16, 'little')
            elif cont == ('mem', 'x8'):
                import x86emu
                b = bytes(uc.mem_read(placement.get('sret', x86emu.RESULT_BUF) + voff, ln))
                coff = 0
            else:
                continue
            out[voff:voff + ln] = b[coff:coff + ln]
        return bytes(out)
