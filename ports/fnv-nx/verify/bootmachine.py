#!/usr/bin/env python3
"""The machine of the emulator boot stage: runs the guest ELF (elf32-littleaarch64 ILP32 PIE, crt0 "FNXG", entry(api)
with the FnvnxHostApi v3 table) under Unicorn, deterministically. No fault-tolerating options exist.
Library: Machine, VirtualClock, MachineError, ExtMissing.

Rules:
1. Layout only from bootlayout.plan(): PT_LOADs at plan base + vaddr with ELF protection (bss = fresh zero pages), stacks /
   host page / heap = the plan's regions. No relocation: the guest relocates itself. A segment outside the image region
   or sharing a page with another raises MachineError.
2. Totality: all 46 slots resolve to services.h_<slot> at construction, else MachineError (the list is the header's struct, checked by the tests).
3. One CPU. A thread switches only at a blocking host call and at a host call's return after `hc_slice` calls of its
   slice while another thread is ready: no wall clock, no instruction counts, so switch points repeat. Nothing ready:
   the virtual tick jumps to the earliest deadline; none: reason "deadlock" with a per-thread wait report.
4. Any invalid access (unmapped or protection violation), Unicorn error, or host call on an unmapped guest pointer
   ends the run, reason "fault", record {kind, addr, size, pc, lr, tid}. There is no continue path.
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
5a. TOTAL against the header (hostapi.h TOTALITY TABLE, checked mechanically by tests/test_bootmachine.py against the parsed table): ext_call returns
   NO_MODULE (module >= 64 or not registered), NO_SERVICE (fn > UINT32_MAX, or a module-11 fn without a row: @HOLES@), BAD_NARGS
   (fewer arguments than the row, or nargs > 0 with a null array); nargs above UINT32_MAX counts as UINT32_MAX; only a handler raising
   ExtMissing ends the run. Module 11 serves fn @SERVED@ here. The stage has no network interface and a fixed UTC zone (VC1). Sockets 16..26, 30, 31 are bootsock.py's model (a host with no network interface; its docstring lists every
   served and every NOT answered (service, case); the latter end the run as "ext-module-missing" naming the service, with an event
   "sock-undetermined" saying why): the nargs rule (sockets dispatch before the general check: nargs != the exact count, null array or not, is -EINVAL;
   the exact count with a null array is BAD_NARGS). TZ_AT 34: UTC, offset 0, no DST, abbr "UTC",
   for every instant (utc_seconds is any int64: the declared machine's zone in the emulator is fixed UTC, CLAUDE.md "The declared machine", and UTC has one rule at every instant, so the stage's time service refuses nothing and -EOVERFLOW (-75) never comes from the stage);
   THREAD_SELF 35: a record of the calling thread under a new id
   (never a second thread): started, so thread_start and thread_join answer BadInput; thread_free ends the record only.
   DISPLAY_DOCKED (9) answers 1 for the constructor argument profile="docked", 0 for "handheld" (default). Thread and directory ids are table
   handles (IdTable: (generation << 16) | slot, at most 1024 live together); the stack count (n_stacks) is a limit of this machine, not of the header.
5b. Platform service (module 11; always on). Totality at construction: every fn of PLATFORM_FNS
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
   never picked; unknown tid NotFound 0x1359, a thread not started or ended BadInput 0x1759 (no kernel call, also for a THREAD_SELF record of such a thread); a thread (or the
   record of a thread) that is the CALLING thread: ResultBusy 0xF401, pause or resume; another live thread: pause of a paused one or resume of one that is not paused
   ResultInvalidState 0xFA01, else 0 (svcSetThreadActivity as Atmosphere's libmesosphere states it, source/svc/kern_svc_activity.cpp and KThread::SetActivity: svc::ResultBusy for the current thread, svc::ResultInvalidState for a pause of a paused or a resume of a not paused thread; a result is module | description << 9, module Kernel = 1, descriptions Busy 122, InvalidState 125 - libnx b156b4774fb2 nx/include/switch/result.h MAKERESULT, where 122 is spelled KernelError_AlreadyExists).
5d. Sections (module 11 fn 11..14, host_section.inc; errors are -(code) as u64, recorded as events section_*-refused with the
   positive code). A section is one anonymous mmap (zero, page aligned) kept alive for the machine's life; each view is
   uc.mem_map_ptr of that buffer at the view's address, so views share bytes. MAP checks in the host's order: size, alignment, id,
   range, view table, then placement: addr != 0 must lie inside one VM reservation (else 0xDA01) with no committed extent and no
   view (else 0xD401); addr == 0 takes the lowest free 64 KiB multiple clear of plan regions, free ranges, mappings,
   reservations and views (not a VM reservation). Views are RW guest memory but not guest-owned pages: VM_PROTECT / COMMIT /
   DECOMMIT of a range holding a view page and VM_RELEASE of a reservation holding one are refused 0xD401 (nothing done);
   map_fixed cannot land on one (refused by its own range rules). FREE kills the id at once, the pages live until the last view is
   unmapped.
5d. TEXT_ENTRY (module 11 fn 36, args {max_len, init, init_n, out, n}; _plat_text_entry): the run's scenario `text_entries` (a list of
   strings, one consumed per successful call, in order). Checks as on the console: max_len 0 or > 500, out or n null -> -FNVNX_HE_BADINPUT, no
   text consumed. A text left -> 1, its UTF-16 units + NUL at out, *n = units; no text left (or no scenario) -> 0 (cancel, out and *n
   untouched). DIFFERENCE from the console, as the header says for the emulator: no human, so the answer comes from the scenario instead of
   always 0. A text of more than max_len units is a scenario error: the run ends, reason "scenario-error", extra["scenario"] = why (never
   truncated). A non-writable out / n on an answer of 1 is a host-call fault. Every call is an event {kind "text-entry", max_len, result}.
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


from unicorn import UC_ARCH_ARM64, UC_HOOK_BLOCK, UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_HOOK_MEM_WRITE, UC_MODE_ARM, UC_PROT_ALL, \
    UC_PROT_EXEC, UC_PROT_READ, UC_PROT_WRITE, Uc, UcError
from unicorn import unicorn_const as UCC
from unicorn.arm64_const import UC_ARM64_REG_CPACR_EL1, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP, \
    UC_ARM64_REG_TPIDR_EL0, UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X4

import bootlayout
import bootsock
import icount

PAGE, U64 = 0x1000, (1 << 64) - 1
REACH_VIOLATION = 0x52   # top byte of a guest exit code 0x52000000 | fact_id: a build invariant was violated (fnvnx_reach_violation)
SLOTS = ["output_debug_string", "sleep_ns", "exit", "console_write", "system_tick", "system_tick_freq",
         "thread_create", "thread_start", "thread_join", "thread_free", "thread_exit", "thread_yield",
         "thread_set_priority", "thread_set_cpu", "wait_on_address", "wake_address", "wall_time_us",
         "random64", "memory_info", "cpu_count",
         "fs_open", "fs_read", "fs_write", "fs_pread", "fs_pwrite", "fs_seek", "fs_truncate", "fs_sync",
         "fs_fstat", "fs_close", "fs_stat", "fs_mkdir", "fs_rmdir", "fs_remove", "fs_rename", "fs_dir_open",
         "fs_dir_next", "fs_dir_close",
         "audio_open", "audio_submit", "audio_close", "pad_read", "pad_vibrate", "input_poll", "map_fixed",
         "ext_call"]
API_SIZE = 0x30 + 8 * len(SLOTS)
FNVNX_EXT_PLATFORM = 11
EXT_NO_MODULE, EXT_NO_SERVICE, EXT_BAD_NARGS = U64 - 254, U64 - 253, U64 - 252   # FNVNX_EXT_*: -255, -254, -253 as int64 (hostapi.h)
FNVNX_EXT_MODULES, UINT32_MAX, INT64_MAX = 64, 0xFFFFFFFF, (1 << 63) - 1
FNVNX_TEXT_ENTRY_MAX = 500   # hostapi.h
# hostapi.h TOTALITY TABLE, module 11 services 1..15, 34, 35 and 36: fn -> (name, least nargs). The sockets 16..26, 30, 31 are SOCK_SERVICES;
# both tables are checked against the header's parsed table by tests/test_bootmachine.py.
PLATFORM_FNS = {1: ("cpu_boost", 1), 2: ("thread_activity", 2), 3: ("message", 2), 4: ("vm_reserve", 2), 5: ("vm_commit", 2),
                6: ("vm_decommit", 2), 7: ("vm_release", 1), 8: ("vm_protect", 3), 9: ("display_docked", 0),
                11: ("section_create", 1), 12: ("section_map", 4), 13: ("section_unmap", 2),
                14: ("section_free", 1), 34: ("tz_at", 2), 35: ("thread_self", 0), 36: ("text_entry", 5)}
SOCK_SERVICES = {16: ("sock_socket", 3), 17: ("sock_close", 1), 18: ("sock_bind", 2), 19: ("sock_listen", 2), 20: ("sock_accept", 2),
                 21: ("sock_connect", 2), 22: ("sock_sendto", 5), 23: ("sock_recvfrom", 5), 24: ("sock_setsockopt", 5),
                 25: ("sock_getsockopt", 5), 26: ("sock_poll", 3), 30: ("sock_getfl", 1), 31: ("sock_setfl", 2)}
def _ranges(ns):
    """sorted ints -> "1..9, 11..14, 16" (runs of 3 or more as a..b)"""
    out, i, ns = [], 0, sorted(ns)
    while i < len(ns):
        j = i
        while j + 1 < len(ns) and ns[j + 1] == ns[j] + 1:
            j += 1
        out += ["%d..%d" % (ns[i], ns[j]) if j - i >= 2 else ", ".join(str(n) for n in ns[i:j + 1])]
        i = j + 1
    return ", ".join(out)


_served = set(PLATFORM_FNS) | set(SOCK_SERVICES)
# the docstring's service lists are generated from the two tables above, so they cannot drift from them (hostapi.h rows: tests/test_bootmachine.py)
__doc__ = __doc__.replace("@SERVED@", _ranges(_served)).replace("@HOLES@", "0, " + _ranges(set(range(1, max(_served) + 1)) - _served) + ", %d and up" % (max(_served) + 1))

# wire numbers and lists of sockwire.h live in bootsock.py (tests/test_bootmachine.py compares them with the fixture copy)
from bootsock import (WE_BADF, WE_FAULT, WE_INVAL, WE_OVERFLOW, WE_AFNOSUPPORT, WIRE_AF, WIRE_SOCKTYPE, WIRE_AI_MASK,  # noqa: F401
                      WEAI_BADFLAGS, WEAI_NONAME, WEAI_FAMILY, WEAI_SOCKTYPE, WPOLLNVAL)
TZ_VC1 = (0, 0, b"UTC")   # VC1's zone: off_s, dst, abbr
WINDOW = (0x00400000, 0x02100000)   # the image range 0x00400000..0x00FDF000 and the data window up to 0x02100000: claimed by the host at start
MAP_FIXED_MAX = 512
FNVNX_HE_NOMEM, FNVNX_HE_NOTFOUND = 0x0559, 0x1359
KERNEL_BUSY, KERNEL_INVALID_STATE = 0xF401, 0xFA01   # kernel results 1 | 122 << 9 (Busy), 1 | 125 << 9 (InvalidState); see the docstring
SEC_E_SIZE, SEC_E_ALIGN, SEC_E_RANGE, SEC_E_REGION, SEC_E_ID, SEC_E_NOMEM, SEC_E_FULL = 0xCA01, 0xCC01, 0xD401, 0xDA01, 0xE401, 0xD001, 0xCE01
SEC_MAX_SIZE, SEC_SECTIONS, SEC_VIEWS = 0x80000000, 128, 512   # host_section.inc
VM_ALIGN, VM_FLOOR = 0x10000, 0x10000   # host_vm.inc FNVNX_VM_ALIGN; the guest's low 64 KiB stay unmapped. Forbidden ranges are plan data.
# libnx result values (hostapi.h names them, the repo does not define them): MAKERESULT(Module_Libnx = 345, desc) = 345 | desc << 9,
# desc from libnx's result.h enum LibnxError (BadInput = 11, AlreadyInitialized = 7)
LIBNX_BAD_INPUT, LIBNX_ALREADY_INITIALIZED = 345 | 11 << 9, 345 | 7 << 9   # 0x1759, 0xF59
assert (LIBNX_BAD_INPUT, LIBNX_ALREADY_INITIALIZED, 345 | 9 << 9, 345 | 2 << 9) == (0x1759, 0xF59, FNVNX_HE_NOTFOUND, FNVNX_HE_NOMEM)
ERR_INVALID_SIZE, ERR_INVALID_ADDRESS, ERR_INVALID_CURRENT_MEMORY = 0xCA01, 0xCC01, 0xD401
ERR_INVALID_NEW_MEMORY_PERMISSION, ERR_INVALID_HANDLE, ERR_INVALID_STATE = 0xD801, 0xE401, 0xFA01
ACCESS_KINDS = {UCC.UC_MEM_READ_UNMAPPED: "read-unmapped", UCC.UC_MEM_WRITE_UNMAPPED: "write-unmapped",
                UCC.UC_MEM_FETCH_UNMAPPED: "fetch-unmapped", UCC.UC_MEM_WRITE_PROT: "write-protected",
                UCC.UC_MEM_READ_PROT: "read-protected", UCC.UC_MEM_FETCH_PROT: "fetch-protected"}
REGS = (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X4)


class MachineError(Exception):
    """a construction-time error: the plan, the ELF or the services do not fit the machine"""


class ScenarioError(Exception):
    """the scenario (the run's inputs) contradicts the call it is consumed by: ends the run as reason "scenario-error" """


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


class _GuestMem:
    """the socket model's view of guest memory: readable / writable ranges of the machine (the host page is not writable)"""
    def __init__(self, m):
        self.m = m

    def can_read(self, lo, hi):
        return self.m.accessible(lo, hi, UC_PROT_READ)

    def can_write(self, lo, hi):
        return self.m._writable(lo, hi)

    def read(self, addr, n):
        return bytes(self.m.uc.mem_read(addr, n))

    def write(self, addr, data):
        self.m.uc.mem_write(addr, data)


class SelfRecord:
    """THREAD_SELF's record (hostapi.h 35): an id for the thread that called it; the record is started, the thread is not the host's"""
    def __init__(self, t):
        self.t = t


class Thread:
    def __init__(self, tid):
        self.handle = 0   # the table handle thread_create returned (0: the main thread, which has none)
        self.tid, self.ctx, self.state, self.wait, self.result, self.resume_pc = tid, None, "new", None, 0, None
        self.paused = False
        self.entry = self.arg = 0   # new: new, ready, running, blocked, done; wait: ("addr", a, dl) / ("join", t) / ("sleep", dl)


def _up(v, a=PAGE):
    return (v + a - 1) // a * a


class IdTable:
    """thread and directory ids (host_ids.inc): a handle is (generation << 16) | slot, never an address; at most 1024 live ids of
    both kinds together. A slot takes the next generation when its id is freed, so a freed id never names anything again. The
    first id of a slot has generation 1 (an id is never 0); slots are taken lowest first."""
    MAX = 1024

    def __init__(self):
        self.live, self.gen = {}, {}   # slot -> (kind, object); slot -> current generation

    def alloc(self, kind, obj):
        """the new handle, or 0 when the table is full"""
        for s in range(self.MAX):
            if s not in self.live:
                self.live[s] = (kind, obj)
                return self.gen.setdefault(s, 1) << 16 | s
        return 0

    def get(self, kind, h):
        """the object of a live id of that kind, else None"""
        s = h & 0xFFFF
        e = self.live.get(s) if h >> 16 == self.gen.get(s) else None
        return e[1] if e and e[0] == kind else None

    def free(self, kind, h):
        """True when h named a live id of that kind (now dead)"""
        if self.get(kind, h) is None:
            return False
        s = h & 0xFFFF
        del self.live[s]
        self.gen[s] += 1
        return True


class Machine:
    def __init__(self, elf_bytes, plan, services, clock=None, *, hc_slice=2000, limit_host_calls=None, n_stacks=32,
                 stack_size=0x40000, limit_instructions=None, profile="handheld", text_entries=None):
        if profile not in ("handheld", "docked"):
            raise MachineError("profile must be 'handheld' or 'docked', got %r" % (profile,))
        self.profile = profile   # FNVNX_PLAT_DISPLAY_DOCKED: docked 1, handheld 0
        if text_entries is None:
            text_entries = []
        if not isinstance(text_entries, (list, tuple)) or not all(isinstance(t, str) for t in text_entries):
            raise MachineError("text_entries must be a list of strings")
        try:
            self.text_units = [struct.unpack("<%dH" % (len(b) // 2), b) for b in (t.encode("utf-16-le") for t in text_entries)]
        except UnicodeEncodeError as e:
            raise MachineError("text_entries: a text is not a sequence of Unicode scalars: %s" % e) from None
        self.text_texts, self.text_next, self.scenario_error = list(text_entries), 0, None   # TEXT_ENTRY scenario (rule 5d)
        self.ids = IdTable()
        self.socks = bootsock.SockModel(_GuestMem(self), lambda: self.clock.now_ns())
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
        if any(lo < WINDOW[1] and WINDOW[0] < hi for lo, hi in self.regions):
            raise MachineError("a plan region overlaps the host window 0x%x..0x%x" % WINDOW)
        self.sections, self.views, self.sec_next = {}, {}, 1   # id -> Section dict; view addr -> view dict; next id (never reused)
        self.vm_resv, self.vm_pages, self.vm_ext = {}, set(), []   # base -> size; committed page addresses; extents [lo, hi): one per commit gap (host_vm.inc)
        self.ext = {int(n[4:]): getattr(services, n) for n in dir(services) if n.startswith("ext_") and n[4:].isdigit()}
        uc = self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)   # FP/SIMD on
        self.backs = []   # [lo, hi, host address, keepalive]: every host-backed mapping, sorted by lo (host_ptr)
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
            self._map(lo, hi - lo, UC_PROT_ALL)
            uc.mem_write(base + s["vaddr"], bytes(elf_bytes[s["offset"]:s["offset"] + s["filesz"]]))
            uc.mem_protect(lo, hi - lo, prot)
        for n in ("stacks", "heap"):
            self._map(reg[n][0], reg[n][1] - reg[n][0])
        self._map(WINDOW[0], WINDOW[1] - WINDOW[0])   # the host's window: RW zero from the start
        self.host_page = hp = reg["host_page"][0]
        self._map(hp, PAGE, UC_PROT_ALL, track=False)   # the host page is never handed out as a host pointer
        self.stub_base, self.return_addr, self.thread_end = hp + 0x800, hp + 0xFF0, hp + 0xFF8
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
        self.mf_live = {}   # addr -> (size, src) of every live map_fixed mapping (the kernel's own record)
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

    def sp(self):
        """the guest SP of the running thread (valid inside a watch callback)"""
        return self.uc.reg_read(UC_ARM64_REG_SP)

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
    def _map(self, addr, size, prot=UC_PROT_READ | UC_PROT_WRITE, ptr=None, track=True):
        """every guest mapping is backed by a host buffer the machine owns (anonymous mmap, zero) or, for a section view, by the
        section's buffer; mem_map_ptr hands that address to Unicorn, so host_ptr can return a stable host address"""
        keep = None
        if ptr is None:
            mm = mmap.mmap(-1, size)
            buf = ctypes.c_char.from_buffer(mm)
            ptr, keep = ctypes.addressof(buf), (mm, buf)
        self.uc.mem_map_ptr(addr, size, prot, ptr)
        if track:
            self.backs.append([addr, addr + size, ptr, keep])
            self.backs.sort(key=lambda b: b[0])
        else:
            self.untracked = keep   # the host page lives as long as the machine

    def _unmap(self, addr, size):
        """unmap [addr, addr + size) and drop it from the backing table; a backing it only partly covers keeps its remaining pieces"""
        self.uc.mem_unmap(addr, size)
        end, out = addr + size, []
        for b in self.backs:
            lo, hi, ptr, keep = b
            if hi <= addr or lo >= end:
                out.append(b)
                continue
            if lo < addr:
                out.append([lo, addr, ptr, keep])
            if end < hi:
                out.append([end, hi, ptr + (end - lo), keep])
        self.backs = out

    def host_ptr(self, addr, size):
        """host address of [addr, addr + size) when it lies wholly inside ONE mapping (one host buffer) that is now readable and
        writable for the guest (never the host page; none-protected and unmapped ranges: None). An EMPTY range (size 0, the
        renderer library's contract: it asks for it and never dereferences the result) gets a pointer when addr lies inside
        such a mapping or exactly at its end, and None when addr is outside every mapping."""
        if size < 0 or addr < 0 or addr + size > 1 << 64:
            return None
        rw = lambda lo, hi: self.accessible(lo, hi, UC_PROT_READ) and self.accessible(lo, hi, UC_PROT_WRITE)  # noqa: E731
        for lo, hi, ptr, _k in self.backs:
            if size and lo <= addr and addr + size <= hi:
                return ptr + (addr - lo) if rw(addr, addr + size) else None
            if not size and lo <= addr <= hi:
                probe = addr if addr < hi else addr - 1          # the byte the empty range touches the mapping at
                if rw(probe, probe + 1):
                    return ptr + (addr - lo)
        return None

    def stack_for(self, tid):
        return self.stacks_top - self.stack_size * (tid - 1) - 0x100

    def _event(self, kind, **kw):
        self.events.append(dict(kind=kind, tid=self.tid(), **kw))

    def _in_window(self, lo, hi):
        return WINDOW[0] <= lo and hi <= WINDOW[1]

    def _owned(self, lo, hi):
        """every page of [lo, hi) is guest-owned: ordinary heap (not given away), a map_fixed range, a committed VM extent or the
        host's window; the range may span several of these"""
        iv = [(max(self.heap[0], x), min(self.heap[1], y)) for x, y in self._heap_free()] + self.mapped \
            + [tuple(e) for e in self.vm_ext] + [WINDOW]
        p = lo
        for a, b in sorted(iv):
            if a <= p < b:
                p = b
        return p >= hi

    def _heap_free(self):
        """the heap region minus the blocks map_fixed gave away"""
        out, p = [], self.heap[0]
        for a, b in sorted(self.given):
            if a > p:
                out.append((p, a))
            p = max(p, b)
        if p < self.heap[1]:
            out.append((p, self.heap[1]))
        return out

    def _vm_committed(self, lo, hi):
        return lo in self.vm_pages and all(p in self.vm_pages for p in range(lo, hi, PAGE))

    def _zero_window(self, addr, size):
        self.uc.mem_protect(addr, size, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_write(addr, bytes(size))

    def map_fixed(self, addr, size, src):
        """hostapi.h map_fixed, checks in the header's order"""
        if size == 0 or addr + size >= 1 << 64 or addr % PAGE or size % PAGE:
            return self._refuse("map_fixed", addr, size, "size 0, wraps or not 4 KiB aligned", LIBNX_BAD_INPUT)
        if self._in_window(addr, addr + size):   # the host's window: read-write and zeroed, src is not looked at
            self._zero_window(addr, size)
            return 0
        if src == 0 or src % PAGE:
            return self._refuse("map_fixed", addr, size, "src 0 or not 4 KiB aligned", LIBNX_BAD_INPUT)
        if len(self.mf_live) >= MAP_FIXED_MAX:
            return self._refuse("map_fixed", addr, size, "%d map_fixed ranges live" % MAP_FIXED_MAX, FNVNX_HE_NOMEM)
        if any(b > addr and addr + size > a for a, b, _p in self.uc.mem_regions()):   # any occupied range, a section view included
            return self._refuse("map_fixed", addr, size, "range is occupied", LIBNX_ALREADY_INITIALIZED)
        if not any(n != "keys" and lo <= addr and addr + size <= hi for n, lo, hi in self.free):
            return self._refuse("map_fixed", addr, size, "not wholly inside the alias-code region (a mappable reserved range)",
                                SEC_E_REGION)
        if not any(a <= src and src + size <= b for a, b in self._heap_free()):
            return self._refuse("map_fixed", addr, size, "src block 0x%x is not ordinary heap (outside it or given away)" % src,
                                ERR_INVALID_CURRENT_MEMORY)
        content = bytes(self.uc.mem_read(src, size))
        self._unmap(src, size)
        self.given.append((src, src + size))
        self._map(addr, size)
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

    def _refuse(self, what, addr, size, why, code):
        self._event(what + "-refused", addr=addr, size=size, why=why, result=code)
        return code

    def _words(self, args, n):
        return [struct.unpack("<Q", self.uc.mem_read(args + 8 * i, 8))[0] for i in range(n)]

    def ext_call(self, module, fn, args, nargs):
        """hostapi.h ext_call: module >= 64 or not registered -> NO_MODULE; fn > UINT32_MAX -> NO_SERVICE (handler not called);
        nargs above UINT32_MAX counts as UINT32_MAX. A handler raising ExtMissing ends the run (a service this machine cannot answer)."""
        if module >= FNVNX_EXT_MODULES or (module != FNVNX_EXT_PLATFORM and module not in self.ext):
            return EXT_NO_MODULE
        if fn > UINT32_MAX:
            return EXT_NO_SERVICE
        nargs = min(nargs, UINT32_MAX)
        if module == FNVNX_EXT_PLATFORM:
            return self._platform_ext(fn, args, nargs)
        return self.ext[module](fn, self._words(args, nargs) if nargs else [])

    def _platform_ext(self, fn, args, nargs):
        """host_platform.inc: sockets 16..26, 30, 31 check nargs themselves (exactly the count, else -WE_INVAL); a fn without a row is
        NO_SERVICE; fewer arguments than the row, or nargs > 0 with a null array, is BAD_NARGS; extra arguments are ignored"""
        if fn in SOCK_SERVICES:   # host_platform.inc: 16..26, 30, 31 dispatch BEFORE the general nargs / null-array check
            if nargs != SOCK_SERVICES[fn][1]:
                return -WE_INVAL & U64
            if not args:
                return EXT_BAD_NARGS
            try:
                return self.socks.call(fn, self._words(args, nargs))
            except bootsock.NotDetermined as e:
                self._event("sock-undetermined", fn=fn, why=str(e))
                raise ExtMissing(FNVNX_EXT_PLATFORM, fn) from None
            except bootsock.BadGuestMemory as e:
                raise MachineError("sock service %d: %s" % (fn, e)) from None
        if fn not in PLATFORM_FNS:
            return EXT_NO_SERVICE
        _name, need = PLATFORM_FNS[fn]
        if nargs < need or (nargs and not args):
            return EXT_BAD_NARGS
        return self.plat[fn](self._words(args, need))

    def tz_at(self, utc_s):
        """VC1's time zone at an instant: (off_s, dst, abbr); fixed UTC, the same at every instant"""
        return TZ_VC1

    def _plat_tz_at(self, a):
        utc_s, out = a
        if not out:
            return -WE_FAULT & U64
        z = self.tz_at(utc_s)
        if not self._writable(out, out + 16):
            raise MachineError("tz_at: out %#x not writable guest memory" % out)
        self.uc.mem_write(out, struct.pack("<iI8s", z[0], z[1], z[2][:7]))
        return 0

    def _plat_thread_self(self, a):
        return self.ids.alloc("thread", SelfRecord(self.cur))

    # -- platform service, module 11 (rule 5b)
    def _vm_blocked(self):
        """sorted (lo, hi) of everything a reservation must not overlap"""
        return sorted(self.regions + [WINDOW] + [(lo, hi) for _n, lo, hi in self.free] + self.mapped
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
        if want > 1 << 32:
            return self._refuse("vm_reserve", hint, want, "size above 4 GiB", 0)
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
            self._map(x, y - x)
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
                self._unmap(x, y - x)
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
            return self._refuse("vm_decommit", addr, size, "addr + size wraps", ERR_INVALID_CURRENT_MEMORY)
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
        """hostapi.h 8: checks in the header's order; prot 0, 1, 3 only (every one expressible on every guest-owned page)"""
        addr, size, prot = a[:3]
        if addr % PAGE:
            return self._refuse("vm_protect", addr, size, "address", ERR_INVALID_ADDRESS)
        if size == 0 or size % PAGE:
            return self._refuse("vm_protect", addr, size, "size", ERR_INVALID_SIZE)
        if addr + size >= 1 << 64:
            return self._refuse("vm_protect", addr, size, "addr + size wraps", ERR_INVALID_CURRENT_MEMORY)
        if prot not in (0, 1, 3):
            return self._refuse("vm_protect", addr, size, "prot 0x%x is not 0, 1 or 3" % prot, ERR_INVALID_NEW_MEMORY_PERMISSION)
        if self._view_overlap(addr, addr + size) or not self._owned(addr, addr + size):
            return self._refuse("vm_protect", addr, size, "a page the guest does not own (a section view included)",
                                ERR_INVALID_CURRENT_MEMORY)
        self.uc.mem_protect(addr, size, (UC_PROT_READ if prot & 1 else 0) | (UC_PROT_WRITE if prot & 2 else 0))
        return 0

    def _plat_display_docked(self, a):
        """FNVNX_PLAT_DISPLAY_DOCKED: 1 docked, 0 handheld, from the machine's profile"""
        return self.services.input.docked(self, self.profile == "docked")

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
        self._map(addr, size, ptr=sec["ptr"] + off)
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
        self._unmap(addr, size)
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

    def _plat_text_entry(self, a):
        """rule 5d. FNVNX_PLAT_TEXT_ENTRY (max_len, init, init_n, out, n): the checks of host_textentry.inc (max_len 0 or > 500, out or n
        null -> -FNVNX_HE_BADINPUT, nothing shown, no text consumed); then the scenario answers: the next text -> 1 with its UTF-16 units,
        a NUL and *n; no text left -> 0 (cancelled; out and *n untouched). init is not read (the answer does not depend on it)."""
        max_len, _init, _init_n, out, n = a[:5]
        if max_len == 0 or max_len > FNVNX_TEXT_ENTRY_MAX or out == 0 or n == 0:
            self._event("text-entry", max_len=max_len, result=-LIBNX_BAD_INPUT)
            return -LIBNX_BAD_INPUT & U64
        if self.text_next >= len(self.text_units):
            self._event("text-entry", max_len=max_len, result=0)
            return 0
        units = self.text_units[self.text_next]
        if len(units) > max_len:
            raise ScenarioError("scenario text %d (%r) is %d UTF-16 units, the guest's TEXT_ENTRY call has max_len %d (a scenario text is never truncated)"
                                % (self.text_next, self.text_texts[self.text_next], len(units), max_len))
        if not self._writable(out, out + 2 * (len(units) + 1)) or not self._writable(n, n + 4):
            raise MachineError("text_entry: out %#x (%d units and a NUL) or n %#x is not writable guest memory" % (out, len(units), n))
        self.text_next += 1
        self.uc.mem_write(out, struct.pack("<%dH" % (len(units) + 1), *units, 0))
        self.uc.mem_write(n, struct.pack("<I", len(units)))
        self._event("text-entry", max_len=max_len, result=1, units=len(units))
        return 1

    def accessible(self, lo, hi, perm):
        """[lo, hi) is wholly mapped with the permission `perm` (UC_PROT_READ / UC_PROT_WRITE)"""
        while lo < hi:
            r = next(((b, e) for b, e, pr in self.uc.mem_regions() if b <= lo <= e and pr & perm), None)
            if r is None:
                return False
            lo = r[1] + 1
        return True

    def _writable(self, lo, hi):
        """[lo, hi) is wholly mapped writable guest memory, not the host page"""
        return not (lo < self.host_page + PAGE and self.host_page < hi) and self.accessible(lo, hi, UC_PROT_WRITE)

    def _plat_thread_activity(self, a):
        tid, paused = a[0], a[1]
        if tid == 0:   # the calling thread: no kernel call (host_thread_activity.inc)
            return self._refuse("thread_activity", tid, paused, "the calling thread", LIBNX_BAD_INPUT)
        t = self.ids.get("thread", tid)
        if t is None:
            return self._refuse("thread_activity", tid, paused, "id names no live thread", FNVNX_HE_NOTFOUND)
        if isinstance(t, SelfRecord):   # started for ever; the kernel answers for the real thread
            t = t.t
        if t.state in ("new", "done"):   # nothing to pause or resume: BadInput without a kernel call
            return self._refuse("thread_activity", tid, paused, "thread is %s" % t.state, LIBNX_BAD_INPUT)
        if t is self.cur:   # the kernel does not pause or resume the calling thread through its own handle
            return self._refuse("thread_activity", tid, paused, "the calling thread's own handle", KERNEL_BUSY)
        if t.paused == bool(paused):   # pause of a paused thread, resume of one that is not paused
            return self._refuse("thread_activity", tid, paused, "thread is already %s" % ("paused" if paused else "running"), KERNEL_INVALID_STATE)
        t.paused = bool(paused)
        return 0

    # -- threads and blocking
    def thread_create(self, entry, arg):
        tid = max(self.threads) + 1
        if tid > self.max_threads:
            return 0
        h = self.ids.alloc("thread", None)
        if not h:
            return 0
        t = self.threads[tid] = Thread(tid)
        t.handle, self.ids.live[h & 0xFFFF] = h, ("thread", t)
        t.entry, t.arg = entry, arg
        self.order.append(tid)
        return h

    def thread_start(self, h):
        t, uc = self.ids.get("thread", h), self.uc
        if t is None:
            return FNVNX_HE_NOTFOUND
        if isinstance(t, SelfRecord) or t.state != "new":   # already started (also a THREAD_SELF record): nothing changed
            return LIBNX_BAD_INPUT
        tid = t.tid
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
        if self.fault is None:
            self.fault = dict(kind=ACCESS_KINDS.get(access, "access-%d" % access), addr=addr, size=size,
                              pc=uc.reg_read(UC_ARM64_REG_PC), lr=uc.reg_read(UC_ARM64_REG_LR), tid=self.tid())
        return False

    def _stop_fault(self, kind, addr=None, size=None):
        if self.fault is None:
            self.fault = dict(kind=kind, addr=addr, size=size, pc=self.uc.reg_read(UC_ARM64_REG_PC),
                              lr=self.uc.reg_read(UC_ARM64_REG_LR), tid=self.tid())
        self.uc.emu_stop()

    def _on_code(self, uc, addr, size, _):
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
        except ScenarioError as e:
            self.scenario_error = str(e)
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
            elif self.scenario_error is not None:
                reason, extra["scenario"] = "scenario-error", self.scenario_error
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
                self._stop_fault("uc-error: %s" % e)
                continue
            if self.cur is t and self.fault is None and not self.limit_hit and self.exit_code is None \
                    and not self.ext_missing and self.scenario_error is None:
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
