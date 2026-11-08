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

from unicorn import (Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_BLOCK, UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_WRITE, UC_HOOK_MEM_READ,
                     UC_HOOK_INTR, UC_HOOK_CODE, UC_PROT_ALL, UC_PROT_READ, UC_PROT_WRITE, UcError)
from unicorn.x86_const import (UC_X86_REG_ESP, UC_X86_REG_EIP, UC_X86_REG_EAX, UC_X86_REG_EDX, UC_X86_REG_ECX,
                               UC_X86_REG_EBX, UC_X86_REG_EBP, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_GDTR,
                               UC_X86_REG_FS, UC_X86_REG_SS, UC_X86_REG_DS, UC_X86_REG_ES, UC_X86_REG_FPCW,
                               UC_X86_REG_FPSW, UC_X86_REG_FP0)
import libcalls
import world

PAGE = world.PAGE
GDT = 0x0e000000
TEB = 0x0e001000
# thread-local storage (difffuzz-16): fs:[0x2c] -> the slot array at TLS_ARRAY (in the TEB page), slot 0 -> a zeroed
# private block (not compared, like the native tpidr_el0 block); offsets from coffobj.TLS_IMAGE_START may be
# negative (a decomp name below it), so the slot points into the middle of the block
TLS_ARRAY = TEB + 0x100
TLS_BLOCK, TLS_SIZE = 0x0e040000, 0x10000
TLS_PTR = TLS_BLOCK + 0x4000
STUBS = 0x0e002000          # epilogue stubs
FRET = 0x0e003000           # float result slot (80-bit)
RESULT_BUF = 0x0e004000     # default hidden result buffer
FLD_STUB = 0x0e005000       # `fld qword [FLD_VAL]; ret` for float results of intercepted calls
FLD_VAL = 0x0e005800
BUILTIN_STUB = 0x0e006000   # x87 helper code (_ftol2, _CIsqrt, ...) and its scratch slots
BUILTIN_SLOT = 0x0e006800
FPCW_MSVC = 0x027f
MAX_CALLS = 20000           # intercepted calls per run (each costs no step)
REGS = {'eax': UC_X86_REG_EAX, 'ecx': UC_X86_REG_ECX, 'edx': UC_X86_REG_EDX, 'ebx': UC_X86_REG_EBX,
        'esi': UC_X86_REG_ESI, 'edi': UC_X86_REG_EDI}          # MSVC CRT default: 53-bit precision, round to nearest, exceptions masked


def _desc(base, limit, access, flags=0xC):
    return struct.pack('<HHBBBB', limit & 0xffff, base & 0xffff, (base >> 16) & 0xff, access,
                       ((limit >> 16) & 0xf) | (flags << 4), (base >> 24) & 0xff)



