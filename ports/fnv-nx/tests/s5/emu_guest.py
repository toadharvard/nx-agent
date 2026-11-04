#!/usr/bin/env python3
"""Run an fnv-nx ILP32 guest NSO under Unicorn with a Python stand-in for the LP64 host (FnvnxHostApi v2).

    python3 -I tests/s5/emu_guest.py build/s5/hello/guest.nso [--expect TEXT] [--root DIR] [--file NAME=PATH]

What it checks without a Switch: the NSO parses and its hashes match (as the host loader checks), the guest
runs at the link address in its header (+16) with the size it declares (+20), outside the reserved
original-image range, never writes its text/rodata (mapped R-X / R-- like the host maps them), and that its
runtime works: newlib, TLS on TPIDR_EL0, threads, locks, condvars, files, C++.

Threads: one emulated CPU, cooperative scheduling. A thread switch happens only inside host calls that block
or yield (wait_on_address, thread_join, sleep_ns, thread_yield, thread_exit, thread end), round-robin; time is
virtual (sleep and timeouts advance it). "sdmc:/" maps to --root (a fresh temp dir by default).
The host side (libnx, Horizon SVCs) is not exercised here; the hardware test covers it.

Needs: pip install unicorn
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import struct
import sys
import traceback
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "toolchain", "tools"))
import elf2nso  # noqa: E402

from unicorn import UC_ARCH_ARM64, UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_HOOK_MEM_WRITE, UC_MODE_ARM, UC_PROT_ALL, UC_PROT_EXEC, \
    UC_PROT_READ, UC_PROT_WRITE, Uc, UcError  # noqa: E402
from unicorn.arm64_const import UC_ARM64_REG_CPACR_EL1, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP, \
    UC_ARM64_REG_TPIDR_EL0, UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, \
    UC_ARM64_REG_X4, UC_ARM64_REG_X29  # noqa: E402

PAGE = 0x1000
HOST_PAGE = 0x3F000000          # host API table + stubs (below 4 GiB, as every address in a 32-bit AS)
STACKS_TOP, STACK_SIZE, MAX_THREADS = 0x3E000000, 0x40000, 32
HEAP_BASE, HEAP_SIZE = 0x30000000, 0x4000000
HEAP_BASE_ALT = 0x34000000      # when the guest itself runs at 0x30000000 (a pinned guest at its link base)
RETURN_ADDR = HOST_PAGE + 0xFF0         # main thread returns here
THREAD_END = HOST_PAGE + 0xFF8          # created threads return here
TICK_FREQ = 19_200_000
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
U64 = (1 << 64) - 1


def align(x, a=PAGE):
    return (x + a - 1) & ~(a - 1)


def neg(e):
    return (-e) & U64


# ---- thread placement: the host's table (toolchain/host/placement.h), read from the header so the emulated host
# places threads exactly like host_v2.inc; each thread's (role, ideal core, mask, priority) is printed at the end.
_PLACEMENT = None

STUB_ACTIONS = r"return0|return-this|(use-)?malloc:x[0-7]"


def parse_fault_table(path):
    """--stub-faults table (tests/s5/known_faults.txt): ([(function, action)], [(before-winmain function, x0)]).
    A function is a link name or an original address 0x...; '#' starts a comment."""
    stubs, pre = [], []
    for k, line in enumerate(open(path), 1):
        f = line.split("#", 1)[0].split()
        if not f:
            continue
        if f[0] == "before-winmain":   # before-winmain <function> [x0]
            if len(f) not in (2, 3):
                raise ValueError(f"{path}:{k}: before-winmain <function> [x0]")
            pre.append((f[1], int(f[2], 0) if len(f) > 2 else 0))
            continue
        action = f[1] if len(f) > 1 else "return0"
        if len(f) > 2 or not re.fullmatch(STUB_ACTIONS, action):
            raise ValueError(f"{path}:{k}: <function> [return0 | return-this | malloc:xN | use-malloc:xN]")
        stubs.append((f[0], action))
    return stubs, pre


MILESTONES = os.path.join(HERE, "milestones.txt")


def parse_milestones(path):
    """--milestones table (tests/s5/milestones.txt): [(name, 'call'|'store', target)] in file order; a name may have
    several lines (any one reaches it); '#' starts a comment."""
    out = []
    with open(path) as fh:
        lines = fh.read().splitlines()
    for k, line in enumerate(lines, 1):
        f = line.split("#", 1)[0].split()
        if not f:
            continue
        ok = len(f) == 3 and re.fullmatch(r"[a-z][\w-]*", f[0]) and (
            f[1] == "call" and (re.fullmatch(r"0x[0-9a-fA-F]+", f[2]) or f[2].startswith("re:")) or
            f[1] == "store" and re.fullmatch(r"\*?0x[0-9a-fA-F]+(\+(0x[0-9a-fA-F]+|\d+))?", f[2]))
        if not ok:
            raise ValueError(f"{path}:{k}: <name> call <0xORIG | re:REGEX> | <name> store <0xADDR | *0xADDR+N>")
        if f[1] == "call" and f[2].startswith("re:"):
            re.compile(f[2][3:])
        out.append(tuple(f))
    return out


def placement_table():
    """{role number: (name, ideal, mask, prio, mask3)} from placement.h and the FNVNX_ROLE_* enum of hostapi.h
    (mask3: the mask with the core 3 opt-in, 0 = mask)"""
    global _PLACEMENT
    if _PLACEMENT is None:
        inc = os.path.join(HERE, "..", "..", "toolchain")
        roles = dict((m.group(1), int(m.group(2))) for m in re.finditer(
            r"FNVNX_ROLE_(\w+)\s*=\s*(\d+)", open(os.path.join(inc, "include", "fnvnx", "hostapi.h")).read()))
        _PLACEMENT = {}
        n = r'(0x[0-9a-fA-F]+|\d+)'
        for m in re.finditer(r'\[FNVNX_ROLE_(\w+)\]\s*=\s*\{"([^"]+)",\s*(-?\d+),\s*' + n + r',\s*' + n + r',\s*' + n + r'\}',
                             open(os.path.join(inc, "host", "placement.h")).read()):
            _PLACEMENT[roles[m.group(1)]] = (m.group(2), int(m.group(3)), int(m.group(4), 0), int(m.group(6), 0),
                                             int(m.group(5), 0))
    return _PLACEMENT


def win32_to_horizon_prio(prio):
    p = 0x2C - prio
    return 0x1C if p < 0x1C else 0x3B if p > 0x3B else p


def place(cpu, seq, prio, core3=False):
    """fnvnx_place_ex(): thread_create's cpu argument -> (role name, ideal core, mask, Horizon priority); core3: the
    core 3 opt-in (sdmc:/fnvnx/fnvnx.ini core3=1, --core3)"""
    prio = prio - (1 << 64) if prio >> 63 else prio
    table = placement_table()
    if cpu & ~0xFF == 0x100 and (cpu & 0xFF) in table:
        role = cpu & 0xFF
    elif cpu >> 63 or cpu & ~0xFF:
        role = 0
    else:
        return ("explicit", cpu, 1 << (cpu & 31), win32_to_horizon_prio(prio))
    name, ideal, mask, hp, mask3 = table[role]
    mask = mask3 & 0xF if core3 and mask3 else mask & 7
    if ideal < 0:
        cores = [c for c in range(32) if mask >> c & 1]
        ideal = cores[seq % len(cores)] if cores else 0
    return (name, ideal, mask | 1 << ideal, hp or win32_to_horizon_prio(prio))


def cpu_spec(spec, ideal, mask):
    """fnvnx_cpu_spec(): thread_set_cpu's spec applied to (ideal, mask)"""
    if not spec >> 63:
        return spec & 31, 1 << (spec & 31)
    m, want = (spec >> 8) & 7, spec & 0xFF
    mask = m or mask
    if want != 0xFF and want < 32:
        ideal = want
    elif not mask >> ideal & 1:
        ideal = next(c for c in range(32) if mask >> c & 1)
    return ideal, mask | 1 << ideal


