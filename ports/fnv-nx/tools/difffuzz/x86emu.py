"""x86emu.py - run one oracle function under Unicorn x86-32 in the shared world, intercepting outgoing calls.

The oracle bytes (pristine decomp, MSVC 2008, relocated to original addresses) are mapped at their original
addresses; the rest of .text is 0xCC. Arguments go where MSVC puts them (conv.Conv: ecx/edx for thiscall/fastcall,
the stack, the hidden result pointer). The return address is an epilogue stub that jumps to the sentinel; for a float
result the stub first stores ST0 with `fstp tbyte`, so the full x87 80-bit value is kept.

Outgoing calls: a basic block starting outside the loaded functions but inside .text is a call (or tail jump) to that
ORIGINAL address. The hook records (target, ecx, edx, stack words), loads the canned result for call k (eax, edx; ST0
via a `fld` stub for float results when the callee is known to return one), pops the return address and resumes at
the caller. The callee's own stack cleanup (stdcall/thiscall `ret N`) is applied when the callee's convention is
known (callee_pop), else none (cdecl).
"""
import bisect
import struct

from unicorn import (Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_BLOCK, UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_WRITE,
                     UC_HOOK_INTR, UC_PROT_ALL, UC_PROT_READ, UC_PROT_WRITE, UcError)
from unicorn.x86_const import (UC_X86_REG_ESP, UC_X86_REG_EIP, UC_X86_REG_EAX, UC_X86_REG_EDX, UC_X86_REG_ECX,
                               UC_X86_REG_EBX, UC_X86_REG_EBP, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_GDTR,
                               UC_X86_REG_FS, UC_X86_REG_SS, UC_X86_REG_DS, UC_X86_REG_ES, UC_X86_REG_FPCW,
                               UC_X86_REG_FPSW)
import libcalls
import world

PAGE = world.PAGE
GDT = 0x0e000000
TEB = 0x0e001000
STUBS = 0x0e002000          # epilogue stubs
FRET = 0x0e003000           # float result slot (80-bit)
RESULT_BUF = 0x0e004000     # default hidden result buffer
FLD_STUB = 0x0e005000       # `fld qword [FLD_VAL]; ret` for float results of intercepted calls
FLD_VAL = 0x0e005800
BUILTIN_STUB = 0x0e006000   # x87 helper code (_ftol2, _CIsqrt, ...) and its scratch slots
BUILTIN_SLOT = 0x0e006800
FPCW_MSVC = 0x027f          # MSVC CRT default: 53-bit precision, round to nearest, exceptions masked


def _desc(base, limit, access, flags=0xC):
    return struct.pack('<HHBBBB', limit & 0xffff, base & 0xffff, (base >> 16) & 0xff, access,
                       ((limit >> 16) & 0xf) | (flags << 4), (base >> 24) & 0xff)


class Call:
    __slots__ = ('target', 'args', 'ret_to')

    def __init__(self, target, args, ret_to):
        self.target, self.args, self.ret_to = target, args, ret_to


class Result:
    def __init__(self):
        self.ret = None          # logical return bytes
        self.ret_ext = None      # x87 80-bit result (float returns)
        self.calls = []
        self.writes = {}         # addr -> last byte written (compared regions)
        self.error = None        # None | 'timeout' | 'fault:...' | 'div0' | 'exc:...'
        self.blocks = set()
        self.saved_ok = True     # callee-saved registers preserved