def x87_result_use(uc, retaddr, fret='int', depth=0):
    """'f32' / 'f64' when the code the call returns to consumes ST0 (the caller takes a float result from the
    callee), else 'int'. For an intercepted call whose callee nothing describes (a wild or unplanted indirect call,
    a CRT name without a type): the native side always finds the canned bytes in v0, so the oracle gets the same
    value in ST0 where it reads one. fst/fstp m32 -> f32, m64 -> f64; x87 arithmetic, fxch, fchs, fabs, fstp st(i)
    -> f32; the caller's cleanup (add esp, pop) and epilogue (mov esp, ebp / leave / pop) are stepped over, and a
    `ret` returns the fuzzed function's own result kind `fret` (a tail position)."""
    if depth > 8:
        return 'int'
    try:
        b = bytes(uc.mem_read(retaddr, 6))
    except Exception:  # noqa: BLE001
        return 'int'
    skip = None
    if b[:2] == b'\x83\xc4':
        skip = 3
    elif b[:2] == b'\x81\xc4':
        skip = 6
    elif b[:2] == b'\x8b\xe5':
        skip = 2
    elif b[0] in (0x59, 0x5b, 0x5d, 0x5e, 0x5f, 0xc9):     # pop ecx/ebx/ebp/esi/edi, leave
        skip = 1
    if skip:
        return x87_result_use(uc, retaddr + skip, fret, depth + 1)
    if b[0] in (0xc3, 0xc2):
        return fret
    if b[0] == 0xeb or b[0] == 0xe9:       # a jump (the shared epilogue of a `return call()` after the call): follow it
        rel = int.from_bytes(b[1:2], 'little', signed=True) + 2 if b[0] == 0xeb else \
            int.from_bytes(b[1:5], 'little', signed=True) + 5
        return x87_result_use(uc, retaddr + rel, fret, depth + 1)
    b0, m = b[0], b[1]
    if b0 == 0xd9 and m == 0xc0:          # fld st0 (the float epilogue stub): the store after it decides
        nxt = x87_result_use(uc, retaddr + 2, fret, depth + 1)
        return nxt if nxt != 'int' else 'f32'
    mod, reg = m >> 6, (m >> 3) & 7
    if b0 in (0xd9, 0xdd) and mod != 3 and reg in (2, 3):
        return 'f64' if b0 == 0xdd else 'f32'
    if b0 in (0xd8, 0xdc, 0xde) or (b0 == 0xd9 and (0xc8 <= m <= 0xcf or m in (0xe0, 0xe1))) or \
            (b0 == 0xdd and 0xd8 <= m <= 0xdf):
        return 'f32'
    # the rest of the x87 instructions that read ST0 (a callee's float result): integer arithmetic and compares
    # (fiadd/fimul/ficom/fisub/fidiv m32: DA /r), fist/fistp/fisttp, fcmov and fucompp, fcomi/fucomi(p), fucom(p),
    # ftst/fxam, and the transcendental / rounding group (f2xm1 .. fcos); the fld constants (D9 E8..EE) and
    # fdecstp/fincstp do not read it
    if (b0 == 0xda and (mod != 3 or 0xc0 <= m <= 0xdf or m == 0xe9)) or \
            (b0 == 0xdb and (mod != 3 and reg in (1, 2, 3) or 0xe8 <= m <= 0xf7)) or \
            (b0 == 0xdf and (mod != 3 and reg in (1, 2, 3, 7) or 0xe8 <= m <= 0xf7)) or \
            (b0 == 0xdd and (mod != 3 and reg == 1 or 0xe0 <= m <= 0xef)) or \
            (b0 == 0xd9 and (m in (0xe4, 0xe5) or 0xf0 <= m <= 0xf5 or 0xf8 <= m <= 0xff)):
        return 'f32'
    return 'int'

class Call:
    __slots__ = ('target', 'args', 'ret_to', 'regs', 'pushed', 'pop_unknown')

    def __init__(self, target, args, ret_to, regs=None):
        self.target, self.args, self.ret_to, self.regs = target, args, ret_to, regs or {}
        self.pushed = None       # stack bytes the caller passed, when its code shows it
        self.pop_unknown = False  # the callee's `ret N` is unknown (no oracle code, no declaration): pop_override


class Result:
    def __init__(self):
        self.ret = None          # logical return bytes
        self.ret_ext = None      # x87 80-bit result (float returns)
        self.calls = []
        self.writes = {}         # addr -> last byte written (compared regions)
        self.error = None        # None | 'timeout' | 'fault:...' | 'div0' | 'exc:...'
        self.blocks = set()
        self.saved_ok = True     # callee-saved registers preserved
        self.noreturn = None            # the noreturn callee the run ended at (libcalls.NORETURN_X86)
        self.esp_ok = None              # the target returned with esp = entry + 4 + its own ret N
        self.ret_smashed = False        # the target wrote its own return-address slot (a frame overrun, F1b)


