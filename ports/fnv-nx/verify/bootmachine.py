#!/usr/bin/env python3
"""The machine of the emulator boot stage: runs the guest ELF (elf32-littleaarch64 ILP32 PIE, crt0 "FNXG", entry(api)
with the FnvnxHostApi v3 table) under Unicorn, deterministically. No fault-tolerating options exist.
Library: Machine, VirtualClock, MachineError, ExtMissing.

Rules:
1. Layout only from bootlayout.plan(): PT_LOADs at plan base + vaddr with ELF protection (bss = fresh zero pages), stacks /
   host page / heap = the plan's regions. No relocation: the guest relocates itself. A segment outside the image region
   or sharing a page with another raises MachineError.
2. Totality: all 50 slots resolve to services.h_<slot> at construction, else MachineError.
3. One CPU. A thread switches only at a blocking host call and at a host call's return after `hc_slice` calls of its
   slice while another thread is ready: no wall clock, no instruction counts, so switch points repeat. Nothing ready:
   the virtual tick jumps to the earliest deadline; none: reason "deadlock" with a per-thread wait report.
4. Any invalid access (unmapped or protection violation), Unicorn error, or host call on an unmapped guest pointer
   ends the run, reason "fault", record {kind, addr, size, pc, lr, tid}. There is no continue path, except the thread's own
   FNVNX_PLAT_ACCESS_FAULT_HANDLER (5c).
5. map_fixed(addr, size, src) follows svcMapMemory (hostapi.h): src is a page-aligned block of the heap region, wholly
   inside it and not given away before; addr gets RW memory holding the CONTENT of src, and [src, src+size) becomes
   unmapped for good (a later access ends the run with "fault"). addr: exactly there, wholly inside a plan["free"]
   range other than "keys", no overlap with src; a range holding any mapping (image, heap, commit, section view, ...) is refused with
   the libnx result LibnxError_AlreadyInitialized 0xF59 first (hostapi.h: "any occupied range"); other refusals return libnx kernel results (InvalidSize 0xCA01, InvalidAddress 0xCC01, InvalidCurrentMemory
   0xD401) and are recorded in run()["events"]. ext_call(11, 8 VM_PROTECT, {addr,size,prot}) re-protects any pages the
   guest owns (heap region minus given-away blocks, map_fixed mappings; bit0 R, bit1 W, 0 none), else a refusal likewise
   (image, host page, stacks, given-away blocks, unmapped: 0xD401). Other modules go to services.ext_<module>
   (fn, args) found at construction; an unregistered module, or a handler raising ExtMissing, ends the run with reason
   "ext-module-missing": a configuration error, never answered with a value.
5b. Platform service (module 11, the header's FNVNX_PLAT_* list 1..8, 10..15 (9 DISPLAY_DOCKED is defined but the host serves nothing for it); always on). Totality at construction: every fn of PLATFORM_FNS
   resolves to services.plat_<name>(args) or the machine's own _plat_<name>(args), else MachineError (the machine serves
   thread_activity and vm_*; the services serve cpu_boost and message). A fn the header does not define is
   "ext-module-missing". VM service: reserve(hint, size) -> address or 0; size rounds up to 64 KiB, size 0 and every failure
   give 0. Without a hint the arena is the lowest 64 KiB multiple >= 0x10000 clear of the plan's regions, every plan["free"]
   range, map_fixed mappings and earlier reservations (one pass over the intervals sorted by lo: c only grows, and an
   interval that overlapped c is cleared by the move, an earlier one cannot overlap the new c because it ends below
   the old c + size or was cleared), end <= 4 GiB. A hint (rounded down to 64 KiB) is taken exactly or refused: it must not be 0, end
   above 4 GiB, or overlap anything listed above (the plan's free ranges are the module and key ranges: data, no fixed no-go). commit(addr, size): the page-rounded
   range must lie inside one reservation and size > 0 (else the libnx result LibnxError_BadInput 0x1759, checked FIRST; then a view page -> 0xD401); uncommitted pages become RW and zero, already committed pages keep their
   content and become RW. Each commit call maps its uncommitted gaps as extents (host_vm.inc). decommit follows host_vm.inc: an extent
   wholly inside the range is unmapped (an access ends the run with "fault"), a partly covered extent has the covered part zeroed and
   stays accessible RW. A reserved page never committed is unmapped (a reservation is address space only): an access faults. release(base): unmaps the reservation's committed pages and frees it (not a base: BadInput 0x1759). decommit has no size or reservation check (host_vm.inc): view page -> 0xD401 first, else the extents inside the range are handled, a range holding none is 0. Pages committed here are
   owned: VM_PROTECT works on them. map_fixed over a reservation is refused (0xD401). thread_activity(tid, paused): tid 0 (the calling thread) BadInput 0x1759; a paused thread is
   never picked (a pause of the running thread takes effect at once); unknown tid 0xE401, not started or ended 0xFA01.
5e. UNMAP_FIXED (fn 15, addr, size, src): the undo of exactly one earlier map_fixed. addr or src unaligned 0xCC01; size 0 / unaligned 0xCA01; (addr, size,
   src) not exactly a live map_fixed mapping 0xD401 (so a second unmap refuses). Success: the range is unmapped (an access faults), the src heap block
   is accessible RW again with the content the mapping had, and addr may be mapped again. A page of the range that is not RW is the header's
   violated precondition: MachineError, which ends the run as a host-call fault (the contract defines no result).
5d. Sections (module 11 fn 11..14, host_section.inc; errors are -(code) as u64, recorded as events section_*-refused with the
   positive code). A section is one anonymous mmap (zero, page aligned) kept alive for the machine's life; each view is
   uc.mem_map_ptr of that buffer at the view's address, so views share bytes. MAP checks in the host's order: size, alignment, id,
   range, view table, then placement: addr != 0 must lie inside one VM reservation (else 0xDA01) with no committed extent and no
   view (else 0xD401); addr == 0 takes the lowest free 64 KiB multiple clear of plan regions, free ranges, mappings,
   reservations and views (not a VM reservation). Views are RW guest memory but not guest-owned pages: VM_PROTECT / COMMIT /
   DECOMMIT of a range holding a view page and VM_RELEASE of a reservation holding one are refused 0xD401 (nothing done);
   map_fixed cannot land on one (refused by its own range rules). FREE kills the id at once, the pages live until the last view is
   unmapped. The fault-handler frame may live in a view: a view is writable guest memory (rule 5c asks only that).
5c. ACCESS_FAULT_HANDLER (module 11 fn 10, args {handler, stack_top}, result 0; handler 0 unregisters; stack_top must be 16-byte
   aligned, >= 816 and < 4 GiB, else 0xCC01 and an event). Per calling thread. A data or instruction access fault (Unicorn's
   UC_HOOK_MEM_INVALID: unmapped or protection, read/write/fetch) of a thread with a handler, not already inside it, is
   delivered at once: the frame (FnvnxFaultFrame, 816 bytes) is written at stack_top - 816 from the exact register state at the
   faulting instruction (pc = that instruction; pstate = NZCV only, bits 31..28), then x0 = frame, sp = frame, lr = a return stub
   on the host page, pc = handler. reason is 0 (unmapped) or 1 (protection); 2 (alignment) is never produced: Unicorn raises no
   alignment fault through the hook (an alignment trap ends the run as "uc-error"), so the machine cannot tell it. When the
   handler returns to the stub: x0 == 0 loads x0..x30, sp, pc, NZCV, fpcr, fpsr, v0..v31 from the frame in guest memory and
   resumes; x0 != 0 ends the run, reason "exit", exit_code x0. A fault of a thread inside its handler ends the run: reason
   "fault", record with in_handler. A frame range that is not wholly guest memory the guest can write (not host page) ends the
   run, reason "fault", kind "frame-unwritable". Each delivery is an event {kind "fault-handler", tid, access, reason, addr, pc}.
   Faults of host calls on bad guest pointers ("host-access") are not thread faults and are not delivered.
6. Bound and count: one UC_HOOK_BLOCK over the image region (installed only with limit_instructions or attach_icount)
   adds size/4 per block, feeds the Icount if attached, and stops at the first block boundary where the total >=
   limit_instructions: reason "limit", the same stop point on every run (the stopping block is counted).
7. Observation only: on_thread, watch_pc, watch_ret (same thread, sp and return address as at entry), watch_store,
   on_console. An exit code of the form 0x52000000 | fact_id (exit_code >> 24 == 0x52, nothing above) is a violated build
   invariant, not an ordinary exit: reason "reach-violation" with "fact_id" (exit_code stays the raw value).
   run() -> {reason, exit_code, host_calls, switches, threads, virtual_ns, events, console, fault|waits|ext};
   "console" is the whole text the guest wrote (decoded with errors="replace").
"""
import ctypes
import mmap
import struct

