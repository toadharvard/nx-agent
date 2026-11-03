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
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "toolchain", "tools"))
import elf2nso  # noqa: E402

from unicorn import UC_ARCH_ARM64, UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_MODE_ARM, UC_PROT_ALL, UC_PROT_EXEC, \
    UC_PROT_READ, UC_PROT_WRITE, Uc, UcError  # noqa: E402
from unicorn.arm64_const import UC_ARM64_REG_CPACR_EL1, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP, \
    UC_ARM64_REG_TPIDR_EL0, UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, \
    UC_ARM64_REG_X4  # noqa: E402

PAGE = 0x1000
HOST_PAGE = 0x3F000000          # host API table + stubs (below 4 GiB, as every address in a 32-bit AS)
STACKS_TOP, STACK_SIZE, MAX_THREADS = 0x3E000000, 0x40000, 32
HEAP_BASE, HEAP_SIZE = 0x30000000, 0x4000000
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
         "fs_dir_next", "fs_dir_close"]
API_SIZE = 0x30 + 8 * len(SLOTS)
U64 = (1 << 64) - 1


def align(x, a=PAGE):
    return (x + a - 1) & ~(a - 1)


def neg(e):
    return (-e) & U64


class Thread:
    def __init__(self, tid, ctx=None):
        self.tid, self.ctx = tid, ctx
        self.state = "new"          # new, ready, running, blocked, done
        self.wait = None            # ("addr", addr, deadline) / ("join", tid) / ("sleep", deadline)
        self.result = 0             # x0 to deliver when resumed
        self.resume_pc = None


class Emu:
    def __init__(self, nso_path, root, verbose=False):
        with open(nso_path, "rb") as f:
            info = elf2nso.parse_nso(f.read())
        for s in info["segments"]:
            if not s["hash_ok"]:
                raise SystemExit(f"{nso_path}: {s['name']} hash mismatch")
        text, ro, data = info["segments"]
        magic, _abi, base, size = struct.unpack_from("<IIII", text["bytes"], 8)
        if magic != 0x47584E46:
            raise SystemExit("not an fnv-nx guest (no FNXG magic at +8)")
        if size != align(data["dst"] + data["size"] + data["bss"]):
            raise SystemExit(f"guest header size {size:#x} does not match the NSO")
        if base < 0x200000 or base + size > 0x40000000 or (base < 0x02100000 and base + size > 0x00400000):
            raise SystemExit(f"guest link range {base:#x}..{base + size:#x} not allowed")
        self.base, self.size, self.root, self.verbose = base, size, root, verbose
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
        uc.mem_map(HEAP_BASE, HEAP_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        uc.mem_map(HOST_PAGE, PAGE, UC_PROT_ALL)
        self.stubs = {HOST_PAGE + 0x800 + 4 * i: name for i, name in enumerate(SLOTS)}
        api = struct.pack("<IIII", 0x48584E46, 2, API_SIZE, 0)
        api += struct.pack("<QQQQ", base, size, HEAP_BASE, HEAP_SIZE)
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
                return (out + b[:i]).decode()
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

    def h_thread_set_priority(self, *_):
        return 0

    def h_thread_set_cpu(self, *_):
        return 0

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
        budget = 400_000_000
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
                raise SystemExit(f"emulation fault at pc={uc.reg_read(UC_ARM64_REG_PC):#x} thread {t.tid}: {e}")
            if self.cur is t:      # stopped by reaching RETURN_ADDR (main returned) or the budget
                if uc.reg_read(UC_ARM64_REG_PC) == RETURN_ADDR and t.tid == 1:
                    self.exit_code = uc.reg_read(UC_ARM64_REG_X0)
                    self.tp_after = uc.reg_read(UC_ARM64_REG_TPIDR_EL0)
                    break
                raise SystemExit(f"thread {t.tid} ran out of instruction budget at {uc.reg_read(UC_ARM64_REG_PC):#x}")
        return self.exit_code


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("nso")
    ap.add_argument("--expect", default="fnvnx guest: OK")
    ap.add_argument("--root", help="host directory for sdmc:/ (default: a fresh temp dir)")
    ap.add_argument("--file", action="append", default=[], help="NAME=PATH: copy PATH to sdmc:/NAME first")
    a = ap.parse_args(argv)
    root = a.root or tempfile.mkdtemp(prefix="fnvnx-emu-")
    for spec in a.file:
        name, _, src = spec.partition("=")
        dst = os.path.join(root, name.lstrip("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    emu = Emu(a.nso, root)
    rc = emu.run()
    out = emu.console.decode(errors="replace")
    tp_ok = getattr(emu, "tp_after", None) == 0xDEAD0000
    print(f"== guest at {emu.base:#010x}: rc={rc:#x} host_calls={emu.calls} threads={len(emu.threads)} "
          f"switches={emu.ctx_switches} blocking_waits={emu.addr_waits} tpidr_el0 restored={tp_ok}")
    for line in emu.debug:
        print(f"   [debug] {line}")
    print("   " + out.rstrip().replace("\n", "\n   "))
    ok = rc == 0 and a.expect in out and tp_ok
    print("PASS" if ok else "FAIL")
    if not a.root:
        shutil.rmtree(root, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
