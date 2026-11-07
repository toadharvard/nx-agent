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
                     UC_HOOK_MEM_READ,
                     UC_HOOK_INTR, UC_PROT_ALL, UC_PROT_READ, UC_PROT_WRITE, UcError)
from unicorn import arm64_const as A
import world
import a64image
import libcalls

PAGE = world.PAGE
TLS_BASE, TLS_SIZE = 0x0b900000, 0x00010000   # inside the AArch64 private range (faults as data elsewhere)
EXC_BASE, EXC_SIZE = 0x0b910000, 0x00010000   # __cxa_allocate_exception's object (libcalls.A64_EXC_OBJ), private
MAX_CALLS = 20000                             # intercepted calls per run (each costs no step)
XR = [A.UC_ARM64_REG_X0 + i for i in range(29)] + [A.UC_ARM64_REG_X29, A.UC_ARM64_REG_X30]
VR = [A.UC_ARM64_REG_V0 + i for i in range(32)]
STUB_LO, STUB_HI = a64image.CODE_STUB, a64image.NAMED_STUB + 0x00800000
BRK = struct.pack('<I', 0xd4200000)


class Call:
    __slots__ = ('target', 'args', 'fargs', 'x8', 'stack', 'addr')

    def __init__(self, target, args, fargs, x8, stack=b''):
        self.target, self.args, self.fargs, self.x8, self.stack = target, args, fargs, x8, stack
        self.addr = None             # the raw address the native branched to (a stub or a wild address)


class Result:
    def __init__(self):
        self.ret = None
        self.ret_ext = None
        self.calls = []
        self.writes = {}
        self.error = None
        self.blocks = set()
        self.saved_ok = True
        self.vptr_stores = 0
        self.private_reads = 0
        self.private_addrs = set()
        self.noreturn = None            # the noreturn callee the run ended at (libcalls.noreturn_a64)