from unicorn import arm64_const

from unicorn import UC_ARCH_ARM64, UC_HOOK_BLOCK, UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_HOOK_MEM_WRITE, UC_MODE_ARM, UC_PROT_ALL, \
    UC_PROT_EXEC, UC_PROT_READ, UC_PROT_WRITE, Uc, UcError
from unicorn import unicorn_const as UCC
from unicorn.arm64_const import UC_ARM64_REG_CPACR_EL1, UC_ARM64_REG_FPCR, UC_ARM64_REG_FPSR, UC_ARM64_REG_LR, UC_ARM64_REG_NZCV, UC_ARM64_REG_PC, UC_ARM64_REG_SP, \
    UC_ARM64_REG_TPIDR_EL0, UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X4

import bootlayout
import icount

PAGE, U64 = 0x1000, (1 << 64) - 1
REACH_VIOLATION = 0x52   # top byte of a guest exit code 0x52000000 | fact_id: a build invariant was violated (fnvnx_reach_violation)
SLOTS = ["output_debug_string", "sleep_ns", "exit", "console_write", "console_flush", "main_loop",
         "buttons_down", "system_tick", "system_tick_freq",
         "thread_create", "thread_start", "thread_join", "thread_free", "thread_exit", "thread_yield",
         "thread_set_priority", "thread_set_cpu", "wait_on_address", "wake_address", "wall_time_us",
         "tz_offset_minutes", "random64", "memory_info", "cpu_count",
         "fs_open", "fs_read", "fs_write", "fs_pread", "fs_pwrite", "fs_seek", "fs_truncate", "fs_sync",
         "fs_fstat", "fs_close", "fs_stat", "fs_mkdir", "fs_rmdir", "fs_remove", "fs_rename", "fs_dir_open",
         "fs_dir_next", "fs_dir_close",
         "audio_open", "audio_submit", "audio_close", "pad_read", "pad_vibrate", "input_poll", "map_fixed",
         "ext_call"]
API_SIZE = 0x30 + 8 * len(SLOTS)
FNVNX_EXT_PLATFORM = 11
# hostapi.h FNVNX_PLAT_* of module 11 (the header defines 1..7; VM_PROTECT = 8 is the loader's addition): fn -> (name, min args)
FAULT_FRAME = 816
PLATFORM_FNS = {1: ("cpu_boost", 1), 2: ("thread_activity", 2), 3: ("message", 2), 4: ("vm_reserve", 2), 5: ("vm_commit", 2),
                6: ("vm_decommit", 2), 7: ("vm_release", 1), 8: ("vm_protect", 3),
                10: ("access_fault_handler", 2), 11: ("section_create", 1), 12: ("section_map", 4), 13: ("section_unmap", 2),
                14: ("section_free", 1), 15: ("unmap_fixed", 3)}
SEC_E_SIZE, SEC_E_ALIGN, SEC_E_RANGE, SEC_E_REGION, SEC_E_ID, SEC_E_NOMEM, SEC_E_FULL = 0xCA01, 0xCC01, 0xD401, 0xDA01, 0xE401, 0xD001, 0xCE01
SEC_MAX_SIZE, SEC_SECTIONS, SEC_VIEWS = 0x80000000, 128, 512   # host_section.inc
VM_ALIGN, VM_FLOOR = 0x10000, 0x10000   # host_vm.inc FNVNX_VM_ALIGN; the guest's low 64 KiB stay unmapped. Forbidden ranges are plan data.
# libnx result values (hostapi.h names them, the repo does not define them): MAKERESULT(Module_Libnx = 345, desc) = 345 | desc << 9,
# desc from libnx's result.h enum LibnxError (BadInput = 11, AlreadyInitialized = 7)
LIBNX_BAD_INPUT, LIBNX_ALREADY_INITIALIZED = 345 | 11 << 9, 345 | 7 << 9   # 0x1759, 0xF59
ERR_INVALID_SIZE, ERR_INVALID_ADDRESS, ERR_INVALID_CURRENT_MEMORY = 0xCA01, 0xCC01, 0xD401
ERR_INVALID_NEW_MEMORY_PERMISSION, ERR_INVALID_HANDLE, ERR_INVALID_STATE = 0xD801, 0xE401, 0xFA01
ACCESS_KINDS = {UCC.UC_MEM_READ_UNMAPPED: "read-unmapped", UCC.UC_MEM_WRITE_UNMAPPED: "write-unmapped",
                UCC.UC_MEM_FETCH_UNMAPPED: "fetch-unmapped", UCC.UC_MEM_WRITE_PROT: "write-protected",
                UCC.UC_MEM_READ_PROT: "read-protected", UCC.UC_MEM_FETCH_PROT: "fetch-protected"}