class X86Engine:
    def __init__(self, world_obj, text_funcs, callee_info=None, follow=None):
        """text_funcs: {addr: bytes} executed for real. callee_info(addr) -> (pop_bytes, ret_kind) or None.
        follow(addr) -> oracle bytes of a function outside the unit to run for real instead of intercepting, or None."""
        self.follow = follow
        self.followed = set()
        self.world = world_obj
        self.text_funcs = text_funcs
        self.callee_info = callee_info or (lambda a: None)
        self.ranges = sorted((a, a + len(c)) for a, c in text_funcs.items())
        self._starts = [lo for lo, _ in self.ranges]
        self.mapped = set()
        self.dynamic = set()
        uc = self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        for base in (GDT, TEB, STUBS, FRET, RESULT_BUF, FLD_STUB, BUILTIN_STUB):
            uc.mem_map(base, PAGE, UC_PROT_ALL)
            self.mapped.add(base)
        uc.mem_map(world.STACK[0], world.STACK[1] - world.STACK[0], UC_PROT_READ | UC_PROT_WRITE)
        self.mapped.update(range(world.STACK[0], world.STACK[1], PAGE))
        gdt = b'\0' * 8 + _desc(0, 0xfffff, 0x9a) + _desc(0, 0xfffff, 0x92) + _desc(TEB, 0xfff, 0x92, 0x4)
        uc.mem_write(GDT, gdt)
        uc.reg_write(UC_X86_REG_GDTR, (0, GDT, len(gdt) - 1, 0))
        uc.reg_write(UC_X86_REG_SS, 2 << 3)     # flat 32-bit data segment: 32-bit stack pointer
        uc.reg_write(UC_X86_REG_DS, 2 << 3)
        uc.reg_write(UC_X86_REG_ES, 2 << 3)
        uc.reg_write(UC_X86_REG_FS, 3 << 3)     # TEB: SEH chain head and self pointer
        uc.mem_write(TEB, struct.pack('<I', 0xffffffff) + struct.pack('<II', world.SP0 + 0x10000, world.STACK[0])
                     + b'\0' * 0xc + struct.pack('<I', TEB))
        uc.mem_write(FLD_STUB, b'\xdd\x05' + struct.pack('<I', FLD_VAL) + b'\xc3')
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
        """Forget the previous case's world pages (the engine is reused across runs of one function)."""
        if self.followed:
            self.ranges = sorted((a, a + len(c)) for a, c in self.text_funcs.items())
            self._starts = [lo for lo, _ in self.ranges]
            self.followed = set()
        for p in self.dynamic:
            self.uc.mem_unmap(p, PAGE)
            self.mapped.discard(p)
        self.dynamic = set()
        self.world = world_obj

    def _ensure(self, addr, size=1):
        for p in range(world.page_of(addr), addr + size, PAGE):
            self._map(p)

    def _on_unmapped(self, uc, access, address, size, value, data):
        if not world.mappable(address, 'x86') or not world.mappable(address + size - 1, 'x86'):
            self._fault = 'fault:%s:%08x' % ({20: 'w', 21: 'x'}.get(access, 'r'), address)
            return False
        self._ensure(address, size)
        return True

    def _on_intr(self, uc, intno, data):
        self._fault = 'div0' if intno == 0 else 'exc:%d' % intno
        uc.emu_stop()

    def _in_loaded(self, a):
        i = bisect.bisect_right(self._starts, a) - 1
        return i >= 0 and a < self.ranges[i][1]

    def run(self, conv, case, placement, max_steps=200000, start=None):
        uc = self.uc
        r = Result()
        for a, code in self.text_funcs.items():
            self._ensure(a, len(code))
            uc.mem_write(a, code)
        uc.mem_write(world.SP0 - 0x8000, bytes(0x7000) + world.stack_fill(case.seed, 0x1000))
        uc.mem_write(world.SP0, bytes(world.STACK[1] - world.SP0))
        # float epilogue: fld st0; fstp dword/qword [FRET+16] (the caller's rounding); fstp tbyte [FRET]; jmp SENTINEL
        st = b'\xd9\xc0' + (b'\xd9\x1d' if conv.ret is not None and conv.ret.size == 4 else b'\xdd\x1d') + \
            struct.pack('<I', FRET + 16) + b'\xdb\x3d' + struct.pack('<I', FRET)
        st += b'\xe9' + struct.pack('<i', (world.SENTINEL - (STUBS + len(st) + 5)) & 0xffffffff)
        uc.mem_write(STUBS, st)
        uc.mem_write(STUBS + 0x20, b'\xe9' + struct.pack('<i', (world.SENTINEL - (STUBS + 0x20 + 5)) & 0xffffffff))
        self._ensure(world.SENTINEL, 1)
        uc.reg_write(UC_X86_REG_FPCW, FPCW_MSVC)
        ret_stub = STUBS if conv.x86_ret == 'st0' else STUBS + 0x20
        # garbage in every register that is not an argument (identical per seed)
        for i, reg in enumerate((UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_EBX, UC_X86_REG_ESI,
                                 UC_X86_REG_EDI, UC_X86_REG_EBP)):
            uc.reg_write(reg, world.garbage32(case.seed, i))
        self._place_args(conv, case, placement, world.SP0, ret_stub)
        saved = [uc.reg_read(x) for x in (UC_X86_REG_EBX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP)]
        hb = uc.hook_add(UC_HOOK_BLOCK, self._on_block, user_data=(case, r), begin=world.TEXT[0],
                         end=world.TEXT[1] - 1)
        hws = [uc.hook_add(UC_HOOK_MEM_WRITE, self._on_write, user_data=r, begin=lo, end=hi - 1)
               for lo, hi in world.COMPARED]
        start = start if start is not None else next(iter(self.text_funcs))
        self._fault = None
        try:
            uc.emu_start(start, world.SENTINEL, count=max_steps)
        except UcError as e:
            r.error = self._fault or 'fault:uc:%s' % e
        finally:
            uc.hook_del(hb)
            for h in hws:
                uc.hook_del(h)
        eip = uc.reg_read(UC_X86_REG_EIP)
        if r.error is None and self._fault:
            r.error = self._fault
        if r.error is None and eip != world.SENTINEL:
            r.error = 'timeout'
        if r.error is None:
            r.ret, r.ret_ext = self._read_ret(conv, placement)
            now = [uc.reg_read(x) for x in (UC_X86_REG_EBX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP)]
            r.saved_ok = now == saved
        return r

    def _place_args(self, conv, case, placement, esp, ret_stub):
        import conv as convmod
        uc = self.uc
        stack_items = []
        for lab, space, off, size in conv.x86:
            if lab == 'sret':
                data = struct.pack('<I', placement.get('sret', RESULT_BUF))
            else:
                v = next(x for x in conv.vals if x.label == lab)
                if lab in placement and (v.is_ptr or lab == 'this'):
                    data = struct.pack('<I', placement[lab])
                elif v.kind in ('gpr', 'fpr'):
                    data = convmod.extend(v, case.args.get(lab, b'').ljust(v.size, b'\0')[:v.size], case.pad)
                else:   # aggregate by value: its bytes on the stack
                    data = case.args.get(lab, b'').ljust(v.size, b'\0')[:v.size]
                    data = data.ljust((len(data) + 3) // 4 * 4, b'\0')
            if space == 'ecx':
                uc.reg_write(UC_X86_REG_ECX, world.u32(data))
            elif space == 'edx':
                uc.reg_write(UC_X86_REG_EDX, world.u32(data))
            else:
                stack_items.append((off, data))
        frame = bytearray(max((o + len(d) for o, d in stack_items), default=0))
        for o, d in stack_items:
            frame[o:o + len(d)] = d
        esp = (esp - len(frame)) & ~0xf
        esp -= 4
        uc.mem_write(esp + 4, bytes(frame))
        uc.mem_write(esp, struct.pack('<I', ret_stub))
        uc.reg_write(UC_X86_REG_ESP, esp)

    def _on_write(self, uc, access, address, size, value, r):
        for i in range(size):
            r.writes[address + i] = (value >> (8 * i)) & 0xff

    def _on_block(self, uc, address, size, user):
        case, r = user
        if self._in_loaded(address):
            r.blocks.add(address)
            return
        esp = uc.reg_read(UC_X86_REG_ESP)
        retaddr = world.u32(uc.mem_read(esp, 4))
        words = [world.u32(uc.mem_read(esp + 4 + 4 * i, 4)) for i in range(16)]
        bname = libcalls.X86.get(address)
        if bname is not None and self._builtin(uc, bname, esp, retaddr, words, r):
            return
        code = self.follow(address) if self.follow else None
        if code:
            # the native object never calls this address (GCC inlined it, typically a header inline function MSVC
            # called out of line): run the oracle's copy for real so both sides do the same work
            self._ensure(address, len(code))
            uc.mem_write(address, code)
            self.ranges.append((address, address + len(code)))
            self.ranges.sort()
            self._starts = [lo for lo, _ in self.ranges]
            self.followed.add(address)
            uc.reg_write(UC_X86_REG_EIP, address)
            return
        k = len(r.calls)
        r.calls.append(Call(address, [uc.reg_read(UC_X86_REG_ECX), uc.reg_read(UC_X86_REG_EDX)] + words, retaddr))
        rb = case.ret_bytes(k)
        info = self.callee_info(address)
        pop, kind = info if info else (0, 'int')
        uc.reg_write(UC_X86_REG_EAX, world.u32(rb, 0))
        uc.reg_write(UC_X86_REG_EDX, world.u32(rb, 4))
        uc.reg_write(UC_X86_REG_ECX, world.garbage32(case.seed, 100 + k))
        if kind in ('f32', 'f64'):
            v = struct.unpack('<f', rb[:4])[0] if kind == 'f32' else struct.unpack('<d', rb[:8])[0]
            uc.mem_write(FLD_VAL, struct.pack('<d', v))
            # FLD_STUB ends in `ret`, which pops our return address: leave it on the stack
            uc.reg_write(UC_X86_REG_ESP, esp)
            uc.mem_write(esp, struct.pack('<I', retaddr))
            if pop:
                # emulate `ret N`: move the return address up by N and let the stub's `ret` pop it
                uc.mem_write(esp + pop, struct.pack('<I', retaddr))
                uc.reg_write(UC_X86_REG_ESP, esp + pop)
            uc.reg_write(UC_X86_REG_EIP, FLD_STUB)
            return
        uc.reg_write(UC_X86_REG_ESP, esp + 4 + pop)
        uc.reg_write(UC_X86_REG_EIP, retaddr)

    def _builtin(self, uc, name, esp, ret, words, r):
        """Run a CRT function or compiler helper for real (not traced). True when handled."""
        if name == 'cookie':                     # @__security_check_cookie@4 (ecx): nothing to check here
            uc.reg_write(UC_X86_REG_ESP, esp + 4); uc.reg_write(UC_X86_REG_EIP, ret); return True
        if name == 'chkstk':                     # _chkstk/_alloca_probe: esp -= eax (probing is a no-op here)
            uc.reg_write(UC_X86_REG_ESP, (esp + 4 - uc.reg_read(UC_X86_REG_EAX)) & 0xffffffff)
            uc.reg_write(UC_X86_REG_EIP, ret); return True
        stub = libcalls.x87_stub(name, BUILTIN_SLOT)
        if stub is not None:
            uc.mem_write(BUILTIN_STUB, stub)
            uc.reg_write(UC_X86_REG_EIP, BUILTIN_STUB); return True
        if name in ('allmul', 'alldiv'):         # stdcall (a_lo, a_hi, b_lo, b_hi) -> edx:eax, ret 16
            a = words[0] | words[1] << 32
            b = words[2] | words[3] << 32
            if name == 'allmul':
                v = (a * b) & (2 ** 64 - 1)
            else:
                sa, sb = a - (a >> 63 << 64), b - (b >> 63 << 64)
                if sb == 0:
                    self._fault = 'div0'; uc.emu_stop(); return True
                q = abs(sa) // abs(sb)
                v = (q if (sa < 0) == (sb < 0) else -q) & (2 ** 64 - 1)
            uc.reg_write(UC_X86_REG_EAX, v & 0xffffffff); uc.reg_write(UC_X86_REG_EDX, v >> 32)
            uc.reg_write(UC_X86_REG_ESP, esp + 4 + 16); uc.reg_write(UC_X86_REG_EIP, ret); return True
        if name in ('allshl', 'allshr'):         # edx:eax << / >> cl (MSVC: counts >= 64 give 0 / the sign)
            v = uc.reg_read(UC_X86_REG_EAX) | uc.reg_read(UC_X86_REG_EDX) << 32
            c = uc.reg_read(UC_X86_REG_ECX) & 0xff
            if name == 'allshl':
                v = 0 if c >= 64 else (v << (c & 31 if c < 32 else c)) & (2 ** 64 - 1)
            else:
                sv = v - (v >> 63 << 64)
                v = (sv >> min(c, 63)) & (2 ** 64 - 1)
            uc.reg_write(UC_X86_REG_EAX, v & 0xffffffff); uc.reg_write(UC_X86_REG_EDX, v >> 32)
            uc.reg_write(UC_X86_REG_ESP, esp + 4); uc.reg_write(UC_X86_REG_EIP, ret); return True
        try:
            v = libcalls.run(name, libcalls.Mem(uc, r.writes, self._ensure), words)
        except KeyError:
            return False
        uc.reg_write(UC_X86_REG_EAX, v & 0xffffffff)
        uc.reg_write(UC_X86_REG_ESP, esp + 4); uc.reg_write(UC_X86_REG_EIP, ret)
        return True

    def _read_ret(self, conv, placement):
        uc = self.uc
        k = conv.x86_ret
        if k == 'none':
            return None, None
        if k == 'eax':
            return struct.pack('<I', uc.reg_read(UC_X86_REG_EAX))[:conv.ret.size], None
        if k == 'edx:eax':
            return struct.pack('<II', uc.reg_read(UC_X86_REG_EAX), uc.reg_read(UC_X86_REG_EDX))[:conv.ret.size], None
        if k == 'st0':
            ext = bytes(uc.mem_read(FRET, 10))
            return bytes(uc.mem_read(FRET + 16, conv.ret.size)), ext
        if k == 'sret':
            return bytes(uc.mem_read(placement.get('sret', RESULT_BUF), conv.ret.size)), None
        return None, None


def ext_to_double(ext):
    """x87 80-bit extended -> Python float (round to nearest double; float() of the exact rational)."""
    m = int.from_bytes(ext[:8], 'little')
    se = int.from_bytes(ext[8:10], 'little')
    exp = se & 0x7fff
    neg = bool(se & 0x8000)
    if exp == 0x7fff:
        return float('nan') if m & ((1 << 63) - 1) else (-float('inf') if neg else float('inf'))
    if m == 0:
        return -0.0 if neg else 0.0
    from fractions import Fraction
    e = (exp if exp else 1) - 16383 - 63
    q = Fraction(m) * (Fraction(2) ** e)
    try:
        v = float(q)
    except OverflowError:
        v = float('inf')
    return -v if neg else v