class X86Engine:
    def __init__(self, world_obj, text_funcs, callee_info=None, follow=None, imports=None):
        """text_funcs: {addr: bytes} executed for real. callee_info(addr) -> (pop_bytes, ret_kind) or None.
        follow(addr) -> oracle bytes of a function outside the unit to run for real instead of intercepting, or None."""
        self.follow = follow
        self.followed = set()
        # {return address: bytes} the intercepted callee of an unknown convention pops (runner.Pair learns them)
        self.pop_override = {}
        self.imports = imports or {}       # import stub index -> (name, stdcall bytes)
        self.world = world_obj
        self.text_funcs = text_funcs
        self.callee_info = callee_info or (lambda a: None)
        self._fret = 'int'
        self.ranges = sorted((a, a + len(c)) for a, c in text_funcs.items())
        self._starts = [lo for lo, _ in self.ranges]
        self.mapped = set()
        self.dynamic = set()
        uc = self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        uc.mem_map(libcalls.IMPORT_BASE, (libcalls.IMPORT_MAX * 16 + PAGE - 1) & ~(PAGE - 1), UC_PROT_ALL)
        uc.mem_write(libcalls.IMPORT_BASE, b'\xcc' * (libcalls.IMPORT_MAX * 16))
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
        uc.mem_write(TEB + 0x2c, struct.pack('<I', TLS_ARRAY))
        uc.mem_write(TLS_ARRAY, struct.pack('<I', TLS_PTR))
        uc.mem_map(TLS_BLOCK, TLS_SIZE, UC_PROT_READ | UC_PROT_WRITE)
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

    def _bmem(self, uc, r):
        """Memory for a builtin: faults where this machine's own load or store would (libcalls.Fault)."""
        return libcalls.Mem(uc, r.writes, self._ensure,
                            check=lambda p, a: p in self.mapped or world.mappable(a, 'x86') or
                            libcalls.uc_mapped(uc, a))

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
        uc.mem_write(TLS_BLOCK, bytes(TLS_SIZE))
        # float epilogue: fld st0; fstp dword/qword [FRET+16] (the caller's rounding); fstp tbyte [FRET]; jmp SENTINEL
        st = b'\xd9\xc0' + (b'\xd9\x1d' if conv.ret is not None and conv.ret.size == 4 else b'\xdd\x1d') + \
            struct.pack('<I', FRET + 16) + b'\xdb\x3d' + struct.pack('<I', FRET)
        st += b'\xe9' + struct.pack('<i', (world.SENTINEL - (STUBS + len(st) + 5)) & 0xffffffff)
        uc.mem_write(STUBS, st)
        uc.mem_write(STUBS + 0x20, b'\xe9' + struct.pack('<i', (world.SENTINEL - (STUBS + 0x20 + 5)) & 0xffffffff))
        self._ensure(world.SENTINEL, 1)
        uc.reg_write(UC_X86_REG_FPCW, world.X87_CW)
        ret_stub = STUBS if conv.x86_ret == 'st0' else STUBS + 0x20
        self._fret = ('f32' if conv.ret.size == 4 else 'f64') if conv.x86_ret == 'st0' else 'int'
        # garbage in every register that is not an argument (identical per seed)
        for i, reg in enumerate((UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_EBX, UC_X86_REG_ESI,
                                 UC_X86_REG_EDI, UC_X86_REG_EBP)):
            uc.reg_write(reg, world.garbage32(case.seed, i))
        self._place_args(conv, case, placement, world.SP0, ret_stub)
        saved = [uc.reg_read(x) for x in (UC_X86_REG_EBX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP)]
        hb = uc.hook_add(UC_HOOK_BLOCK, self._on_block, user_data=(case, r), begin=world.TEXT[0],
                         end=world.TEXT[1] - 1)
        hb2 = uc.hook_add(UC_HOOK_BLOCK, self._on_import, user_data=(case, r), begin=libcalls.IMPORT_BASE,
                          end=libcalls.IMPORT_BASE + libcalls.IMPORT_MAX * 16 - 1)
        hwild = [uc.hook_add(UC_HOOK_BLOCK, self._on_wild, user_data=(case, r), begin=lo, end=hi - 1)
                 for lo, hi in world.WILD_X86]
        hws = [uc.hook_add(UC_HOOK_MEM_WRITE, self._on_write, user_data=r, begin=lo, end=hi - 1)
               for lo, hi in world.COMPARED]
        hrd = []
        if world.BYTE_READS is not None:
            for a, b in case.mem.items():
                lo = int(a, 16) if isinstance(a, str) else a
                hrd.append(uc.hook_add(UC_HOOK_MEM_READ, self._on_read, user_data=world.BYTE_READS, begin=lo,
                                       end=lo + max(1, len(b)) - 1))
        if world.BG_READS is not None:
            for lo, hi in ((world.NULL_END, world.STACK[0]), (world.STACK[1], world.X86_PRIV[0]),
                           (world.X86_PRIV[1], world.MAX)):
                hrd.append(uc.hook_add(UC_HOOK_MEM_READ, self._on_bg_read, user_data=world.BG_READS, begin=lo,
                                       end=hi - 1))
        hfp = None
        if world.X87_CLAMP is not None:
            world.X87_CLAMP.update(n=0, addr=None, over=0, under=0)
            hfp = uc.hook_add(UC_HOOK_CODE, self._on_fp_clamp, user_data=world.X87_CLAMP, begin=world.TEXT[0],
                              end=world.TEXT[1] - 1)
        start = start if start is not None else next(iter(self.text_funcs))
        esp0 = uc.reg_read(UC_X86_REG_ESP)
        # no MSVC code writes its own return-address slot: a store there is a local buffer overrun (difffuzz-20)
        hrs = uc.hook_add(UC_HOOK_MEM_WRITE, self._on_ret_slot, user_data=r, begin=esp0, end=esp0 + 3)
        self._fault = None
        try:
            uc.emu_start(start, world.SENTINEL, timeout=world.RUN_TIMEOUT_US, count=max_steps)
        except UcError as e:
            r.error = self._fault or 'fault:uc:%s' % e
        finally:
            uc.hook_del(hb)
            uc.hook_del(hb2)
            uc.hook_del(hrs)
            if hfp is not None:
                uc.hook_del(hfp)
            for h in hwild:
                uc.hook_del(h)
            for h in hws:
                uc.hook_del(h)
            for h in hrd:
                uc.hook_del(h)
        eip = uc.reg_read(UC_X86_REG_EIP)
        if r.error is None and self._fault:
            r.error = self._fault
        if r.error is None and eip != world.SENTINEL and not r.noreturn:
            r.error = 'timeout'
        if r.error is None and not r.noreturn:
            r.esp_ok = uc.reg_read(UC_X86_REG_ESP) == (esp0 + 4 + conv.pop) & 0xffffffff
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
            if space in REGS:
                uc.reg_write(REGS[space], world.u32(data.ljust(4, b'\0')))
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

    def _on_read(self, uc, access, address, size, value, log):
        log.setdefault(address, set()).add(size)

    def _on_fp_clamp(self, uc, address, size, rec):
        """world.X87_CLAMP: before each instruction, the x87 registers lose what float (or double) arithmetic could
        not hold: a magnitude of 2**128 (2**1024) or more becomes inf, one below 2**-150 (2**-1075) becomes zero."""
        hi, lo = (128, -150) if rec['kind'] == 'f32' else (1024, -1075)
        for i in range(8):
            mant, se = uc.reg_read(UC_X86_REG_FP0 + i)
            ex = se & 0x7fff
            if not mant or ex == 0 or ex == 0x7fff:
                continue
            e = ex - 16383
            if e >= hi:
                uc.reg_write(UC_X86_REG_FP0 + i, (0x8000000000000000, (se & 0x8000) | 0x7fff))
                rec['over'] = rec.get('over', 0) + 1
            elif e < lo:
                uc.reg_write(UC_X86_REG_FP0 + i, (0, se & 0x8000))
                rec['under'] = rec.get('under', 0) + 1
            else:
                continue
            rec['n'] += 1
            if rec['addr'] is None:
                rec['addr'] = address

    def _on_bg_read(self, uc, access, address, size, value, log):
        e = log.setdefault(address, [set(), None])
        e[0].add(size)
        if size == 1 and e[1] is None:
            try:
                e[1] = bytes(uc.mem_read(address, 1))[0]
            except Exception:  # noqa: BLE001
                pass

    def _on_ret_slot(self, uc, access, address, size, value, r):
        r.ret_smashed = True

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
        try:
            if bname is not None and self._builtin(uc, bname, esp, retaddr, words, r):
                return
        except libcalls.Fault as e:              # the real CRT function faults there too (libcalls.Mem.check)
            self._fault = e.args[0]; uc.emu_stop(); return
        code = self.follow(address) if self.follow and address not in libcalls.NORETURN_X86 else None
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
        if k >= MAX_CALLS:                    # intercepted calls run no instructions: bound them like steps
            self._fault = 'timeout'
            uc.emu_stop()
            return
        r.calls.append(Call(address, [uc.reg_read(UC_X86_REG_ECX), uc.reg_read(UC_X86_REG_EDX)] + words, retaddr,
                            {n: uc.reg_read(REGS[n]) for n in ('eax', 'ebx', 'esi', 'edi')}))
        r.calls[-1].pushed = self._pushed(uc, retaddr, address)
        if address in libcalls.NORETURN_X86:
            r.noreturn = libcalls.NORETURN_X86[address]   # the call never returns: the run ends here (difffuzz-14)
            uc.emu_stop()
            return
        rb = case.ret_bytes(k)
        info = self.callee_info(address)
        pop, kind = info if info else (self.pop_override.get(retaddr, 0), x87_result_use(uc, retaddr, self._fret))
        if not info:
            r.calls[-1].pop_unknown = True
            if world.X87_FLIP_WIDTH and kind in ('f32', 'f64'):
                kind = 'f64' if kind == 'f32' else 'f32'
        eax = world.u32(rb, 0)
        cv = self._callee_conv(address)
        ccv = self._callee_conv(address, caller=True)
        if kind == 'int' and ccv is not None and ccv.x86_ret == 'st0' and ccv.ret is not None and \
                (cv is None or cv.x86_ret != 'st0'):
            # the calling unit declares a float result the callee's definition does not (a void decl in the tree):
            # the caller's code reads ST0 and the native reads v0 (the same canned bytes), so the oracle gets ST0
            kind = 'f32' if ccv.ret.size == 4 else 'f64'
        ck = world.canned_kind((cv, ccv))
        if address in world.ALLOCATORS:
            eax = world.alloc_result(k)
        elif world.returns_this((cv, ccv)):
            eax = r.calls[-1].args[0]                  # this (ecx), as the native gets x0 (difffuzz-19)
        elif ck == 'ref':
            eax = world.ref_result(k)
        elif ck in ('bool', 'untyped'):
            eax = (eax & ~0xff) | (rb[0] & 1)          # a real bool result is 0/1 (MSVC tests al, GCC bit 0)
        if cv is not None and cv.hidden and cv.ret is not None:
            p = self._sret_of(cv, r.calls[-1])
            if p is not None:                          # the result in the caller's buffer, its address in eax
                libcalls.Mem(uc, r.writes, self._ensure).write(p, world.result_bytes(case, k, cv.ret.size))
                eax = p
        uc.reg_write(UC_X86_REG_EAX, eax)
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

    def _on_wild(self, uc, address, size, user):
        """Execution reached memory that holds no code: an indirect call through a planted pointer."""
        self._on_block(uc, address, size, user)

    def _pushed(self, uc, retaddr, address):
        """Stack bytes the caller passed: its cleanup after the call (`add esp, N`, `pop ecx` x n) or what the
        callee pops (`ret N`); None when neither shows. A definition that declares more parameters than the caller
        pushes (a variadic reader such as FUN_005accb0's ExtractArgs) reads past them: not an argument."""
        try:
            b = bytes(uc.mem_read(retaddr, 6))
        except Exception:  # noqa: BLE001
            b = b''
        if b[:2] == b'\x83\xc4':
            return b[2]
        if b[:2] == b'\x81\xc4':
            return int.from_bytes(b[2:6], 'little')
        if b[:1] == b'\x59':
            return 8 if b[1:2] == b'\x59' else 4
        info = self.callee_info(address)
        if info and info[0]:
            return info[0]
        return None

    def _callee_conv(self, address, caller=False):
        f = getattr(self.callee_info, 'caller_conv' if caller else 'conv', None)
        try:
            return f(address) if f else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _sret_of(cv, call):
        """The hidden result pointer the caller passed (MSVC: first stack word, or ecx/edx/another register)."""
        for lab, space, off, size in cv.x86:
            if lab != 'sret':
                continue
            if space == 'ecx':
                return call.args[0]
            if space == 'edx':
                return call.args[1]
            if space in call.regs:
                return call.regs[space]
            if off % 4 == 0 and 2 + off // 4 < len(call.args):
                return call.args[2 + off // 4]
        return None

    def _on_import(self, uc, address, size, user):
        """A call through an IAT slot (pinned to import stub k): the Interlocked family runs, anything else is a
        traced call with the import's identity, a canned result in eax and stdcall cleanup."""
        case, r = user
        name, pop = self.imports.get((address - libcalls.IMPORT_BASE) // 16, ('?', 0))
        esp = uc.reg_read(UC_X86_REG_ESP)
        ret = world.u32(uc.mem_read(esp, 4))
        words = [world.u32(uc.mem_read(esp + 4 + 4 * i, 4)) for i in range(16)]
        if name in libcalls.IMPORT_BUILTINS:
            try:
                v = libcalls.run_import(name, self._bmem(uc, r), words)
            except libcalls.Fault as e:
                self._fault = e.args[0]; uc.emu_stop(); return
        else:
            k = len(r.calls)
            r.calls.append(Call(libcalls.import_id(name), [uc.reg_read(UC_X86_REG_ECX),
                                                           uc.reg_read(UC_X86_REG_EDX)] + words, ret))
            if name in libcalls.NORETURN_IMPORTS:
                r.noreturn = name
                uc.emu_stop()
                return
            v = world.u32(case.ret_bytes(k), 0)
            uc.reg_write(UC_X86_REG_EDX, world.u32(case.ret_bytes(k), 4))
            uc.reg_write(UC_X86_REG_ECX, world.garbage32(case.seed, 100 + k))
        uc.reg_write(UC_X86_REG_EAX, v & 0xffffffff)
        uc.reg_write(UC_X86_REG_ESP, esp + 4 + pop)
        uc.reg_write(UC_X86_REG_EIP, ret)

    def _builtin(self, uc, name, esp, ret, words, r):
        """Run a CRT function or compiler helper for real (not traced). True when handled."""
        if name == 'cookie':                     # @__security_check_cookie@4 (ecx): nothing to check here
            uc.reg_write(UC_X86_REG_ESP, esp + 4); uc.reg_write(UC_X86_REG_EIP, ret); return True
        if name == 'chkstk':                     # _chkstk/_alloca_probe: esp -= eax (probing is a no-op here)
            uc.reg_write(UC_X86_REG_ESP, (esp + 4 - uc.reg_read(UC_X86_REG_EAX)) & 0xffffffff)
            uc.reg_write(UC_X86_REG_EIP, ret); return True
        if name in libcalls.CRT_MATH:            # cdecl double functions: doubles at [esp+4], [esp+12] -> ST0
            xs = [struct.unpack('<d', bytes(uc.mem_read(esp + 4 + 8 * i, 8)))[0] for i in range(libcalls.CRT_MATH[name])]
            uc.mem_write(FLD_VAL, struct.pack('<d', libcalls.crt_math(name, xs)))
            uc.reg_write(UC_X86_REG_EIP, FLD_STUB); return True     # fld qword [FLD_VAL]; ret (to `ret`)
        stub = libcalls.x87_stub(name, BUILTIN_SLOT)
        if stub is not None:
            at = BUILTIN_STUB + libcalls.STUB_SIZE * libcalls.STUB_SLOT[name]
            uc.mem_write(at, stub)
            uc.reg_write(UC_X86_REG_EIP, at); return True
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
            v = libcalls.run(name, self._bmem(uc, r), words)
        except KeyError:
            return False
        except libcalls.TooLong:
            self._fault = 'fault:libcall-length'; uc.emu_stop(); return True
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
            p = placement.get('sret', RESULT_BUF)
            self._ensure(p, conv.ret.size)          # a result buffer the function never touched is still a page
            return bytes(uc.mem_read(p, conv.ret.size)), None
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