X_REGS = tuple(getattr(arm64_const, "UC_ARM64_REG_X%d" % i) for i in range(31))
V_REGS = tuple(getattr(arm64_const, "UC_ARM64_REG_V%d" % i) for i in range(32))
REGS = (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X4)


class MachineError(Exception):
    """a construction-time error: the plan, the ELF or the services do not fit the machine"""


class ExtMissing(Exception):
    """raised by an ext handler for a function it does not implement: ends the run as ext-module-missing"""

    def __init__(self, module, fn):
        super().__init__("ext module %d fn %d is not registered" % (module, fn))
        self.module, self.fn = module, fn


class VirtualClock:
    """the stage's clock: 19.2 MHz tick, +1000 per system_tick read; the one place that defines time"""
    FREQ, STEP = 19_200_000, 1000

    def __init__(self, start=1_000_000):
        self.tick = start

    def read(self):
        self.tick += self.STEP
        return self.tick

    def now_ns(self):
        return self.tick * 1_000_000_000 // self.FREQ

    def jump_to_ns(self, when):
        self.tick = max(self.tick, when * self.FREQ // 1_000_000_000 + 1)


class Thread:
    def __init__(self, tid):
        self.tid, self.ctx, self.state, self.wait, self.result, self.resume_pc = tid, None, "new", None, 0, None
        self.paused = False
        self.fh, self.in_handler, self.frame = None, False, 0   # fault handler (handler, stack_top); inside it; its frame address
        self.entry = self.arg = 0   # new: new, ready, running, blocked, done; wait: ("addr", a, dl) / ("join", t) / ("sleep", dl)


def _up(v, a=PAGE):
    return (v + a - 1) // a * a


class Machine:
    def __init__(self, elf_bytes, plan, services, clock=None, *, hc_slice=2000, limit_host_calls=None, n_stacks=32,
                 stack_size=0x40000, limit_instructions=None):
        self.elf = elf = bootlayout.parse_elf(elf_bytes)
        self.services, self.clock, self.hc_slice, self.limit = services, clock or VirtualClock(), hc_slice, limit_host_calls
        reg = {n: (r["lo"], r["hi"]) for n, r in plan["regions"].items()}
        base, (ilo, ihi) = plan["image"]["base"], reg["image"]
        self.image, self.heap, self.given = (ilo, ihi), reg["heap"], []   # given: heap blocks consumed by map_fixed
        self.base, self.entry = base, plan["image"]["entry"]
        self.free = [(f["name"], f["lo"], f["hi"]) for f in plan["free"]]
        slo, shi = reg["stacks"]
        if shi - slo != n_stacks * stack_size:
            raise MachineError("stacks region 0x%x..0x%x is not %d x 0x%x" % (slo, shi, n_stacks, stack_size))
        self.stacks_top, self.stack_size, self.max_threads = shi, stack_size, n_stacks
        self.handlers = self._resolve(services)
        self.plat = self._resolve_platform(services)
        self.regions = sorted(reg.values())
        self.sections, self.views, self.sec_next = {}, {}, 1   # id -> Section dict; view addr -> view dict; next id (never reused)
        self.vm_resv, self.vm_pages, self.vm_ext = {}, set(), []   # base -> size; committed page addresses; extents [lo, hi): one per commit gap (host_vm.inc)
        self.ext = {int(n[4:]): getattr(services, n) for n in dir(services) if n.startswith("ext_") and n[4:].isdigit()}
        uc = self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)   # FP/SIMD on
        used = []
        for s in elf["loads"]:
            lo, hi = (base + s["vaddr"]) // PAGE * PAGE, _up(base + s["vaddr"] + s["memsz"])
            if s["memsz"] == 0:
                continue
            if lo < ilo or hi > ihi or any(lo < b and a < hi for a, b in used):
                raise MachineError("PT_LOAD 0x%x..0x%x is outside the image region or shares a page" % (lo, hi))
            used.append((lo, hi))
            prot = (UC_PROT_READ if s["flags"] & 4 else 0) | (UC_PROT_WRITE if s["flags"] & 2 else 0) | \
                   (UC_PROT_EXEC if s["flags"] & 1 else 0)
            uc.mem_map(lo, hi - lo, UC_PROT_ALL)
            uc.mem_write(base + s["vaddr"], bytes(elf_bytes[s["offset"]:s["offset"] + s["filesz"]]))
            uc.mem_protect(lo, hi - lo, prot)
        for n in ("stacks", "heap"):
            uc.mem_map(reg[n][0], reg[n][1] - reg[n][0], UC_PROT_READ | UC_PROT_WRITE)
        self.host_page = hp = reg["host_page"][0]
        uc.mem_map(hp, PAGE, UC_PROT_ALL)
        self.stub_base, self.return_addr, self.thread_end = hp + 0x800, hp + 0xFF0, hp + 0xFF8
        self.fault_ret, self.pending = hp + 0xFE0, None
        self.stubs = {self.stub_base + 4 * i: n for i, n in enumerate(SLOTS)}
        api = struct.pack("<IIII", 0x48584E46, 3, API_SIZE, 0)
        api += struct.pack("<QQQQ", ilo, ihi - ilo, reg["heap"][0], reg["heap"][1] - reg["heap"][0])
        api += b"".join(struct.pack("<Q", a) for a in self.stubs)
        assert len(api) == API_SIZE
        uc.mem_write(hp, api)
        uc.mem_write(self.stub_base, struct.pack("<I", 0xD65F03C0) * len(SLOTS))   # ret
        self.addr_of = {n: a for a, n in self.stubs.items()}
        self.threads, self.order, self.cur, self.last = {}, [], None, None
        self.calls, self.switches, self.slice_calls0, self.exit_code = 0, 0, 0, None
        self.fault, self.events, self.ext_missing, self.limit_hit = None, [], None, False
        self.mapped = []   # merged [lo, hi) ranges mapped by map_fixed
        self.mf_live = {}   # addr -> (size, src) of every live map_fixed mapping (the kernel's own record, UNMAP_FIXED checks against it)
        self.cb_thread, self.cb_console, self.line = None, None, bytearray()
        self.ret_pending, self.console_text = {}, []
        self.limit_instr, self.instr, self.icount, self.block_hook = limit_instructions, 0, None, False
        if limit_instructions is not None:
            if limit_instructions <= 0:
                raise MachineError("limit_instructions must be positive, got %r" % (limit_instructions,))
            self._block_hook()
        uc.hook_add(UC_HOOK_CODE, self._on_code, begin=hp, end=hp + PAGE - 1)
        uc.hook_add(UC_HOOK_MEM_INVALID, self._on_bad)
        services.bind(self)

    @staticmethod
    def _resolve(services):
        h = {}
        for n in SLOTS:
            f = getattr(services, "h_" + n, None)
            if not callable(f):
                raise MachineError("services has no h_%s: all %d host slots must be implemented" % (n, len(SLOTS)))
            h[n] = f
        if not callable(getattr(services, "bind", None)):
            raise MachineError("services has no bind(machine)")
        return h

    def _resolve_platform(self, services):
        h = {}
        for fn, (name, _n) in sorted(PLATFORM_FNS.items()):
            f = getattr(services, "plat_" + name, None) or getattr(self, "_plat_" + name, None)
            if not callable(f):
                raise MachineError("module 11 fn %d (%s) is defined by the header but neither the machine nor services has "
                                   "plat_%s" % (fn, name, name))
            h[fn] = f
        return h

    # -- instruction bound and count
    def _block_hook(self):
        if not self.block_hook:
            self.block_hook = True
            self.uc.hook_add(UC_HOOK_BLOCK, self._on_block, begin=self.image[0], end=self.image[1] - 1)

    def attach_icount(self, spec=None, bias=0):
        """the Icount of this machine (icount.py, fed by the machine's one block hook); returns it"""
        if self.icount is not None:
            raise MachineError("attach_icount called twice")
        self.icount = ic = icount.Icount(spec, bias)
        ic.uc = self.uc
        self._block_hook()
        return ic

    def _on_block(self, uc, addr, size, _):
        if self.icount is not None:
            self.icount._block(uc, addr, size, _)
        self.instr += size // 4
        if self.limit_instr is not None and self.instr >= self.limit_instr and not self.limit_hit:
            self.limit_hit = True
            uc.emu_stop()

    # -- observation
    def on_thread(self, cb):
        self.cb_thread = cb

    def on_console(self, cb):
        self.cb_console = cb

    def _hook_at(self, addr, cb):
        """a code hook at one address, also when added while running (a block translated earlier is dropped)"""
        self.uc.hook_add(UC_HOOK_CODE, cb, begin=addr, end=addr)
        try:
            self.uc.ctl_remove_cache(addr, addr + 4)
        except (AttributeError, UcError):
            pass

    def watch_pc(self, addr, cb):
        """cb(tid, pc) each time the instruction at addr is about to execute"""
        self._hook_at(addr, lambda uc, a, s, _: cb(self.tid(), a))

    def watch_ret(self, addr, cb):
        """cb(tid, x0) when a call entered at addr returns to its caller: same thread, sp equal to the sp at entry,
        pc equal to lr at entry (recursive and other threads' returns do not count)"""
        def entered(uc, a, s, _):
            ret, sp = uc.reg_read(UC_ARM64_REG_LR) & 0xFFFFFFFF, uc.reg_read(UC_ARM64_REG_SP)
            if ret not in self.ret_pending:
                self.ret_pending[ret] = []
                self._hook_at(ret, lambda u, a2, s2, _r, ret=ret: self._returned(ret))
            self.ret_pending[ret].append((cb, sp, self.tid()))
        self._hook_at(addr, entered)

    def _returned(self, ret):
        sp, tid = self.uc.reg_read(UC_ARM64_REG_SP), self.tid()
        for e in list(self.ret_pending[ret]):
            if e[1] == sp and e[2] == tid:
                self.ret_pending[ret].remove(e)
                e[0](tid, self.uc.reg_read(UC_ARM64_REG_X0))

    def watch_store(self, addr, cb, size=4):
        """cb(tid, store_addr, store_size, value) for every store overlapping [addr, addr+size)"""
        def hit(uc, access, a, s, value, _):
            if a < addr + size and addr < a + s:
                cb(self.tid(), a, s, value & ((1 << 8 * s) - 1))
        self.uc.hook_add(UC_HOOK_MEM_WRITE, hit, begin=max(addr - 31, 0), end=addr + size - 1)

    def tid(self):
        return self.cur.tid if self.cur else 0

    def console_write(self, data):
        self.console_text.append(bytes(data))
        self.line += data
        while b"\n" in self.line:
            line, _, rest = bytes(self.line).partition(b"\n")
            self.line = bytearray(rest)
            if self.cb_console:
                self.cb_console(line.decode(errors="replace"))
        return len(data)

    # -- guest memory services
    def stack_for(self, tid):
        return self.stacks_top - self.stack_size * (tid - 1) - 0x100

    def _event(self, kind, **kw):
        self.events.append(dict(kind=kind, tid=self.tid(), **kw))

    def _covered(self, lo, hi):
        return any(a <= lo and hi <= b for a, b in self.mapped)

    def _owned(self, lo, hi):
        """wholly guest-owned: inside the heap region and clear of given-away blocks, or inside map_fixed mappings"""
        if self.heap[0] <= lo and hi <= self.heap[1]:
            return not any(a < hi and lo < b for a, b in self.given)
        return self._covered(lo, hi) or self._vm_committed(lo, hi)

    def _vm_committed(self, lo, hi):
        return lo in self.vm_pages and all(p in self.vm_pages for p in range(lo, hi, PAGE))

    def map_fixed(self, addr, size, src):
        if size == 0 or size % PAGE:
            return self._refuse("map_fixed", addr, size, "size", ERR_INVALID_SIZE)
        if addr % PAGE or addr + size > 1 << 32:
            return self._refuse("map_fixed", addr, size, "address", ERR_INVALID_ADDRESS)
        if any(b > addr and addr + size > a for a, b, _p in self.uc.mem_regions()):   # any occupied range, a section view included
            return self._refuse("map_fixed", addr, size, "range is occupied", LIBNX_ALREADY_INITIALIZED)
        if any(addr < r + n and r < addr + size for r, n in self.vm_resv.items()):
            return self._refuse("map_fixed", addr, size, "overlaps a VM reservation", ERR_INVALID_CURRENT_MEMORY)
        if not any(n != "keys" and lo <= addr and addr + size <= hi for n, lo, hi in self.free):
            return self._refuse("map_fixed", addr, size, "not wholly inside a mappable reserved range", ERR_INVALID_ADDRESS)
        if src % PAGE or not (self.heap[0] <= src and src + size <= self.heap[1]):
            return self._refuse("map_fixed", addr, size, "src not a page-aligned block wholly inside the heap region 0x%x" % src,
                                ERR_INVALID_ADDRESS)
        if any(a < src + size and src < b for a, b in self.given) or (src < addr + size and addr < src + size):
            return self._refuse("map_fixed", addr, size, "src block 0x%x already given away or overlaps addr" % src,
                                ERR_INVALID_CURRENT_MEMORY)
        content = bytes(self.uc.mem_read(src, size))
        self.uc.mem_unmap(src, size)
        self.given.append((src, src + size))
        self.uc.mem_map(addr, size, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_write(addr, content)
        self.mf_live[addr] = (size, src)
        self._merge_mapped()
        return 0

    def _merge_mapped(self):
        spans = sorted((a, a + n) for a, (n, _s) in self.mf_live.items())
        self.mapped = spans[:1]
        for a, b in spans[1:]:
            if a <= self.mapped[-1][1]:
                self.mapped[-1] = (self.mapped[-1][0], max(b, self.mapped[-1][1]))
            else:
                self.mapped.append((a, b))

    def _plat_unmap_fixed(self, a):
        """FNVNX_PLAT_UNMAP_FIXED (addr, size, src): the undo of exactly one earlier map_fixed (hostapi.h 15)"""
        addr, size, src = a[:3]
        if (addr | src) % PAGE:
            return self._refuse("unmap_fixed", addr, size, "addr or src not page aligned", ERR_INVALID_ADDRESS)
        if size == 0 or size % PAGE:
            return self._refuse("unmap_fixed", addr, size, "size", ERR_INVALID_SIZE)
        if self.mf_live.get(addr) != (size, src):
            return self._refuse("unmap_fixed", addr, size, "not the mapping of that src (kernel: InvalidCurrentMemory)",
                                ERR_INVALID_CURRENT_MEMORY)
        perms = {}
        for lo, hi, p in self.uc.mem_regions():
            for pg in range(max(lo, addr), min(hi + 1, addr + size), PAGE):
                perms[pg] = p
        if any(perms.get(pg) != (UC_PROT_READ | UC_PROT_WRITE) for pg in range(addr, addr + size, PAGE)):
            # the header's precondition ("every page of the range is read-write") is violated: the contract defines no result
            raise MachineError("unmap_fixed 0x%x+0x%x: precondition violated, a page is not read-write" % (addr, size))
        content = bytes(self.uc.mem_read(addr, size))
        self.uc.mem_unmap(addr, size)
        del self.mf_live[addr]
        self.given.remove((src, src + size))
        self.uc.mem_map(src, size, UC_PROT_READ | UC_PROT_WRITE)   # the heap block is ordinary accessible heap again
        self.uc.mem_write(src, content)
        self._merge_mapped()
        return 0

    def _refuse(self, what, addr, size, why, code):
        self._event(what + "-refused", addr=addr, size=size, why=why, result=code)
        return code

    def ext_call(self, module, fn, args, nargs):
        a = [struct.unpack("<Q", self.uc.mem_read(args + 8 * i, 8))[0] for i in range(nargs)] if nargs else []
        if module == FNVNX_EXT_PLATFORM and fn in PLATFORM_FNS:
            name, need = PLATFORM_FNS[fn]
            if len(a) < need:
                raise MachineError("module 11 fn %d (%s) needs %d arguments, the guest passed %d" % (fn, name, need, len(a)))
            return self.plat[fn](a)
        if module not in self.ext:
            raise ExtMissing(module, fn)
        return self.ext[module](fn, a)

    # -- platform service, module 11 (rule 5b)
    def _vm_blocked(self):
        """sorted (lo, hi) of everything a reservation must not overlap"""
        return sorted(self.regions + [(lo, hi) for _n, lo, hi in self.free] + self.mapped
                      + [(b, b + n) for b, n in self.vm_resv.items()] + [(a, a + v["size"]) for a, v in self.views.items()])

    def _find_free(self, size):
        """lowest 64 KiB multiple >= VM_FLOOR whose [c, c + size) is clear of _vm_blocked(), end <= 4 GiB; None if none"""
        lo = VM_FLOOR
        for x, b in self._vm_blocked():
            if lo < b and x < lo + size:
                lo = _up(b, VM_ALIGN)
        return lo if lo + size <= 1 << 32 else None

    def _view_overlap(self, lo, hi):
        return any(a < hi and lo < a + v["size"] for a, v in self.views.items())

    def _vm_resv_of(self, lo, hi):
        return next((b for b, n in self.vm_resv.items() if b <= lo and hi <= b + n), None)

    def _plat_vm_reserve(self, a):
        hint, want = a[0], a[1]
        if want == 0:
            return self._refuse("vm_reserve", hint, want, "size 0", 0)
        size = _up(want, VM_ALIGN)
        blocked = self._vm_blocked()
        if hint:
            lo = hint & ~(VM_ALIGN - 1)
            if lo == 0 or lo + size > 1 << 32:
                return self._refuse("vm_reserve", hint, want, "address", 0)
            if any(b > lo and lo + size > x for x, b in blocked):
                return self._refuse("vm_reserve", hint, want, "overlaps a mapping, a free range or a reservation", 0)
        else:
            lo = self._find_free(size)
            if lo is None:
                return self._refuse("vm_reserve", hint, want, "no address space", 0)
        self.vm_resv[lo] = size
        return lo

    def _vm_range(self, what, a):
        """commit's checks in host_vm_commit's order: size 0 or not inside one reservation (libnx BadInput) first, then a section view"""
        addr, size = a[0], a[1]
        lo, hi = addr // PAGE * PAGE, _up(addr + size)
        if size == 0 or addr + size >= 1 << 64 or self._vm_resv_of(lo, hi) is None:
            return None, self._refuse(what, addr, size, "size 0 or not inside one reservation", LIBNX_BAD_INPUT)
        if self._view_overlap(lo, hi):
            return None, self._refuse(what, addr, size, "range holds a page of a section view", ERR_INVALID_CURRENT_MEMORY)
        return (lo, hi), 0

    def _vm_runs(self, lo, hi, committed):
        """maximal runs [x, y) of pages of [lo, hi) that are (not) committed"""
        runs, p = [], lo
        while p < hi:
            if (p in self.vm_pages) == committed:
                q = p
                while q < hi and (q in self.vm_pages) == committed:
                    q += PAGE
                runs.append((p, q))
                p = q
            else:
                p += PAGE
        return runs

    def _plat_vm_commit(self, a):
        rng, res = self._vm_range("vm_commit", a)
        if rng is None:
            return res
        lo, hi = rng
        for x, y in self._vm_runs(lo, hi, True):
            self.uc.mem_protect(x, y - x, UC_PROT_READ | UC_PROT_WRITE)
        for x, y in self._vm_runs(lo, hi, False):
            self.uc.mem_map(x, y - x, UC_PROT_READ | UC_PROT_WRITE)
            self.vm_pages.update(range(x, y, PAGE))
            self.vm_ext.append([x, y])
        return 0

    def _vm_decommit(self, lo, hi):
        """host_vm.inc: an extent wholly inside [lo, hi) is unmapped (an access faults), a partly covered one has the covered part
        zeroed and stays accessible RW"""
        keep = []
        for e in self.vm_ext:
            x, y = e
            if y <= lo or x >= hi:
                keep.append(e)
            elif lo <= x and y <= hi:
                self.uc.mem_unmap(x, y - x)
                self.vm_pages.difference_update(range(x, y, PAGE))
            else:
                s, t = max(x, lo), min(y, hi)
                self.uc.mem_protect(s, t - s, UC_PROT_READ | UC_PROT_WRITE)
                self.uc.mem_write(s, bytes(t - s))
                keep.append(e)
        self.vm_ext = keep

    def _plat_vm_decommit(self, a):
        # host_vm_decommit has no reservation or size check: the view check is first, then the extents inside the range are handled
        addr, size = a[0], a[1]
        if addr + size >= 1 << 64:
            raise MachineError("vm_decommit 0x%x+0x%x wraps the address space: the contract defines no result" % (addr, size))
        lo, hi = addr // PAGE * PAGE, _up(addr + size)
        if hi <= lo:
            return 0
        if self._view_overlap(lo, hi):
            return self._refuse("vm_decommit", addr, size, "range holds a page of a section view", ERR_INVALID_CURRENT_MEMORY)
        self._vm_decommit(lo, hi)
        return 0

    def _plat_vm_release(self, a):
        base = a[0]
        if base not in self.vm_resv:
            return self._refuse("vm_release", base, 0, "not the base of a reservation", LIBNX_BAD_INPUT)
        if self._view_overlap(base, base + self.vm_resv[base]):
            return self._refuse("vm_release", base, 0, "reservation holds a section view", ERR_INVALID_CURRENT_MEMORY)
        self._vm_decommit(base, base + self.vm_resv.pop(base))
        return 0

    def _plat_vm_protect(self, a):
        addr, size, prot = a[:3]
        if size == 0 or size % PAGE:
            return self._refuse("vm_protect", addr, size, "size", ERR_INVALID_SIZE)
        if addr % PAGE or addr + size > 1 << 32:
            return self._refuse("vm_protect", addr, size, "address", ERR_INVALID_ADDRESS)
        if prot & ~3:
            return self._refuse("vm_protect", addr, size, "prot 0x%x has bits other than R and W" % prot,
                                ERR_INVALID_NEW_MEMORY_PERMISSION)
        if self._view_overlap(addr, addr + size):
            return self._refuse("vm_protect", addr, size, "pages of a section view (not guest-owned pages)", ERR_INVALID_CURRENT_MEMORY)
        if not self._owned(addr, addr + size):
            return self._refuse("vm_protect", addr, size, "pages the guest does not own", ERR_INVALID_CURRENT_MEMORY)
        self.uc.mem_protect(addr, size, (UC_PROT_READ if prot & 1 else 0) | (UC_PROT_WRITE if prot & 2 else 0))
        return 0

    # -- memory sections, module 11 fn 11..14 (rule 5d)
    def _sec_refuse(self, what, addr, size, why, code):
        self._refuse(what, addr, size, why, code)
        return -code & U64

    def _plat_section_create(self, a):
        size = a[0]
        if not size or size % PAGE or size > SEC_MAX_SIZE:
            return self._sec_refuse("section_create", 0, size, "size", SEC_E_SIZE)
        if len(self.sections) >= SEC_SECTIONS:
            return self._sec_refuse("section_create", 0, size, "table full", SEC_E_FULL)
        if self.sec_next > 0x7FFFFFFF:
            return self._sec_refuse("section_create", 0, size, "ids exhausted", SEC_E_NOMEM)
        sid, self.sec_next = self.sec_next, self.sec_next + 1
        mm = mmap.mmap(-1, size)   # anonymous: zero, page aligned; mem_map_ptr needs it alive and at a fixed address
        buf = ctypes.c_char.from_buffer(mm)
        self.sections[sid] = dict(mm=mm, buf=buf, ptr=ctypes.addressof(buf), size=size, live=True, views=0)
        return sid

    def _plat_section_map(self, a):
        sid, addr, off, size = a[:4]
        if not size or size % PAGE or size > SEC_MAX_SIZE:
            return self._sec_refuse("section_map", addr, size, "size", SEC_E_SIZE)
        if (addr | off) % PAGE:
            return self._sec_refuse("section_map", addr, size, "addr or offset not page aligned", SEC_E_ALIGN)
        sec = self.sections.get(sid)
        if sec is None or not sec["live"]:
            return self._sec_refuse("section_map", addr, size, "unknown id %d" % sid, SEC_E_ID)
        if off + size > sec["size"]:
            return self._sec_refuse("section_map", addr, size, "range outside the section", SEC_E_RANGE)
        if len(self.views) >= SEC_VIEWS:
            return self._sec_refuse("section_map", addr, size, "view table full", SEC_E_FULL)
        if addr:
            if self._vm_resv_of(addr, addr + size) is None:
                return self._sec_refuse("section_map", addr, size, "not inside one VM reservation", SEC_E_REGION)
            if self._view_overlap(addr, addr + size) or any(x < addr + size and addr < y for x, y in self.vm_ext):
                return self._sec_refuse("section_map", addr, size, "range not free: committed pages or another view", SEC_E_RANGE)
            own = False
        else:
            addr = self._find_free(size)
            if addr is None:
                return self._sec_refuse("section_map", 0, size, "no address space", SEC_E_NOMEM)
            own = True
        self.uc.mem_map_ptr(addr, size, UC_PROT_READ | UC_PROT_WRITE, sec["ptr"] + off)
        self.views[addr] = dict(size=size, off=off, sid=sid, own=own)
        sec["views"] += 1
        return addr

    def _sec_release(self, sid):
        sec = self.sections.pop(sid)
        del sec["buf"]   # the exported pointer first, then the mapping can close (no view of it is mapped any more)
        sec["mm"].close()

    def _plat_section_unmap(self, a):
        addr, size = a[:2]
        v = self.views.get(addr)
        if v is None or v["size"] != size:
            return self._sec_refuse("section_unmap", addr, size, "not exactly a view", SEC_E_RANGE)
        self.uc.mem_unmap(addr, size)
        del self.views[addr]
        sec = self.sections[v["sid"]]
        sec["views"] -= 1
        if not sec["live"] and not sec["views"]:
            self._sec_release(v["sid"])
        return 0

    def _plat_section_free(self, a):
        sec = self.sections.get(a[0])
        if sec is None or not sec["live"]:
            return self._sec_refuse("section_free", 0, a[0], "unknown id", SEC_E_ID)
        sec["live"] = False   # the id is dead now; with views the pages stay until the last one is unmapped
        if not sec["views"]:
            self._sec_release(a[0])
        return 0

    def _plat_access_fault_handler(self, a):
        handler, top = a[:2]
        t = self.cur
        if handler == 0:
            t.fh = None
            return 0
        if top % 16 or top < FAULT_FRAME or top >= 1 << 32:
            return self._refuse("access_fault_handler", handler, top, "stack_top not 16-byte aligned or outside 816..4 GiB (exclusive)",
                                ERR_INVALID_ADDRESS)
        t.fh = (handler, top)
        return 0

    def _writable(self, lo, hi):
        """[lo, hi) is wholly mapped writable guest memory, not the host page"""
        if lo < self.host_page + PAGE and self.host_page < hi:
            return False
        while lo < hi:
            r = next(((b, e) for b, e, pr in self.uc.mem_regions() if b <= lo <= e and pr & UC_PROT_WRITE), None)
            if r is None:
                return False
            lo = r[1] + 1
        return True

    def _deliver_fault(self, t):
        """rule 5c: build the frame from the thread's registers at the faulting instruction and enter its handler"""
        uc, (access, addr, size) = self.uc, self.pending
        self.pending = None
        handler, top = t.fh
        pc, fa = uc.reg_read(UC_ARM64_REG_PC), top - FAULT_FRAME
        kind = 1 if access in (UCC.UC_MEM_WRITE_UNMAPPED, UCC.UC_MEM_WRITE_PROT) else 2 if access in (
            UCC.UC_MEM_FETCH_UNMAPPED, UCC.UC_MEM_FETCH_PROT) else 0
        reason = 0 if access in (UCC.UC_MEM_READ_UNMAPPED, UCC.UC_MEM_WRITE_UNMAPPED, UCC.UC_MEM_FETCH_UNMAPPED) else 1
        if not self._writable(fa, top):
            self.fault = dict(kind="frame-unwritable", addr=fa, size=FAULT_FRAME, pc=pc, lr=uc.reg_read(UC_ARM64_REG_LR), tid=t.tid)
            return
        f = struct.pack("<IIQQQQ", kind, reason, pc if kind == 2 else addr, pc, uc.reg_read(UC_ARM64_REG_SP),
                        uc.reg_read(UC_ARM64_REG_NZCV) & 0xF0000000)
        f += b"".join(struct.pack("<Q", uc.reg_read(r)) for r in X_REGS)
        f += struct.pack("<II", uc.reg_read(UC_ARM64_REG_FPCR), uc.reg_read(UC_ARM64_REG_FPSR))
        f += b"".join(struct.pack("<QQ", v & U64, v >> 64) for v in (uc.reg_read(r) for r in V_REGS))
        uc.mem_write(fa, f.ljust(FAULT_FRAME, b"\0"))
        self._event("fault-handler", access=ACCESS_KINDS.get(access), reason=reason, addr=addr, pc=pc)
        uc.reg_write(UC_ARM64_REG_X0, fa)
        uc.reg_write(UC_ARM64_REG_SP, fa & ~15)
        uc.reg_write(UC_ARM64_REG_LR, self.fault_ret)
        t.ctx, t.resume_pc, t.result, t.state = uc.context_save(), handler, fa, "ready"
        t.in_handler, t.frame = True, fa

    def _fault_return(self):
        """the handler returned to the stub: 0 resumes from the frame, else the process ends with that value"""
        uc, t = self.uc, self.cur
        rv = uc.reg_read(UC_ARM64_REG_X0)
        if rv:
            self.exit(rv)
            return
        f = bytes(uc.mem_read(t.frame, FAULT_FRAME))
        _k, _r, _a, pc, sp, pstate = struct.unpack_from("<IIQQQQ", f, 0)
        for i, r in enumerate(X_REGS):
            uc.reg_write(r, struct.unpack_from("<Q", f, 40 + 8 * i)[0])
        fpcr, fpsr = struct.unpack_from("<II", f, 288)
        uc.reg_write(UC_ARM64_REG_FPCR, fpcr)
        uc.reg_write(UC_ARM64_REG_FPSR, fpsr)
        for i, r in enumerate(V_REGS):
            lo, hi = struct.unpack_from("<QQ", f, 296 + 16 * i)
            uc.reg_write(r, lo | hi << 64)
        uc.reg_write(UC_ARM64_REG_NZCV, pstate & 0xF0000000)
        uc.reg_write(UC_ARM64_REG_SP, sp)
        uc.reg_write(UC_ARM64_REG_PC, pc)
        t.in_handler = False

    def _plat_thread_activity(self, a):
        tid, paused = a[0], a[1]
        if tid == 0:   # the calling thread: the kernel cannot pause it (host_thread_activity.inc)
            return self._refuse("thread_activity", tid, paused, "the calling thread", LIBNX_BAD_INPUT)
        t = self.threads.get(tid)
        if t is None:
            return self._refuse("thread_activity", tid, paused, "no such thread", ERR_INVALID_HANDLE)
        if t.state in ("new", "done"):
            return self._refuse("thread_activity", tid, paused, "thread is %s" % t.state, ERR_INVALID_STATE)
        t.paused = bool(paused)
        return (("yield",), 0) if t is self.cur and t.paused else 0

    # -- threads and blocking
    def thread_create(self, entry, arg):
        tid = max(self.threads) + 1
        if tid > self.max_threads:
            return 0
        t = self.threads[tid] = Thread(tid)
        t.entry, t.arg = entry, arg
        self.order.append(tid)
        return tid

    def thread_start(self, tid):
        t, uc = self.threads.get(tid), self.uc
        if not t or t.state != "new":
            return 1
        arg = t.arg
        regs = uc.context_save()
        uc.reg_write(UC_ARM64_REG_X0, arg)
        uc.reg_write(UC_ARM64_REG_LR, self.thread_end)
        uc.reg_write(UC_ARM64_REG_SP, self.stack_for(tid))
        uc.reg_write(UC_ARM64_REG_TPIDR_EL0, 0)
        t.ctx = uc.context_save()
        t.resume_pc, t.result = t.entry, arg
        uc.context_restore(regs)
        t.state = "ready"
        return 0

    def exit(self, code):
        self.exit_code = code
        self.cur.state = "done"
        self.uc.emu_stop()

    def _block(self, wait, result):
        t = self.cur
        t.resume_pc, t.result, t.ctx = self.uc.reg_read(UC_ARM64_REG_LR), result, self.uc.context_save()
        if wait[0] == "done":
            t.state = "done"
            for o in self.threads.values():
                if o.state == "blocked" and o.wait == ("join", t.tid):
                    o.state, o.result = "ready", 0
        elif wait[0] == "yield":
            t.state = "ready"
        else:
            t.state, t.wait = "blocked", wait
        self.cur = None
        self.uc.emu_stop()

    def wake(self, addr, count):
        n = 0
        for tid in self.order:
            o = self.threads[tid]
            if o.state == "blocked" and o.wait[0] == "addr" and o.wait[1] == addr:
                if count != U64 and n >= count:
                    break
                o.state, o.result = "ready", 0
                n += 1

    def _pick(self):
        live = [self.threads[t] for t in self.order]
        for _ in range(2):
            start = (self.order.index(self.last) + 1) if self.last in self.order else 0
            for i in range(len(live)):
                t = live[(start + i) % len(live)]
                if t.state == "ready" and not t.paused:
                    return t
            dl = [(t.wait[-1], t) for t in live if t.state == "blocked" and t.wait[0] in ("sleep", "addr")
                  and t.wait[-1] is not None]
            if not dl:
                return None
            when = min(d for d, _ in dl)
            self.clock.jump_to_ns(when)
            for d, t in dl:
                if d <= when:
                    t.state, t.result = "ready", 1 if t.wait[0] == "addr" else 0
        return None

    # -- host-call dispatch
    def _on_bad(self, uc, access, addr, size, value, _):
        t = self.cur
        if self.fault is None and t is not None and t.fh and not t.in_handler:
            self.pending = (access, addr, size)   # delivered by run() once Unicorn has stopped with the error
            return False
        if self.fault is None:
            self.fault = dict(kind=ACCESS_KINDS.get(access, "access-%d" % access), addr=addr, size=size,
                              pc=uc.reg_read(UC_ARM64_REG_PC), lr=uc.reg_read(UC_ARM64_REG_LR), tid=self.tid(),
                              **({"in_handler": True} if t is not None and t.in_handler else {}))
        return False

    def _stop_fault(self, kind, addr=None, size=None):
        if self.fault is None:
            self.fault = dict(kind=kind, addr=addr, size=size, pc=self.uc.reg_read(UC_ARM64_REG_PC),
                              lr=self.uc.reg_read(UC_ARM64_REG_LR), tid=self.tid())
        self.uc.emu_stop()

    def _on_code(self, uc, addr, size, _):
        if addr == self.fault_ret:
            try:
                self._fault_return()
            except UcError as e:
                self._stop_fault("host-access")
            return
        if addr == self.thread_end:
            self._block(("done",), 0)
            return
        name = self.stubs.get(addr)
        if name is None:
            return
        if self.limit is not None and self.calls >= self.limit:
            self.limit_hit = True
            uc.emu_stop()
            return
        self.calls += 1
        try:
            res = self.handlers[name](*[uc.reg_read(r) for r in REGS])
        except ExtMissing as e:
            self.ext_missing = (e.module, e.fn)
            uc.emu_stop()
            return
        except (UcError, MachineError) as e:
            self._stop_fault("host-access" if isinstance(e, UcError) else "host-call: %s" % e)
            return
        if isinstance(res, tuple):          # blocking call: (wait spec, result when resumed)
            self._block(res[0], res[1])
            return
        uc.reg_write(UC_ARM64_REG_X0, res & U64)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))
        if self.exit_code is None and self.calls - self.slice_calls0 >= self.hc_slice \
                and any(x is not self.cur and x.state == "ready" and not x.paused for x in self.threads.values()):
            self._block(("yield",), res & U64)

    def _waits(self):
        return [dict(tid=t.tid, state=t.state, wait=list(t.wait) if t.wait else None, **({"paused": True} if t.paused else {}))
                for t in sorted(self.threads.values(), key=lambda x: x.tid) if t.state != "done"]

    def run(self):
        uc = self.uc
        main = self.threads[1] = Thread(1)
        self.order, self.last = [1], None
        uc.reg_write(UC_ARM64_REG_SP, self.stack_for(1))
        uc.reg_write(UC_ARM64_REG_X0, self.host_page)
        uc.reg_write(UC_ARM64_REG_LR, self.return_addr)
        uc.reg_write(UC_ARM64_REG_TPIDR_EL0, 0xDEAD0000)
        main.ctx, main.resume_pc, main.state, main.result = uc.context_save(), self.entry, "ready", self.host_page
        extra = {}
        while True:
            if self.exit_code is not None:
                reason = "exit"
                if self.exit_code >> 24 == REACH_VIOLATION:   # fnvnx_reach_violation(fact_id): exit code 0x52000000 | fact_id
                    reason, extra["fact_id"] = "reach-violation", self.exit_code & 0xFFFFFF
            elif self.fault is not None:
                reason, extra["fault"] = "fault", self.fault
            elif self.ext_missing:
                reason, extra["ext"] = "ext-module-missing", {"module": self.ext_missing[0], "fn": self.ext_missing[1]}
            elif self.limit_hit:
                reason = "limit"
            else:
                reason = None
            if reason:
                break
            t = self._pick()
            if t is None:
                reason, extra["waits"] = "deadlock", self._waits()
                break
            if self.last is not None and self.last != t.tid:
                self.switches += 1
            self.last, self.cur = t.tid, t
            if self.cb_thread:
                self.cb_thread(t.tid)
            uc.context_restore(t.ctx)
            uc.reg_write(UC_ARM64_REG_X0, t.result & U64)
            t.state, self.slice_calls0 = "running", self.calls
            try:
                uc.emu_start(t.resume_pc, self.return_addr)
            except UcError as e:
                if self.pending is not None:
                    self._deliver_fault(t)
                else:
                    self._stop_fault("uc-error: %s" % e)
                continue
            if self.cur is t and self.fault is None and not self.limit_hit and self.exit_code is None \
                    and not self.ext_missing:
                if uc.reg_read(UC_ARM64_REG_PC) == self.return_addr and t.tid == 1:   # main returned
                    self.exit_code = uc.reg_read(UC_ARM64_REG_X0)
                else:
                    self._stop_fault("unexpected-stop")
        if self.line and self.cb_console:
            self.cb_console(bytes(self.line).decode(errors="replace"))
            self.line = bytearray()
        r = dict(reason=reason, console=b"".join(self.console_text).decode(errors="replace"), exit_code=self.exit_code, host_calls=self.calls, switches=self.switches,
                 threads=len(self.threads), virtual_ns=self.clock.now_ns(), events=list(self.events))
        r.update(extra)
        return r