class Arm64Engine:
    def __init__(self, world_obj, image, stub_orig, vtables=None, vt_mem=None):
        self.vtables = vtables or {}
        self.callee_conv = None          # original address -> Conv of the callee definition (set by runner.Pair)
        self.iat_names = {}              # IAT slot -> import name (set by runner.Pair)
        self.caller_conv = None          # original address -> Conv of the calling unit's declaration (runner.Pair)
        self.imports = {}                # x86 import stub index -> (name, stdcall bytes) (set by runner.Pair)
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
        # thread-local storage: the native code reads [tpidr_el0 + offset] (the platform's thread_local state, GCC's
        # TLS model local-exec); a zeroed private block, not compared (the oracle has no such state)
        uc.mem_map(TLS_BASE, TLS_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        uc.mem_map(EXC_BASE, EXC_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        uc.mem_map(a64image.STACK_GUARD, PAGE, UC_PROT_READ)
        uc.mem_write(a64image.STACK_GUARD, struct.pack('<Q', 0x5ca1ab1e0ddba11))
        self.mapped.add(a64image.STACK_GUARD)
        # native vtable groups synthesized for planted vptrs (vtmodel.py: classes whose _ZTV the unit image does not
        # define), read only, in the private range
        for base, data in sorted((vt_mem or {}).items()):
            for p in range(world.page_of(base), base + len(data), PAGE):
                if p not in self.mapped:
                    uc.mem_map(p, PAGE, UC_PROT_READ)
                    self.mapped.add(p)
            uc.mem_write(base, data)
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._on_unmapped)
        uc.hook_add(UC_HOOK_INTR, self._on_intr)
        self._trap_pcs = None
        self._fault = None

    def _map(self, page, prot=UC_PROT_ALL):
        if page in self.mapped:
            return
        self.uc.mem_map(page, PAGE, prot)
        self.uc.mem_write(page, self.world.page_a64(page))
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
        if access == 21 and libcalls.IMPORT_BASE <= address < libcalls.IMPORT_BASE + libcalls.IMPORT_MAX * 16:
            # an import called through the value of its IAT slot (pinned to the x86 import stub): a wild call
            # there is that import (_on_stub)
            p = world.page_of(address)
            uc.mem_map(p, PAGE, UC_PROT_ALL)
            uc.mem_write(p, BRK * (PAGE // 4))
            self.mapped.add(p)
            return True
        if STUB_LO <= address < STUB_HI and access == 21 and not any(lo <= address < hi for lo, hi in self.holes()):
            # UC_MEM_FETCH_UNMAPPED: a call into a stub
            p = world.page_of(address)
            uc.mem_map(p, PAGE, UC_PROT_ALL)
            uc.mem_write(p, BRK * (PAGE // 4))
            self.mapped.add(p)
            return True
        if not self._mappable(address) or not self._mappable(address + size - 1):
            kind = {20: 'w', 21: 'x'}.get(access, 'r')
            self._fault = 'fault:%s:%08x' % (kind, address & 0xffffffff) if address < world.MAX else \
                'fault:%s:ilp32-high:%x' % (kind, address)
            return False
        self._ensure(address, size)
        return True

    def holes(self):
        """The parts of the AArch64 private range this unit image leaves unused (world.A64_HOLES plus, per image,
        code stubs outside .text, named stubs above the last one assigned, the space after the image): background
        memory as on the oracle, and a wild call when executed (difffuzz-19)."""
        if getattr(self, '_holes', None) is None:
            import vtmodel
            pg = lambda a: (a + PAGE - 1) & ~(PAGE - 1)          # noqa: E731
            top = max([a for a in self.image.stubs if a < vtmodel.VT_STUBS] or [a64image.NAMED_STUB])
            h = list(world.A64_HOLES) + [
                (a64image.CODE_STUB, a64image.CODE_STUB + world.TEXT[0]),
                (pg(a64image.CODE_STUB + world.TEXT[1]), a64image.NAMED_STUB),
                (pg(top + 16), vtmodel.VT_STUBS),
                (pg(max(self.image.end, a64image.A64_BASE)), TLS_BASE)]
            self._holes = [(lo, hi) for lo, hi in h if lo < hi]
        return self._holes

    def _mappable(self, a):
        return world.mappable(a, 'a64') or any(lo <= a < hi for lo, hi in self.holes())

    # the link's trap stubs for vtable slots it cannot bind (fnvlink.py: __fnvnx_vtrap_<X> loads x1 = a text naming
    # the class and slot, then `b fnvnx_vslot_trap`; special members -> fnvnx_special_trap; weak defaults: brk)
    TRAPS = ('fnvnx_vslot_trap', 'fnvnx_special_trap')

    def _on_intr(self, uc, intno, data):
        pc = uc.reg_read(A.UC_ARM64_REG_PC)
        if self._trap_pcs is None:
            self._trap_pcs = {self.image.syms[n]: n for n in self.TRAPS if n in getattr(self.image, 'syms', {})}
        name = self._trap_pcs.get(pc)
        if name:
            # a call of a virtual function the link binds to nothing (difffuzz-14; S1-fuzzq's exc:7 "at shared
            # PCs": 0x0a069914 in 54 functions of one unit is that unit's fnvnx_vslot_trap)
            try:
                text = bytes(uc.mem_read(uc.reg_read(XR[1]) & 0xffffffff, 96)).split(b'\0')[0]
                text = text.decode('ascii', 'replace')
            except UcError:
                text = '?'
            self._fault = 'trap:%s:%s' % (name, text)
        else:
            self._fault = 'exc:%d@%08x' % (intno, pc)
        uc.emu_stop()

    def entry(self, orig_addr):
        # an override of a secondary base's virtual: MSVC enters it with `this` at that base, so the native entry is
        # the link's __fnvnx_msvc_<addr> (GCC's _ZThn<d>_ thunk), not __fnv_<addr> (S2c thisadj, difffuzz-19)
        t = self.image.syms.get('__fnvnx_msvc_%08x' % orig_addr)
        if t is not None:
            return t
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
        uc.mem_write(TLS_BASE, bytes(TLS_SIZE))
        uc.mem_write(EXC_BASE, bytes(EXC_SIZE))
        uc.reg_write(A.UC_ARM64_REG_TPIDR_EL0, TLS_BASE)
        uc.reg_write(A.UC_ARM64_REG_LR, world.SENTINEL)
        self._ensure(world.SENTINEL, 4)
        saved = [uc.reg_read(XR[i]) for i in range(19, 30)] + [uc.reg_read(A.UC_ARM64_REG_SP)]
        hs = uc.hook_add(UC_HOOK_BLOCK, self._on_stub, user_data=(case, r), begin=STUB_LO, end=STUB_HI - 1)
        hwild = [uc.hook_add(UC_HOOK_BLOCK, self._on_wild, user_data=(case, r), begin=lo, end=hi - 1)
                 for lo, hi in tuple(world.WILD_A64) + tuple(self.holes())]
        hc = uc.hook_add(UC_HOOK_BLOCK, self._on_cov, user_data=r, begin=a64image.A64_BASE,
                         end=max(self.image.end, a64image.A64_BASE + 4) - 1)
        hws = [uc.hook_add(UC_HOOK_MEM_WRITE, self._on_write, user_data=r, begin=lo, end=hi - 1)
               for lo, hi in world.COMPARED]
        # function-local statics paired with the oracle's (engine.static_pairs, difffuzz-17): stores into the native
        # copy count as stores at the oracle's address
        for na, oa, n in getattr(self, 'static_pairs', ()):
            hws.append(uc.hook_add(UC_HOOK_MEM_WRITE, self._on_static_write, user_data=(r, na, oa, n),
                                   begin=na, end=na + n - 1))
        # reads of the unit image's own data (.data/.bss after .rodata): state the native code keeps outside the
        # image window (a unit-local copy of something the oracle reads at an original address)
        for lo, hi in getattr(self.image, 'private', ()):
            hws.append(uc.hook_add(UC_HOOK_MEM_READ, self._on_private_read, user_data=r, begin=lo, end=hi - 1))
        self._fault = None
        try:
            uc.emu_start(native, world.SENTINEL, timeout=world.RUN_TIMEOUT_US, count=max_steps)
        except UcError as e:
            r.error = self._fault or 'fault:uc:%s' % e
        finally:
            for h in [hs, hc] + hws + hwild:
                uc.hook_del(h)
        pc = uc.reg_read(A.UC_ARM64_REG_PC)
        if r.error is None and self._fault:
            r.error = self._fault
        if r.error is None and pc != world.SENTINEL and not r.noreturn:
            r.error = 'timeout'
        if r.error is None and not r.noreturn:
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

    def _codeptr(self, v):
        """A native code address stored as data stands for its original address (S3 maps image code pointers to
        native ones): a code stub (unlinked callee) or a native entry of the image."""
        if a64image.CODE_STUB <= v < a64image.NAMED_STUB:
            return v - a64image.CODE_STUB
        return self.image.entries.get(v, v)

    def _norm_bytes(self, b):
        b = bytearray(b)
        i = 0
        while i + 4 <= len(b):
            w = int.from_bytes(b[i:i + 4], 'little')
            n = self._codeptr(w)
            if n == w and self.vtables:
                n = self.vtables.get(w, w)
            if n != w:
                b[i:i + 4] = n.to_bytes(4, 'little')
                i += 4
            else:
                i += 1
        return bytes(b)

    def _on_write(self, uc, access, address, size, value, r):
        if size >= 4:
            # code pointers anywhere in the stored bytes (tables of initializers are not always word aligned)
            value &= (1 << (8 * size)) - 1
            b = bytearray(value.to_bytes(size, 'little'))
            i, changed = 0, False
            while i + 4 <= size:
                w = int.from_bytes(b[i:i + 4], 'little')
                n = self._codeptr(w)
                if n != w:
                    b[i:i + 4] = n.to_bytes(4, 'little')
                    changed = True
                    i += 4
                else:
                    i += 1
            if changed:
                value = int.from_bytes(bytes(b), 'little')
        vt = self.vtables
        if vt and size in (4, 8):
            # a native vtable address point stored into an object stands for the image vftable (D7)
            lo, hi = value & 0xffffffff, (value >> 32) & 0xffffffff
            if lo in vt or (size == 8 and hi in vt):
                value = vt.get(lo, lo) | (vt.get(hi, hi) << 32 if size == 8 else 0)
                r.vptr_stores += 1
        for i in range(size):
            r.writes[address + i] = (value >> (8 * i)) & 0xff

    def _on_static_write(self, uc, access, address, size, value, data):
        r, na, oa, n = data
        b = (value & ((1 << (8 * size)) - 1)).to_bytes(size, 'little')
        for i, x in enumerate(b):
            if na <= address + i < na + n:
                r.writes[oa + address + i - na] = x

    def _on_private_read(self, uc, access, address, size, value, r):
        if any(na <= address < na + n for na, _, n in getattr(self, 'static_pairs', ())):
            return                      # a paired local static: compared at the oracle's address instead
        r.private_reads += 1
        if len(r.private_addrs) < 32:
            r.private_addrs.add(address)

    def _on_cov(self, uc, address, size, r):
        r.blocks.add(address)

    def _on_wild(self, uc, address, size, user):
        """Execution reached memory that holds no native code (an indirect call through a planted pointer, or a
        jump to an original .text address): an intercepted call of that address, as on x86."""
        if address == (uc.reg_read(A.UC_ARM64_REG_LR) & 0xffffffff):
            # a `ret` to a garbage return address: "returning" from the intercepted call would land on it again,
            # forever, without executing an instruction (the step limit never fires): a fault
            self._fault = 'fault:x:%08x' % address
            uc.emu_stop()
            return
        self._on_stub(uc, address, size, user, wild=True)

    def _on_stub(self, uc, address, size, user, wild=False):
        case, r = user
        if wild:
            target, name = address, None
            if libcalls.IMPORT_BASE <= address < libcalls.IMPORT_BASE + libcalls.IMPORT_MAX * 16:
                name = (self.imports.get((address - libcalls.IMPORT_BASE) // 16) or ('?', 0))[0]
                target = libcalls.import_id(name)
        elif address < a64image.NAMED_STUB:
            target = address - a64image.CODE_STUB
            name = None
        else:
            target = self.stub_orig.get(address, -address)
            name = self.image.stubs.get(address)
        if not wild and target is not None and target in self.iat_names:
            # an import called through its IAT slot (S2's IAT binding): the import, as on x86
            name = self.iat_names[target]
            target = libcalls.import_id(name)
        if self._builtin(uc, name, target, r):
            return
        if name == '__stack_chk_fail':
            self._fault = 'stack-protector'; uc.emu_stop(); return
        k = len(r.calls)
        if k >= MAX_CALLS:                    # intercepted calls run no instructions: bound them like steps
            self._fault = 'timeout'
            uc.emu_stop()
            return
        sp = uc.reg_read(A.UC_ARM64_REG_SP)
        try:
            stk = bytes(uc.mem_read(sp, 64))
        except UcError:
            stk = b''
        r.calls.append(Call(target, [uc.reg_read(XR[i]) for i in range(8)], [uc.reg_read(VR[i]) for i in range(8)],
                            uc.reg_read(A.UC_ARM64_REG_X8), stk))
        r.calls[-1].addr = address
        if libcalls.noreturn_a64(name) or (name is None and target in libcalls.NORETURN_X86):
            r.noreturn = name or libcalls.NORETURN_X86[target]   # never returns: the run ends here (difffuzz-14)
            uc.emu_stop()
            return
        rb = case.ret_bytes(k)
        x8 = uc.reg_read(A.UC_ARM64_REG_X8) & 0xffffffff
        for i in list(range(1, 18)):
            uc.reg_write(XR[i], world.garbage32(case.seed, 600 + i + 32 * k))
        for i in list(range(1, 8)) + list(range(16, 32)):
            uc.reg_write(VR[i], world.garbage32(case.seed, 700 + i + 32 * k))
        x0 = int.from_bytes(rb[:8], 'little')
        cv = None
        if self.callee_conv is not None and target is not None and target >= 0:
            try:
                cv = self.callee_conv(target)
            except Exception:  # noqa: BLE001
                cv = None
        ccv = None
        if self.caller_conv is not None and target is not None and target >= 0:
            try:
                ccv = self.caller_conv(target)
            except Exception:  # noqa: BLE001
                ccv = None
        ck = world.canned_kind((cv, ccv))
        if target in world.ALLOCATORS:
            x0 = world.alloc_result(k)
        elif world.returns_this((cv, ccv)):
            x0 = r.calls[-1].args[0] & 0xffffffff      # this (x0), as the oracle gets ecx (difffuzz-19)
        elif ck == 'ref':
            x0 = world.ref_result(k)
        elif ck in ('bool', 'untyped'):
            x0 = (x0 & ~0xff) | (rb[0] & 1)            # a real bool result is 0/1
        uc.reg_write(XR[0], x0)
        uc.reg_write(VR[0], int.from_bytes(rb[:16], 'little'))
        if cv is not None and cv.hidden and cv.ret is not None:
            # the MSVC side returns this value through memory: the same bytes here, wherever AAPCS64 puts them
            res = world.result_bytes(case, k, cv.ret.size)
            for voff, cont, coff, ln in cv.a64_ret:
                if cont == ('mem', 'x8'):
                    libcalls.Mem(uc, r.writes, self._ensure, self._norm_bytes).write(x8 + voff, res[voff:voff + ln])
                elif cont[0] in ('x', 'v'):
                    reg = XR[cont[1]] if cont[0] == 'x' else VR[cont[1]]
                    width = 8 if cont[0] == 'x' else 16
                    cur = bytearray((uc.reg_read(reg) & ((1 << (8 * width)) - 1)).to_bytes(width, 'little'))
                    cur[coff:coff + ln] = res[voff:voff + ln]
                    uc.reg_write(reg, int.from_bytes(bytes(cur), 'little'))
        uc.reg_write(A.UC_ARM64_REG_PC, uc.reg_read(A.UC_ARM64_REG_LR))

    def _builtin(self, uc, name, target, r):
        if name == 'fnvnx_interlocked_slow':
            # S1-rules: the misaligned path of the shim's inline Interlocked* (fnvnx_win32.h; sync.cpp's split lock).
            # The oracle's lock-prefixed instruction is not a call either: run it as that Interlocked function
            op = uc.reg_read(XR[0]) & 0xffffffff
            fn = libcalls.INTERLOCKED_SLOW_OPS.get(op, 'InterlockedCompareExchange')
            args = [uc.reg_read(XR[i]) & 0xffffffff for i in (1, 2, 3)]
            v = libcalls.run_import(fn, libcalls.Mem(uc, r.writes, self._ensure, self._norm_bytes), args)
            uc.reg_write(XR[0], v & 0xffffffff)
            uc.reg_write(A.UC_ARM64_REG_PC, uc.reg_read(A.UC_ARM64_REG_LR))
            return True
        if name and libcalls.import_name(name)[0] in libcalls.IMPORT_BUILTINS:
            args = [uc.reg_read(XR[i]) & 0xffffffff for i in range(4)]
            v = libcalls.run_import(libcalls.import_name(name)[0], libcalls.Mem(uc, r.writes, self._ensure, self._norm_bytes), args)
            uc.reg_write(XR[0], v & 0xffffffff)
            uc.reg_write(A.UC_ARM64_REG_PC, uc.reg_read(A.UC_ARM64_REG_LR))
            return True
        bname = libcalls.A64.get(name) if name else None
        if bname is None and target is not None and target >= 0:
            bname = libcalls.X86.get(target)
            if bname not in libcalls.A64.values():
                bname = None
        if bname is None:
            return False
        if bname in ('sincos', 'sincosf'):              # void sincos(x, double *s, double *c)
            v = uc.reg_read(VR[0])
            f = bname.endswith('f')
            x = struct.unpack('<f' if f else '<d', (v & (2 ** (32 if f else 64) - 1)).to_bytes(4 if f else 8, 'little'))[0]
            s, c = libcalls.run_float('sin', x), libcalls.run_float('cos', x)
            m = libcalls.Mem(uc, r.writes, self._ensure, self._norm_bytes)
            m.write(uc.reg_read(XR[0]) & 0xffffffff, struct.pack('<f' if f else '<d', s))
            m.write(uc.reg_read(XR[1]) & 0xffffffff, struct.pack('<f' if f else '<d', c))
            uc.reg_write(A.UC_ARM64_REG_PC, uc.reg_read(A.UC_ARM64_REG_LR))
            return True
        if bname in libcalls.CRT_MATH:                   # libm: v0 (, v1) -> v0, double or float (`f`)
            f = bname.endswith('f') and bname[:-1] in libcalls.CRT_MATH
            fmt, mask = ('<f', 0xffffffff) if f else ('<d', 2 ** 64 - 1)
            xs = [struct.unpack(fmt, (uc.reg_read(VR[i]) & mask).to_bytes(4 if f else 8, 'little'))[0]
                  for i in range(libcalls.CRT_MATH[bname])]
            uc.reg_write(VR[0], int.from_bytes(struct.pack(fmt, libcalls.crt_math(bname, xs)), 'little'))
        elif bname in libcalls.FLOAT_NAMES:
            v = uc.reg_read(VR[0])
            if bname.endswith('f'):
                x = struct.unpack('<f', (v & 0xffffffff).to_bytes(4, 'little'))[0]
                out = int.from_bytes(struct.pack('<f', libcalls.run_float(bname, x)), 'little')
            else:
                x = struct.unpack('<d', (v & (2 ** 64 - 1)).to_bytes(8, 'little'))[0]
                out = int.from_bytes(struct.pack('<d', libcalls.run_float(bname, x)), 'little')
            uc.reg_write(VR[0], out)
        else:
            args = [uc.reg_read(XR[i]) & 0xffffffff for i in range(4)]
            try:
                v = libcalls.run(bname, libcalls.Mem(uc, r.writes, self._ensure, self._norm_bytes), args)
            except libcalls.TooLong:
                self._fault = 'fault:libcall-length'; uc.emu_stop(); return True
            uc.reg_write(XR[0], v & 0xffffffff)
        uc.reg_write(A.UC_ARM64_REG_PC, uc.reg_read(A.UC_ARM64_REG_LR))
        return True

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
                self._ensure(placement.get('sret', x86emu.RESULT_BUF) + voff, ln)
                b = bytes(uc.mem_read(placement.get('sret', x86emu.RESULT_BUF) + voff, ln))
                coff = 0
            else:
                continue
            out[voff:voff + ln] = b[coff:coff + ln]
        return bytes(out)