class Thread:
    def __init__(self, tid, ctx=None):
        self.tid, self.ctx = tid, ctx
        self.state = "new"          # new, ready, running, blocked, done
        self.wait = None            # ("addr", addr, deadline) / ("join", tid) / ("sleep", deadline)
        self.result = 0             # x0 to deliver when resumed
        self.resume_pc = None


class Emu:
    def __init__(self, nso_path, root, verbose=False, load_base=None):
        with open(nso_path, "rb") as f:
            info = elf2nso.parse_nso(f.read())
        for s in info["segments"]:
            if not s["hash_ok"]:
                raise SystemExit(f"{nso_path}: {s['name']} hash mismatch")
        text, ro, data = info["segments"]
        magic, _abi, link_base, size, gflags = struct.unpack_from("<IIIII", text["bytes"], 8)
        if magic != 0x47584E46:
            raise SystemExit("not an fnv-nx guest (no FNXG magic at +8)")
        if size != align(data["dst"] + data["size"] + data["bss"]):
            raise SystemExit(f"guest header size {size:#x} does not match the NSO")
        self.relocatable, self.link_base = bool(gflags & 1), link_base
        self.pinned = bool(gflags & 2)   # position-dependent code inside: runs only at its link base (elf2nso --pin)
        # a relocatable guest goes where the host finds room (here: deliberately not its link base)
        base = (load_base or 0x05000000) if self.relocatable and not self.pinned else link_base
        if self.pinned and load_base and load_base != link_base:
            raise SystemExit(f"pinned guest: it runs only at its link base {link_base:#x}")
        if base < 0x200000 or base + size > 0x40000000 or (base < 0x02100000 and base + size > 0x00400000):
            raise SystemExit(f"guest link range {base:#x}..{base + size:#x} not allowed")
        self.base, self.size, self.root, self.verbose = base, size, root, verbose
        self.text_bytes = text["bytes"]
        uc = self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)   # FP/SIMD on

        def map_seg(seg, prot, extra=0):
            sz = align(seg["size"] + extra)
            uc.mem_map(base + seg["dst"], sz, UC_PROT_ALL)
            uc.mem_write(base + seg["dst"], seg["bytes"])
            uc.mem_protect(base + seg["dst"], sz, prot)

        map_seg(text, UC_PROT_READ | UC_PROT_EXEC)
        map_seg(ro, UC_PROT_READ)
        map_seg(data, UC_PROT_READ | UC_PROT_WRITE, data["bss"])
        uc.mem_map(STACKS_TOP - STACK_SIZE * MAX_THREADS, STACK_SIZE * MAX_THREADS, UC_PROT_READ | UC_PROT_WRITE)
        heap = self.heap = HEAP_BASE if base >= HEAP_BASE + HEAP_SIZE or base + size <= HEAP_BASE else HEAP_BASE_ALT
        uc.mem_map(heap, HEAP_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        uc.mem_map(HOST_PAGE, PAGE, UC_PROT_ALL)
        self.stubs = {HOST_PAGE + 0x800 + 4 * i: name for i, name in enumerate(SLOTS)}
        api = struct.pack("<IIII", 0x48584E46, 3, API_SIZE, 0)
        api += struct.pack("<QQQQ", base, size, heap, HEAP_SIZE)
        api += b"".join(struct.pack("<Q", a) for a in self.stubs)
        assert len(api) == API_SIZE
        uc.mem_write(HOST_PAGE, api)
        uc.mem_write(HOST_PAGE + 0x800, struct.pack("<I", 0xD65F03C0) * len(SLOTS))
        self.tick = 1_000_000
        self.console, self.debug, self.calls = bytearray(), [], 0
        self.threads: dict[int, Thread] = {}
        self.order: list[int] = []
        self.cur: Thread | None = None
        self.exit_code = None
        self.fds, self.dirs, self.next_dir = {}, {}, 1
        self.ctx_switches, self.max_threads, self.addr_waits = 0, 1, 0
        uc.hook_add(UC_HOOK_CODE, self.on_code, begin=HOST_PAGE, end=HOST_PAGE + PAGE - 1)
        uc.hook_add(UC_HOOK_MEM_INVALID, self.on_bad)

    # -- helpers
    def rs(self, addr):
        out = bytearray()
        while True:
            b = self.uc.mem_read(addr + len(out), 64)
            i = b.find(0)
            if i >= 0:
                return (out + b[:i]).decode(errors="surrogateescape")   # the guest may pass any bytes
            out += b

    def path(self, p):
        p = self.rs(p)
        if p.startswith("sdmc:"):
            p = p[5:]
        return os.path.join(self.root, p.lstrip("/"))

    def now_ns(self):
        return self.tick * 1_000_000_000 // TICK_FREQ

    def stack_for(self, tid):
        return STACKS_TOP - STACK_SIZE * (tid - 1) - 0x100

    def on_bad(self, uc, access, addr, size, value, _):
        print(f"  invalid memory access type={access} addr={addr:#x} size={size} pc={uc.reg_read(UC_ARM64_REG_PC):#x}"
              f" thread={self.cur.tid if self.cur else '?'}")
        return False

    # -- host calls
    def on_code(self, uc, addr, size, _):
        if addr == THREAD_END:
            self.block(("done",), stop=True)
            return
        name = self.stubs.get(addr)
        if not name:
            return
        self.calls += 1
        a = [uc.reg_read(r) for r in (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
                                      UC_ARM64_REG_X4)]
        res = getattr(self, "h_" + name)(*a)
        if isinstance(res, tuple):          # blocking call: (wait spec, result when resumed)
            self.block(res[0], result=res[1], stop=True)
            return
        uc.reg_write(UC_ARM64_REG_X0, res & U64)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))

    def block(self, wait, result=0, stop=True):
        t = self.cur
        t.resume_pc = self.uc.reg_read(UC_ARM64_REG_LR)
        t.result = result
        t.ctx = self.uc.context_save()
        if wait[0] == "done":
            t.state = "done"
            self.wake_joiners(t.tid)
        elif wait[0] == "yield":
            t.state = "ready"
        else:
            t.state, t.wait = "blocked", wait
        self.cur = None
        self.uc.emu_stop()

    def wake_joiners(self, tid):
        for o in self.threads.values():
            if o.state == "blocked" and o.wait[0] == "join" and o.wait[1] == tid:
                o.state, o.result = "ready", 0

    # v1
    def h_output_debug_string(self, s, n, *_):
        self.debug.append(bytes(self.uc.mem_read(s, n)).decode(errors="replace"))
        return 0

    def h_sleep_ns(self, ns, *_):
        return (("sleep", self.now_ns() + ns), 0)

    def h_exit(self, code, *_):
        self.exit_code = code
        self.cur.state = "done"
        self.uc.emu_stop()
        return 0

    def h_console_write(self, b, n, *_):
        self.console += self.uc.mem_read(b, n)
        return n

    def h_console_flush(self, *_):
        return 0

    def h_main_loop(self, *_):
        return 1

    def h_buttons_down(self, *_):
        return 0

    def h_system_tick(self, *_):
        self.tick += 1000
        return self.tick

    def h_system_tick_freq(self, *_):
        return TICK_FREQ

    # v2 threads
    def h_thread_create(self, entry, arg, stack, prio, cpu, *_):
        tid = max(self.threads) + 1
        if tid > MAX_THREADS:
            return 0
        t = Thread(tid)
        t.entry, t.arg = entry, arg
        self.thread_seq = getattr(self, "thread_seq", 0)
        t.place = list(place(cpu, self.thread_seq, prio, getattr(self, "core3", False)))
        self.thread_seq += 1
        self.threads[tid] = t
        self.order.append(tid)
        self.max_threads = max(self.max_threads, sum(1 for x in self.threads.values() if x.state != "done"))
        return tid

    def h_thread_start(self, tid, *_):
        t = self.threads.get(tid)
        if not t or t.state != "new":
            return 1
        regs = self.uc.context_save()
        self.uc.reg_write(UC_ARM64_REG_X0, t.arg)
        self.uc.reg_write(UC_ARM64_REG_LR, THREAD_END)
        self.uc.reg_write(UC_ARM64_REG_SP, self.stack_for(tid))
        self.uc.reg_write(UC_ARM64_REG_TPIDR_EL0, 0)
        t.ctx = self.uc.context_save()
        t.resume_pc, t.result = t.entry, t.arg
        self.uc.context_restore(regs)
        t.state = "ready"
        return 0

    def h_thread_join(self, tid, *_):
        t = self.threads.get(tid)
        if t is None or t.state == "done":
            return 0
        return (("join", tid), 0)

    def h_thread_free(self, tid, *_):
        return 0

    def h_thread_exit(self, *_):
        self.block(("done",))
        return 0

    def h_thread_yield(self, *_):
        return (("yield",), 0)

    def placed(self, tid):
        t = self.threads.get(tid) if tid else self.cur
        if t is not None and not hasattr(t, "place"):
            t.place = ["main", 0, 1, 0x2C]
        return t

    def h_thread_set_priority(self, tid, prio, *_):   # another thread's id too (not started yet is fine)
        t = self.placed(tid)
        if t is None:
            return 0xE401   # a Horizon "invalid handle" result
        t.place[3] = win32_to_horizon_prio(prio - (1 << 64) if prio >> 63 else prio)
        return 0

    def h_thread_set_cpu(self, tid, spec, *_):
        t = self.placed(tid)
        if t is None:
            return 0xE401
        if (spec >> 40) & 0x7FFFFF:   # -1: nothing to do
            return 0
        t.place[1], t.place[2] = cpu_spec(spec, t.place[1], t.place[2])
        return 0

    def placements(self):
        return [f"placement\tthread {tid}\t{p[0]}\tcore {p[1]}\tmask {p[2]:#x}\tpriority {p[3]:#x}"
                for tid, t in sorted(self.threads.items()) for p in [getattr(t, "place", None)] if p]

    def h_wait_on_address(self, addr, expected, timeout, *_):
        (v,) = struct.unpack("<I", self.uc.mem_read(addr, 4))
        if v != (expected & 0xFFFFFFFF):
            return 2
        if timeout == 0:
            return 1
        self.addr_waits += 1
        deadline = None if timeout >= 1 << 63 else self.now_ns() + timeout
        return (("addr", addr, deadline), 0)

    def h_wake_address(self, addr, count, *_):
        n = 0
        for tid in self.order:
            o = self.threads[tid]
            if o.state == "blocked" and o.wait[0] == "addr" and o.wait[1] == addr:
                if count != U64 and n >= count:
                    break
                o.state, o.result = "ready", 0
                n += 1
        return 0

    def h_wall_time_us(self, *_):
        return 1_700_000_000_000_000 + self.now_ns() // 1000

    def h_tz_offset_minutes(self, *_):
        return 0

    def h_random64(self, *_):
        return int.from_bytes(os.urandom(8), "little")

    def h_memory_info(self, out, *_):
        self.uc.mem_write(out, struct.pack("<QQ", 4 << 30, 3 << 30))
        return 0

    def h_cpu_count(self, *_):
        return 3

    # v2 files
    def fs(self, fn, *a):
        try:
            r = fn(*a)
            return 0 if r is None else r
        except OSError as e:
            return neg(e.errno or 5)

    def h_fs_open(self, p, flags, *_):
        fl = (os.O_RDWR if flags & 3 == 3 else os.O_WRONLY if flags & 2 else os.O_RDONLY)
        fl |= (os.O_CREAT if flags & 4 else 0) | (os.O_EXCL if flags & 8 else 0) | (os.O_TRUNC if flags & 0x10 else 0)
        fl |= os.O_APPEND if flags & 0x20 else 0
        path = self.path(p)

        def op():
            fd = os.open(path, fl, 0o666)
            self.fds[fd] = path
            return fd
        return self.fs(op)

    def h_fs_read(self, fd, b, n, *_):
        def op():
            d = os.read(fd, n)
            self.uc.mem_write(b, d)
            return len(d)
        return self.fs(op)

    def h_fs_write(self, fd, b, n, *_):
        return self.fs(lambda: os.write(fd, bytes(self.uc.mem_read(b, n))))

    def h_fs_pread(self, fd, b, n, off, *_):
        def op():
            d = os.pread(fd, n, off)
            self.uc.mem_write(b, d)
            return len(d)
        return self.fs(op)

    def h_fs_pwrite(self, fd, b, n, off, *_):
        return self.fs(lambda: os.pwrite(fd, bytes(self.uc.mem_read(b, n)), off))

    def h_fs_seek(self, fd, off, whence, *_):
        return self.fs(lambda: os.lseek(fd, struct.unpack("<q", struct.pack("<Q", off))[0], whence))

    def h_fs_truncate(self, fd, size, *_):
        return self.fs(lambda: os.ftruncate(fd, size))

    def h_fs_sync(self, fd, *_):
        return self.fs(lambda: os.fsync(fd))

    def put_stat(self, st, out):
        import stat as S
        self.uc.mem_write(out, struct.pack("<IIQqqq", int(S.S_ISDIR(st.st_mode)), int(not st.st_mode & 0o200),
                                           st.st_size, int(st.st_mtime * 1e6), int(st.st_atime * 1e6),
                                           int(st.st_ctime * 1e6)))

    def h_fs_fstat(self, fd, out, *_):
        return self.fs(lambda: self.put_stat(os.fstat(fd), out))

    def h_fs_close(self, fd, *_):
        self.fds.pop(fd, None)
        return self.fs(lambda: os.close(fd))

    def h_fs_stat(self, p, out, *_):
        path = self.path(p)
        return self.fs(lambda: self.put_stat(os.stat(path), out))

    def h_fs_mkdir(self, p, *_):
        path = self.path(p)
        return self.fs(lambda: os.mkdir(path))

    def h_fs_rmdir(self, p, *_):
        path = self.path(p)
        return self.fs(lambda: os.rmdir(path))

    def h_fs_remove(self, p, *_):
        path = self.path(p)
        return self.fs(lambda: os.remove(path))

    def h_fs_rename(self, a, b, *_):
        pa, pb = self.path(a), self.path(b)
        return self.fs(lambda: os.replace(pa, pb))

    def h_fs_dir_open(self, p, *_):
        try:
            names = sorted(os.listdir(self.path(p)))
        except OSError:
            return 0
        d = self.next_dir
        self.next_dir += 1
        self.dirs[d] = names
        return d

    def h_fs_dir_next(self, d, buf, cap, *_):
        names = self.dirs.get(d)
        if not names:
            return 0
        n = names.pop(0).encode()[:cap - 1]
        self.uc.mem_write(buf, n + b"\0")
        return len(n)

    def h_fs_dir_close(self, d, *_):
        self.dirs.pop(d, None)
        return 0

    # v3 audio / input / memory / extensions
    def h_audio_open(self, rate, period, *_):
        self.audio_period = period
        return 0 if rate == 48000 else U64

    def h_audio_submit(self, pcm, frames, *_):
        self.audio_frames = getattr(self, "audio_frames", 0) + frames
        return (("sleep", self.now_ns() + frames * 1_000_000_000 // 48000), 0)

    def h_audio_close(self, *_):
        return 0

    def h_pad_read(self, i, out, *_):
        self.uc.mem_write(out, bytes(32))
        return 0

    def h_pad_vibrate(self, *_):
        return 0

    def h_input_poll(self, *_):
        return 0

    def h_map_fixed(self, addr, size, src, *_):
        try:
            self.uc.mem_map(addr, size, UC_PROT_READ | UC_PROT_WRITE)
        except UcError:
            return 1
        self.mapped_fixed = getattr(self, "mapped_fixed", []) + [(addr, size)]
        return 0

    def h_ext_call(self, module, fn, args, nargs, *_):
        """FNVNX_EXT_PLATFORM (11): the host's platform services; the CPU boost is only logged here"""
        if module == 11 and fn == 1:   # FNVNX_PLAT_CPU_BOOST(mode)
            mode = struct.unpack("<Q", self.uc.mem_read(args, 8))[0] if nargs >= 1 and args else 0
            on = getattr(self, "boost_loading", True)
            self.boosts = getattr(self, "boosts", []) + [(mode, on)]
            if len(self.boosts) <= 40:
                print(f"   cpu boost: {'FastLoad' if mode else 'Normal'}"
                      f"{'' if on else ' (off: boost_loading=0)'} (thread {self.cur.tid if self.cur else '?'})")
            return 0 if on else 1
        return 0xFFFFFFFFFFFFFF01

    # -- crash trace: PC/LR and the frame-record chain (x29), symbolized with --elf (S2's __fnv_<addr> aliases
    # give the original FalloutNV.exe address of each native function)
    def load_symbols(self, elf):
        import bisect
        import subprocess
        out = subprocess.run(["aarch64-linux-gnu-nm", "-n", "--defined-only", elf], capture_output=True, text=True).stdout
        by_addr = {}
        self.data_syms = {}
        for line in out.splitlines():
            f = line.split()
            if len(f) == 3 and f[1] in "bBdD" and f[2] in ("g_fnvnx_xc_current", "fnvnx_trap_stats"):
                self.data_syms[f[2]] = int(f[0], 16)
            if len(f) == 3 and f[1] in "tTwW":
                a = int(f[0], 16)
                by_addr.setdefault(a, []).append(f[2])
                self.code_syms = getattr(self, "code_syms", {})
                self.code_syms.setdefault(f[2], a)
        self.sym_addrs = sorted(by_addr)
        self.sym_names = [by_addr[a] for a in self.sym_addrs]
        self._bisect = bisect.bisect_right

    def xreg(self, i):
        import unicorn.arm64_const as c
        return self.uc.reg_read(getattr(c, f"UC_ARM64_REG_X{i}")) if i < 31 else 0

    def trap_text(self):
        """At S2's trap stubs (fnvnx_vslot_trap: a virtual function defined nowhere, fnvnx_special_trap): the text the
        stub passes in x1, e.g. 'virtual function defined nowhere: <symbol> (slot <k> of <vtable>)'; '' elsewhere."""
        name = self.sym(self.uc.reg_read(UC_ARM64_REG_PC))
        if not re.search(r" fnvnx_(vslot|special)_trap\+0x0$", name):
            return ""
        try:
            return bytes(self.uc.mem_read(self.xreg(1) & 0xFFFFFFFF, 240)).split(b"\0")[0].decode("latin-1")
        except UcError:
            return ""

    def null_call(self):
        """At a fetch fault from address 0: where the call came from and where the null was loaded from.
        Returns '<caller> | <what>' with what = 'vslot <k> of vtable <addr> (this <x0>)' for a virtual call
        (the vptr of the object in x0 is the table the slot came from), 'loaded from <addr>' for a function
        pointer read from memory, or 'via br (tail call)' when the call site cannot be seen."""
        uc = self.uc
        lr = uc.reg_read(UC_ARM64_REG_LR)
        try:
            (call,) = struct.unpack("<I", uc.mem_read(lr - 4, 4))
        except UcError:
            return f"{lr:#x} | ?"
        caller = re.sub(r"^0x[0-9a-f]+ ", "", self.sym(lr - 4))
        if call & 0xFFFFFC1F != 0xD63F0000:   # not blr xN: the null came through br / ret
            return f"{caller} | via br (tail call) or return"
        n = (call >> 5) & 31
        for back in range(2, 12):
            try:
                (w,) = struct.unpack("<I", uc.mem_read(lr - 4 * back, 4))
            except UcError:
                break
            if w & 0xFFC00000 == 0xB9400000 and w & 31 == n:   # ldr wN, [xM, #imm]
                m, imm = (w >> 5) & 31, ((w >> 10) & 0xFFF) * 4
                base = self.xreg(m) & 0xFFFFFFFF
                this = self.xreg(0) & 0xFFFFFFFF
                try:
                    (vptr,) = struct.unpack("<I", uc.mem_read(this, 4)) if this else (None,)
                except UcError:
                    vptr = None
                if m == n and vptr is not None:   # ldr wN, [xN, #imm]: the base is gone; a virtual call reads
                    try:                          # the slot from this's vptr, check that one
                        (slot,) = struct.unpack("<I", uc.mem_read(vptr + imm, 4))
                    except UcError:
                        slot = None
                    if slot == 0 or slot == uc.reg_read(UC_ARM64_REG_PC) & 0xFFFFFFFF:   # (xN now holds
                        base = vptr                                                      # the loaded slot)
                where = "image" if 0x00400000 <= base < 0x02100000 else "native"
                if vptr == base:
                    return f"{caller} | vslot {imm // 4} of {where} vtable {base:#010x}"
                return f"{caller} | loaded from {where} {base + imm:#010x}"
            if (w & 31) == n and w & 0x1F000000 in (0x11000000, 0x0B000000, 0x2A000000):   # add/orr/mov into xN
                break
        return f"{caller} | register x{n} (source not found)"

    # -- fault stubs (--stub-faults): known faults the boot should run past while their fixes are pending. Each
    # listed function returns 0 when a fault happens anywhere below it: its entry saves the registers (the caller's
    # callee-saved registers and SP, per AAPCS64), the fault restores them and resumes at the return address.
    # A line 'before-winmain <function> [x0]' instead calls that function once, right before WinMain, for an
    # initializer the decomp lacks (one whose effect the boot needs).
    def code_addr(self, n):
        key = f"__fnv_{int(n, 16):08x}" if re.fullmatch(r"0x[0-9a-fA-F]+", n) else n
        link = self.code_syms.get(key)
        if link is None:
            return None
        return link - self.link_base + self.base if self.relocatable else link

    def install_fault_stubs(self, path):
        self.stub_guards, self.stubbed, self.stub_returns = {}, [], set()
        stubs, self.pre_calls = parse_fault_table(path)
        names, actions = [n for n, _ in stubs], dict(stubs)
        for n in names:
            a = self.code_addr(n)
            if a is None:
                print(f"   stub-faults: {n}: not in the link")
                continue
            self.uc.hook_add(UC_HOOK_CODE, self.on_stub_entry, user_data=(n, actions[n]), begin=a, end=a)
        print(f"   stub-faults: {len(names)} functions stubbed, {len(self.pre_calls)} called before WinMain")
        winmain = self.code_addr("0x0086a850")
        if self.pre_calls and winmain is not None:
            self.pre_ctx = None
            self.uc.hook_add(UC_HOOK_CODE, self.on_winmain_entry, begin=winmain, end=winmain)

    def on_winmain_entry(self, uc, addr, size, _):
        """WinMain's first instruction: run the before-winmain calls one after the other, each returning to this
        address, then restore WinMain's arguments and go on."""
        if self.pre_calls is None:
            return
        if self.pre_ctx is None:
            self.pre_ctx = uc.context_save()
        while self.pre_calls:
            n, x0 = self.pre_calls.pop(0)
            fn = self.code_addr(n)
            if fn is None:
                print(f"   before-winmain: {n}: not in the link")
                continue
            print(f"   before-winmain: calling {self.sym(fn)} (x0 {x0:#x})")
            uc.reg_write(UC_ARM64_REG_X0, x0)
            uc.reg_write(UC_ARM64_REG_LR, addr)
            uc.reg_write(UC_ARM64_REG_PC, fn)
            return
        self.pre_calls = None
        uc.context_restore(self.pre_ctx)

    def on_stub_entry(self, uc, addr, size, data):
        if data[1].startswith("use-malloc:x") and "malloc" in self.code_syms:   # replaced on every call
            self.replaced = getattr(self, "replaced", {})
            self.replaced[data[0]] = self.replaced.get(data[0], 0) + 1
            uc.reg_write(UC_ARM64_REG_X0, self.xreg(int(data[1][12:])) & 0xFFFFFFFF)
            uc.reg_write(UC_ARM64_REG_PC, self.code_addr("malloc"))   # returns to the caller's lr
            return
        tid = self.cur.tid if self.cur else 1
        sp, lr = uc.reg_read(UC_ARM64_REG_SP), uc.reg_read(UC_ARM64_REG_LR)
        if data[1] == "return-this":   # returns x0 as it came in (an MSVC constructor called as a function)
            self.ret_this = getattr(self, "ret_this", {})
            st = [g for g in self.ret_this.get(tid, []) if g[0] > sp]
            st.append((sp, lr, self.xreg(0), data[0]))
            self.ret_this[tid] = st
            if ("this", lr) not in self.stub_returns:
                self.stub_returns.add(("this", lr))
                uc.hook_add(UC_HOOK_CODE, self.on_this_return, begin=lr, end=lr)
            return
        st = [g for g in self.stub_guards.get(tid, []) if g[0] >= sp]   # drop guards left by an unwind
        st.append((sp, uc.context_save(), lr, data[0], data[1]))
        self.stub_guards[tid] = st
        if lr not in self.stub_returns:   # the guard ends when its call returns (also after a tail call)
            self.stub_returns.add(lr)
            uc.hook_add(UC_HOOK_CODE, self.on_stub_return, begin=lr, end=lr)

    def on_this_return(self, uc, addr, size, _):
        tid = self.cur.tid if self.cur else 1
        st = self.ret_this.get(tid)
        if st and st[-1][1] == addr and st[-1][0] == uc.reg_read(UC_ARM64_REG_SP):
            _, _, this, name = st.pop()
            if uc.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF != this:
                self.replaced = getattr(self, "replaced", {})
                self.replaced[name + " return-this"] = self.replaced.get(name + " return-this", 0) + 1
            uc.reg_write(UC_ARM64_REG_X0, this)

    def on_stub_return(self, uc, addr, size, _):
        tid = self.cur.tid if self.cur else 1
        sp = uc.reg_read(UC_ARM64_REG_SP)
        st = self.stub_guards.get(tid)
        if st and st[-1][2] == addr and st[-1][0] == sp:
            st.pop()

    def stub_fault(self, tid, err):
        """the innermost live fault stub of the thread, restored: (resume pc, x0), its return address and 0, or for
        'malloc:xN' the guest's malloc with the size from xN, returning to the same address; None if none"""
        st = getattr(self, "stub_guards", {}).get(tid)
        sp = self.uc.reg_read(UC_ARM64_REG_SP)
        while st and st[-1][0] < sp:   # guards whose frames an unwind already left
            st.pop()
        if not st:
            return None
        g_sp, ctx, lr, name, action = st.pop()
        pc = self.uc.reg_read(UC_ARM64_REG_PC)
        bad_call = pc == 0 or "fetch" in err.lower()
        where = self.sym(pc) + (" | " + self.null_call() if bad_call else "")
        if self.trap_text():
            where += " | " + self.trap_text()
        self.uc.context_restore(ctx)
        resume, result, how = lr, 0, "returns 0"
        if action.startswith("malloc:x") and "malloc" in self.code_syms:
            result = self.xreg(int(action[8:])) & 0xFFFFFFFF
            m = self.code_syms["malloc"]
            resume = m - self.link_base + self.base if self.relocatable else m
            how = f"returns malloc({result})"
        self.stubbed.append((name, err, where))
        if len(self.stubbed) <= 30:
            print(f"   stubbed fault: {name} {how} ({err} at {where})")
        return resume, result

    def stub_summary(self):
        import collections
        c = collections.Counter((n, re.sub(r" \(UC_ERR_\w+\)", "", e), re.sub(r"\+0x[0-9a-f]+", "", w))
                                for n, e, w in getattr(self, "stubbed", []))
        return ([f"stubbed-fault\t{k}\t{n}\t{e}\t{w}" for (n, e, w), k in c.most_common()]
                + [f"replaced-call\t{k}\t{n}" + ("" if n.endswith("return-this") else " use-malloc")
                   for n, k in getattr(self, "replaced", {}).items()])

    def skip_summary(self, top=60):
        """All skipped initializers grouped by (fault, faulting function):
        skip-summary <count> <fault> <faulting function> <first initializer>"""
        import collections
        groups, first = collections.Counter(), {}
        for init, pc, err in getattr(self, "skipped", None) or []:
            site = re.sub(r"\+0x[0-9a-f]+$", "", self.sym(pc).split(" ", 1)[-1])
            if site == re.sub(r"\+0x[0-9a-f]+$", "", self.sym(init).split(" ", 1)[-1]):
                site = "(in the initializer itself)"
            try:
                (w,) = struct.unpack("<I", self.uc.mem_read(pc, 4))
                if w & 0xFFE0001F == 0xD4200000:   # brk #imm: __builtin_trap()
                    site = f"brk #{(w >> 5) & 0xFFFF:#x} " + site
            except UcError:
                pass
            err = re.sub(r" \(UC_ERR_\w+\)", "", err)
            if " | " in err:   # null call: group by its call site and pointer source instead of pc 0
                err, site = err.split(" | ", 1)
                init_fn = re.sub(r"\+0x[0-9a-f]+$", "", self.sym(init).split(" ", 1)[-1])
                site = re.sub(r"^" + re.escape(init_fn) + r"\+0x[0-9a-f]+", "(in the initializer itself)", site)
                site = re.sub(r"(\]|\w)\+0x[0-9a-f]+ \|", r"\1 |", site)
            key = (err, site)
            groups[key] += 1
            first.setdefault(key, self.sym(init).split(" ", 1)[-1])
        return [f"skip-summary\t{n}\t{k[0]}\t{k[1]}\t{first[k]}" for k, n in groups.most_common(top)]

    def trap_table(self):
        """Lines of the guest's trap statistics (toolchain/game/boot.cpp fnvnx_trap_stats) in first-hit order:
        trap-table <seq> <hits> <init|winmain> <sym|ptr|iat> <name or orig address> <caller of the first hit>."""
        a = getattr(self, "data_syms", {}).get("fnvnx_trap_stats")
        if a is None:
            return []
        a = a - self.link_base + self.base if self.relocatable else a
        magic, n, cap, seq = struct.unpack("<4I", self.uc.mem_read(a, 16))
        if magic != 0x54564E46 or cap > 1 << 16:
            return []
        raw = self.uc.mem_read(a + 16, cap * 24)
        recs = []
        for i in range(cap):
            key, kind, hits, first, caller, phase = struct.unpack_from("<6I", raw, i * 24)
            if hits:
                recs.append((first, hits, phase, kind, key, caller))
        out = [f"== trap table: {len(recs)} keys, {seq} hits"]
        for first, hits, phase, kind, key, caller in sorted(recs):
            if kind == 0:
                try:
                    name = self.rs(key)
                except UcError:
                    name = f"?{key:#x}"
            else:
                name = f"{key:#010x}"
            k = ("sym", "ptr", "iat")[kind] if kind < 3 else str(kind)
            who = self.sym(caller) if caller else "-"
            out.append(f"trap-table\t{first}\t{hits}\t{'winmain' if phase else 'init'}\t{k}\t{name}\t{who}")
        return out

    # -- boot milestones (--milestones, default tests/s5/milestones.txt): how far the game got, from events the
    # boot cannot fake. 'call F' is reached when F's first instruction runs (F an original address 0x... or
    # 're:<regex>' over the ELF's code symbols, for native code such as the D3D9 device); 'store A' when a non-zero
    # word is written to the data-window address A; 'store *A+N' when one is written to (the pointer stored at A)+N.
    def install_milestones(self, path):
        self.miles, self.mile_order = {}, []
        for name, kind, target in parse_milestones(path):
            if name not in self.mile_order:
                self.mile_order.append(name)
            if kind == "call":
                if target.startswith("re:"):
                    rx = re.compile(target[3:])
                    addrs = sorted({self.code_addr(n) for n in self.code_syms if rx.search(n)})
                else:
                    addrs = [a for a in [self.code_addr(target)] if a is not None]
                for a in addrs:
                    self.uc.hook_add(UC_HOOK_CODE, self.on_mile_call, user_data=name, begin=a, end=a)
                if not addrs:
                    print(f"   milestones: {name}: {target} is not in the link")
            else:
                m = re.fullmatch(r"(\*?)(0x[0-9a-fA-F]+)(?:\+(0x[0-9a-fA-F]+|\d+))?", target)
                addr, off = int(m.group(2), 16), int(m.group(3) or "0", 0)
                # The hook range starts STORE_REACH bytes below the word: a wider store (str x at want-4, a q store
                # Unicorn splits into 8-byte pieces) covers it from below; stored_word keeps only full covers.
                if m.group(1):
                    self.uc.hook_add(UC_HOOK_MEM_WRITE, self.on_mile_base, user_data=(name, target, addr, off),
                                     begin=addr - self.STORE_REACH, end=addr + 3)
                else:
                    self.uc.hook_add(UC_HOOK_MEM_WRITE, self.on_mile_store, user_data=(name, target, addr),
                                     begin=addr - self.STORE_REACH, end=addr + 3)
        print(f"   milestones: {', '.join(self.mile_order)}")

    def mile_hit(self, name, how, at=None):
        if name in self.miles:
            return
        lr = self.uc.reg_read(UC_ARM64_REG_LR)
        where = self.sym(self.uc.reg_read(UC_ARM64_REG_PC) if at is None else at).split(" ", 1)[-1]
        self.miles[name] = {"how": how, "where": where, "caller": self.sym(lr).split(" ", 1)[-1],
                            "seq": len(self.miles) + 1, "host_calls": self.calls,
                            "thread": self.cur.tid if self.cur else 1}
        print(f"   milestone: {name} ({how} at {where}, caller {self.miles[name]['caller']})")

    def on_mile_call(self, uc, addr, size, name):
        if name not in self.miles:
            self.mile_hit(name, "call", at=addr)

    def on_mile_store(self, uc, access, addr, size, value, data):
        name, target, want = data
        if name not in self.miles and self.stored_word(addr, size, value, want):
            self.mile_hit(name, f"store {target}")

    STORE_REACH = 12   # a 16-byte store at want-12 still covers the word at want

    def on_mile_base(self, uc, access, addr, size, value, data):
        """a store to A of 'store *A+N': watch (the new pointer)+N, which may hold its value already. The object the
        old pointer named is no longer watched (freed and reused memory must not count)."""
        name, target, base, off = data
        if name in self.miles or not (addr <= base < addr + size):
            return
        p = self.stored_word(addr, size, value, base, nonzero=False)
        if p is None:   # a store that covers only part of A: not a pointer store
            return
        held = getattr(self, "mile_obj", {}).get(name)
        if held and held[0] == p:
            return
        if held:
            uc.hook_del(held[1])
            self.mile_obj.pop(name)
        if not p:
            return
        try:
            (cur,) = struct.unpack("<I", uc.mem_read(p + off, 4))
        except UcError:
            cur = 0
        if cur:
            self.mile_hit(name, f"store {target} (already set when the pointer was stored)")
        else:
            h = uc.hook_add(UC_HOOK_MEM_WRITE, self.on_mile_store, user_data=(name, target, p + off),
                            begin=p + off - self.STORE_REACH, end=p + off + 3)
            self.__dict__.setdefault("mile_obj", {})[name] = (p, h)

    @staticmethod
    def stored_word(addr, size, value, want, nonzero=True):
        """the 32-bit word a store of size bytes at addr leaves at want (None if it does not cover all of it)"""
        if size not in (4, 8, 16) or not addr <= want < addr + size or want + 4 > addr + size:
            return None
        w = (value >> (8 * (want - addr))) & 0xFFFFFFFF
        return (w if w or not nonzero else None)

    def milestone_lines(self):
        """milestone <name> <reached|-> <how> <where> <caller>, in the table's order"""
        out = []
        for n in getattr(self, "mile_order", []):
            m = self.miles.get(n)
            out.append(f"milestone\t{n}\t" + ("\t".join(["reached", m["how"], m["where"], m["caller"]]) if m else "-"))
        return out

    def summary(self, stop, rc=None):
        """the machine-readable boot summary (--summary-json): milestones in order, the highest reached, the stop"""
        order = getattr(self, "mile_order", [])
        reached = [n for n in order if n in self.miles]
        best = None
        for n in order:   # the last milestone in table order that was reached (later ones may come without earlier)
            if n in self.miles:
                best = n
        msgs = [l.strip() for l in self.console.decode(errors="replace").splitlines()
                if re.search(r"\[fnvnx\] (MessageBox|ExitProcess|FatalAppExit)", l)]
        tr = getattr(self, "last_trace", None)
        return {
            "schema": "fnvnx-boot-summary/1",
            "milestones": [dict(name=n, reached=n in self.miles, **self.miles.get(n, {})) for n in order],
            "milestone": best, "reached": len(reached), "of": len(order),
            "winmain": "calling WinMain" in self.console.decode(errors="replace"),
            "stop": {"reason": stop, "exit_code": rc, "trace": tr, "messages": msgs[:20]},
            "initializers_skipped": len(getattr(self, "skipped", None) or []),
            "stubbed_faults": len(getattr(self, "stubbed", [])),
            "host_calls": self.calls, "threads": len(self.threads),
            "peak_heap_bytes": self.heap_high_water(),
        }

    def heap_high_water(self):
        """bytes from the start of the guest heap (the host's malloc arena, mapped zeroed) to its last non-zero byte:
        the most the boot's heap ever reached, as far as memory it wrote shows (R0's boot_peak_heap_bytes)"""
        try:
            return len(bytes(self.uc.mem_read(self.heap, HEAP_SIZE)).rstrip(b"\0"))
        except UcError:
            return None

    # -- guarded initializer calls (--skip-faulting-init): every indirect call (blr) made by the initializer
    # runners is a checkpoint; a fault below one restores the registers of the call and returns 0 from it, so a
    # boot without game data can be traced past initializers that fault on the empty image.
    GUARD_FUNCS = ("__libc_init_array", "call", "fnvnx_xc_run_address_order", "fnvnx_xc_run_image_order")

    def install_guards(self):
        import bisect
        self.guard = {}
        self.skipped = []
        sites = []
        names_at = {a: ns for a, ns in zip(self.sym_addrs, self.sym_names)}
        for i, a in enumerate(self.sym_addrs):
            if not any(n in self.GUARD_FUNCS for n in self.sym_names[i]):
                continue
            end = self.sym_addrs[i + 1] if i + 1 < len(self.sym_addrs) else a + 0x400
            for off in range(a - self.link_base, min(end, a + 0x2000) - self.link_base, 4):
                if 0 <= off < len(self.text_bytes) - 4:
                    (w,) = struct.unpack_from("<I", self.text_bytes, off)
                    if w & 0xFFFFFC1F == 0xD63F0000:   # blr xN
                        sites.append((off + self.base, (w >> 5) & 31))
        for addr, reg in sites:
            self.uc.hook_add(UC_HOOK_CODE, self.on_guard_call, user_data=reg, begin=addr, end=addr)
            self.uc.hook_add(UC_HOOK_CODE, self.on_guard_return, begin=addr + 4, end=addr + 4)
        print(f"   guarding {len(sites)} indirect call sites of the initializer runners")

    def on_guard_call(self, uc, addr, size, reg):
        from unicorn.arm64_const import UC_ARM64_REG_X0 as X0
        target = uc.reg_read(X0 + reg) if reg <= 28 else 0
        self.guard[self.cur.tid] = (uc.context_save(), addr + 4, target)

    def on_guard_return(self, uc, addr, size, _):
        self.guard.pop(self.cur.tid, None)

    def sym(self, a):
        if not getattr(self, "sym_addrs", None):
            return f"{a:#x}"
        if self.base <= a < self.base + self.size:   # runtime -> link address (relocatable guest)
            a = a - self.base + self.link_base
        i = self._bisect(self.sym_addrs, a) - 1
        if i < 0:
            return f"{a:#x}"
        names = self.sym_names[i]
        if a - self.sym_addrs[i] > 0x100000:
            return f"{a:#x}"
        orig = [n for n in names if n.startswith("__fnv_") and len(n) >= 14]
        native = [n for n in names if not n.startswith("__fnv_")]
        label = (native[0] if native else names[0])
        if orig:
            label += f" [orig {orig[0][6:14]}]"
        return f"{a:#x} {label}+{a - self.sym_addrs[i]:#x}"

    def trace(self, why):
        uc = self.uc
        pc, lr, fp = uc.reg_read(UC_ARM64_REG_PC), uc.reg_read(UC_ARM64_REG_LR), uc.reg_read(UC_ARM64_REG_X29)
        print(f"== trace ({why}), thread {self.cur.tid if self.cur else '?'}")
        self.last_trace = tr = {"why": why, "thread": self.cur.tid if self.cur else None,
                                "pc": self.sym(pc), "lr": self.sym(lr), "trap": self.trap_text() or None,
                                "call": self.null_call() if pc == 0 or "fetch" in why.lower() else None, "frames": []}
        xa = getattr(self, "data_syms", {}).get("g_fnvnx_xc_current")
        if xa is not None:
            xa = xa - self.link_base + self.base if self.relocatable else xa
            (cur,) = struct.unpack("<I", uc.mem_read(xa, 4))
            if cur:   # S10's xc runner does not clear it: after the initializers this is the last one run
                print(f"   S10 initializer (running or last run): orig {cur:#010x}")
        print(f"   pc  {self.sym(pc)}")
        if self.trap_text():
            print(f"   trap {self.trap_text()}")
        if pc == 0 or "fetch" in why.lower():   # a call through a bad pointer: where it came from
            print(f"   call {self.null_call()}")
        print(f"   lr  {self.sym(lr)}")
        print("   " + " ".join(f"x{i} {self.xreg(i):#x}" for i in range(8)))
        print("   " + " ".join(f"x{i} {self.xreg(i):#x}" for i in range(19, 29)))
        for i in range(40):
            if not fp or fp & 7:
                break
            try:
                nfp, ret = struct.unpack("<QQ", uc.mem_read(fp, 16))
            except UcError:
                break
            print(f"   #{i:<2} {self.sym(ret)}")
            tr["frames"].append(self.sym(ret))
            if nfp <= fp:
                break
            fp = nfp

    # -- scheduler
    def pick(self):
        live = [self.threads[t] for t in self.order]
        for _ in range(2):
            n = len(live)
            start = (self.order.index(self.last) + 1) if self.last in self.order else 0
            for i in range(n):
                t = live[(start + i) % n]
                if t.state == "ready":
                    return t
            # nothing ready: advance virtual time to the earliest deadline
            dl = [(t.wait[-1], t) for t in live if t.state == "blocked" and t.wait[0] in ("sleep", "addr")
                  and t.wait[-1] is not None]
            if not dl:
                return None
            when = min(d for d, _ in dl)
            self.tick = max(self.tick, when * TICK_FREQ // 1_000_000_000 + 1)
            for d, t in dl:
                if d <= when:
                    t.state = "ready"
                    t.result = 1 if t.wait[0] == "addr" else 0
        return None

    def run(self):
        uc = self.uc
        main = Thread(1)
        self.threads[1], self.order, self.last = main, [1], None
        uc.reg_write(UC_ARM64_REG_SP, self.stack_for(1))
        uc.reg_write(UC_ARM64_REG_X0, HOST_PAGE)
        uc.reg_write(UC_ARM64_REG_LR, RETURN_ADDR)
        uc.reg_write(UC_ARM64_REG_TPIDR_EL0, 0xDEAD0000)
        main.ctx, main.resume_pc, main.state, main.result = uc.context_save(), self.base, "ready", HOST_PAGE
        budget = getattr(self, "budget", 400_000_000)
        while self.exit_code is None:
            t = self.pick()
            if t is None:
                states = {x.tid: (x.state, x.wait) for x in self.threads.values() if x.state != "done"}
                if states:
                    raise SystemExit(f"deadlock: {states}")
                break
            if self.last is not None and self.last != t.tid:
                self.ctx_switches += 1
            self.last, self.cur = t.tid, t
            uc.context_restore(t.ctx)
            uc.reg_write(UC_ARM64_REG_X0, t.result & U64)
            t.state = "running"
            try:
                uc.emu_start(t.resume_pc, RETURN_ADDR, count=budget)
            except UcError as e:
                stub = self.stub_fault(t.tid, str(e))
                if stub is not None:   # a known fault: the stubbed function returns and the thread goes on
                    t.ctx, t.state = uc.context_save(), "ready"
                    t.resume_pc, t.result = stub
                    self.cur = None
                    continue
                g = getattr(self, "guard", {}).pop(t.tid, None)
                if g is not None and len(self.skipped) < getattr(self, "max_skips", 100000):
                    pc = uc.reg_read(UC_ARM64_REG_PC)
                    why = str(e)
                    if pc == 0 or "fetch" in why.lower():   # a call through a bad pointer: call site, source
                        why += " | " + self.null_call()
                    elif self.trap_text():
                        why += " | " + self.sym(uc.reg_read(UC_ARM64_REG_LR)).split(" ", 1)[-1] + " | " + self.trap_text()
                    self.skipped.append((g[2], pc, why))
                    if len(self.skipped) <= 30:
                        print(f"   skipped initializer {self.sym(g[2])}: {e} at {self.sym(pc)}")
                    uc.context_restore(g[0])
                    uc.reg_write(UC_ARM64_REG_X0, 0)
                    t.ctx, t.resume_pc, t.result, t.state = uc.context_save(), g[1], 0, "ready"
                    self.cur = None
                    continue
                self.trace(f"fault: {e}")
                trap = self.trap_text()
                raise SystemExit(f"emulation fault at pc={uc.reg_read(UC_ARM64_REG_PC):#x} thread {t.tid}: {e}"
                                 + (f": {trap}" if trap else ""))
            if self.exit_code is not None:
                self.tp_after = 0xDEAD0000 if t.tid != 1 else uc.reg_read(UC_ARM64_REG_TPIDR_EL0)
                break
            if self.cur is t:      # stopped by reaching RETURN_ADDR (main returned) or the budget
                if uc.reg_read(UC_ARM64_REG_PC) == RETURN_ADDR and t.tid == 1:
                    self.exit_code = uc.reg_read(UC_ARM64_REG_X0)
                    self.tp_after = uc.reg_read(UC_ARM64_REG_TPIDR_EL0)
                    break
                self.trace("instruction budget exhausted")
                raise SystemExit(f"thread {t.tid} ran out of instruction budget at {uc.reg_read(UC_ARM64_REG_PC):#x}")
        return self.exit_code


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("nso")
    ap.add_argument("--expect", default="fnvnx guest: OK")
    ap.add_argument("--root", help="host directory for sdmc:/ (default: a fresh temp dir)")
    ap.add_argument("--file", action="append", default=[], help="NAME=PATH: copy PATH to sdmc:/NAME first")
    ap.add_argument("--elf", help="guest ELF for symbolized crash traces")
    ap.add_argument("--load-base", type=lambda v: int(v, 0), help="where to map a relocatable guest")
    ap.add_argument("--skip-faulting-init", action="store_true",
                    help="trace mode: return 0 from an initializer that faults (needs --elf)")
    ap.add_argument("--budget", type=int, default=400_000_000, help="instructions per scheduling slice")
    ap.add_argument("--core3", action="store_true", help="the host's core 3 opt-in (fnvnx.ini core3=1)")
    ap.add_argument("--no-boost", action="store_true", help="the host's boost_loading=0")
    ap.add_argument("--stub-faults", metavar="FILE", help="functions (names or 0xORIG, one per line) that return 0 "
                    "when a fault happens below them, e.g. tests/s5/known_faults.txt; needs --elf")
    ap.add_argument("--milestones", metavar="FILE", default=MILESTONES,
                    help="boot milestones to watch (default tests/s5/milestones.txt; 'none': none); needs --elf")
    ap.add_argument("--summary-json", metavar="FILE", help="write the machine-readable boot summary here")
    ap.add_argument("--peek", action="append", default=[], metavar="ADDR[:SIZE]",
                    help="print guest memory at ADDR (SIZE bytes, default 4) when the run ends")
    a = ap.parse_args(argv)
    root = a.root or tempfile.mkdtemp(prefix="fnvnx-emu-")
    for spec in a.file:
        name, _, src = spec.partition("=")
        dst = os.path.join(root, name.lstrip("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    emu = Emu(a.nso, root, load_base=a.load_base)
    emu.budget = a.budget
    emu.core3, emu.boost_loading = a.core3, not a.no_boost
    if a.elf:
        emu.load_symbols(a.elf)
        if a.skip_faulting_init:
            emu.install_guards()
        if a.stub_faults:
            emu.install_fault_stubs(a.stub_faults)
        if a.milestones and a.milestones != "none":
            emu.install_milestones(a.milestones)
    emu.miles = getattr(emu, "miles", {})

    def write_summary(stop, rc=None):
        if a.summary_json:
            import json
            with open(a.summary_json, "w") as f:
                json.dump(emu.summary(stop, rc), f, indent=1)
                f.write("\n")
    def peeks():
        out = []
        for spec in a.peek:
            hops = len(spec) - len(spec.lstrip("*"))   # "**ADDR": follow two 32-bit pointers first
            addr, _, n = spec.lstrip("*").partition(":")
            addr, n = int(addr, 0), int(n, 0) if n else 4
            chain = []
            try:
                for _ in range(hops):
                    (nxt,) = struct.unpack("<I", emu.uc.mem_read(addr, 4))
                    chain.append(f"{addr:#010x} -> {nxt:#010x}")
                    addr = nxt
            except UcError as e:
                out.append(f"peek\t{spec}\t{' / '.join(chain)} / unreadable ({e})")
                continue
            if chain:
                out.append(f"peek\t{spec}\t{' / '.join(chain)}\t{emu.sym(addr)}")
            try:
                b = bytes(emu.uc.mem_read(addr, n))
                val = f"{int.from_bytes(b, 'little'):#x}" if n in (1, 2, 4, 8) else b.hex()
            except UcError as e:
                val = f"unreadable ({e})"
            out.append(f"peek\t{addr:#010x}\t{n}\t{val}")
        return out

    def stopped(why):
        print("   " + emu.console.decode(errors="replace").rstrip().replace("\n", "\n   "))
        if getattr(emu, "skipped", None) is not None:
            print(f"   initializers skipped after a fault: {len(emu.skipped)}")
        for line in (emu.skip_summary() + emu.stub_summary() + emu.trap_table() + peeks() + emu.placements()
                     + emu.milestone_lines()):
            print(line)
        print(f"STOPPED: {why}")
        write_summary(f"STOPPED: {why}")
        return 1
    try:
        rc = emu.run()
    except SystemExit as e:
        return stopped(e)
    except Exception as e:   # the Python host stand-in failed on a request: still show where the guest was
        traceback.print_exc()
        emu.trace(f"host stand-in raised {e!r}")
        return stopped(f"host stand-in error: {e!r}")
    out = emu.console.decode(errors="replace")
    tp_ok = getattr(emu, "tp_after", None) == 0xDEAD0000
    print(f"== guest at {emu.base:#010x} (link base {emu.link_base:#x}, {('pinned' if emu.pinned else 'relocatable') if emu.relocatable else 'fixed'}): rc={rc:#x} host_calls={emu.calls} threads={len(emu.threads)} "
          f"switches={emu.ctx_switches} blocking_waits={emu.addr_waits} tpidr_el0 restored={tp_ok}")
    for line in emu.debug:
        print(f"   [debug] {line}")
    if getattr(emu, "skipped", None) is not None:
        print(f"   initializers skipped after a fault: {len(emu.skipped)}")
    print("   " + out.rstrip().replace("\n", "\n   "))
    for line in (emu.skip_summary() + emu.stub_summary() + emu.trap_table() + peeks() + emu.placements()
                 + emu.milestone_lines()):
        print(line)
    ok = rc == 0 and a.expect in out and tp_ok
    write_summary(f"guest returned {rc:#x}", rc)
    print("PASS" if ok else "FAIL")
    if not a.root:
        shutil.rmtree(root, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
