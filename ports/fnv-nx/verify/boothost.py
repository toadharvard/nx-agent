#!/usr/bin/env python3
"""The host services of the boot stage: the 50 h_<slot> methods of the FnvnxHostApi v3 table. Policy (address space, scheduling, ext modules) lives in bootmachine.Machine; these
methods do the argument decoding and the host-side effect. A blocking call returns (wait spec, result when resumed).

Files: guest paths go through bootpath.BootPath (sdmc: prefix, case-folded components, the game mounts; its rule is
stated in bootpath.py). BootPath(root) creates <root>/switch/fnv, as the device always has it.

Host slots whose effect is not modelled (the observable contract is kept; see HOST SLOTS in the stage report):
 output_debug_string (kept in .debug, no sink), console_flush, main_loop (always 1), buttons_down (always 0),
 thread_free, thread_set_priority / thread_set_cpu (recorded in .placement only, no placement effect),
 wall_time_us (fixed epoch + virtual ns), tz_offset_minutes (0), random64 (deterministic splitmix64, seed 0),
 memory_info (4 GiB / 3 GiB), cpu_count (3), audio_open / audio_submit / audio_close (frames counted, samples
 dropped, submit sleeps the period in virtual time), pad_read (never connected, zeros), pad_vibrate, input_poll
 (no events), ext_11 CPU_BOOST (recorded in .boosts, returns 0).
"""
import os
import stat as S
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bootpath  # noqa: E402
from bootmachine import ExtMissing  # noqa: E402

U64 = (1 << 64) - 1
WALL_EPOCH_US = 1_700_000_000_000_000
FNVNX_PLAT_CPU_BOOST = 1


def neg(e):
    return (-e) & U64


class Services:
    def __init__(self, root, game=None):
        self.m = None
        self.dp = bootpath.BootPath(root, game)
        self.debug, self.fds, self.dirs, self.next_dir = [], {}, {}, 1
        self.audio_period, self.audio_frames, self.boosts, self.placement = 0, 0, [], {}
        self.rng = 0

    def bind(self, machine):
        self.m = machine

    # -- helpers
    def cstr(self, addr):
        out = bytearray()
        while True:
            a = addr + len(out)
            b = self.m.uc.mem_read(a, min(64, 0x1000 - (a & 0xFFF)))   # never past the page: the next may be unmapped
            i = b.find(0)
            if i >= 0:
                return (out + b[:i]).decode(errors="surrogateescape")
            out += b

    def path(self, addr):
        self.last_sd = self.cstr(addr)
        return self.dp.resolve(self.last_sd)

    # -- v1
    def h_output_debug_string(self, s, n, *_):
        self.debug.append(bytes(self.m.uc.mem_read(s, n)).decode(errors="replace"))
        return 0

    def h_sleep_ns(self, ns, *_):
        return (("sleep", self.m.clock.now_ns() + ns), 0)

    def h_exit(self, code, *_):
        self.m.exit(code)
        return 0

    def h_console_write(self, b, n, *_):
        return self.m.console_write(bytes(self.m.uc.mem_read(b, n)))

    def h_console_flush(self, *_):
        return 0

    def h_main_loop(self, *_):
        return 1

    def h_buttons_down(self, *_):
        return 0

    def h_system_tick(self, *_):
        return self.m.clock.read()

    def h_system_tick_freq(self, *_):
        return self.m.clock.FREQ

    # -- v2 threads
    def h_thread_create(self, entry, arg, stack, prio, cpu, *_):
        tid = self.m.thread_create(entry, arg)
        if tid:
            self.placement[tid] = {"prio": prio, "cpu": cpu}
        return tid

    def h_thread_start(self, tid, *_):
        return self.m.thread_start(tid)

    def h_thread_join(self, tid, *_):
        t = self.m.threads.get(tid)
        return 0 if t is None or t.state == "done" else (("join", tid), 0)

    def h_thread_free(self, tid, *_):
        return 0

    def h_thread_exit(self, *_):
        return (("done",), 0)

    def h_thread_yield(self, *_):
        return (("yield",), 0)

    def _placed(self, tid):
        tid = tid or self.m.cur.tid
        return self.placement.setdefault(tid, {}) if tid in self.m.threads else None

    def h_thread_set_priority(self, tid, prio, *_):
        p = self._placed(tid)
        if p is None:
            return 0xE401   # a Horizon "invalid handle" result
        p["prio"] = prio
        return 0

    def h_thread_set_cpu(self, tid, spec, *_):
        p = self._placed(tid)
        if p is None:
            return 0xE401
        p["cpu"] = spec
        return 0

    def h_wait_on_address(self, addr, expected, timeout, *_):
        (v,) = struct.unpack("<I", self.m.uc.mem_read(addr, 4))
        if v != (expected & 0xFFFFFFFF):
            return 2
        if timeout == 0:
            return 1
        return (("addr", addr, None if timeout >= 1 << 63 else self.m.clock.now_ns() + timeout), 0)

    def h_wake_address(self, addr, count, *_):
        self.m.wake(addr, count)
        return 0

    def h_wall_time_us(self, *_):
        return WALL_EPOCH_US + self.m.clock.now_ns() // 1000

    def h_tz_offset_minutes(self, *_):
        return 0

    def h_random64(self, *_):
        """splitmix64 from seed 0 (the old driver used os.urandom, which no deterministic run can use)"""
        self.rng = (self.rng + 0x9E3779B97F4A7C15) & U64
        z = self.rng
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & U64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & U64
        return z ^ (z >> 31)

    def h_memory_info(self, out, *_):
        self.m.uc.mem_write(out, struct.pack("<QQ", 4 << 30, 3 << 30))
        return 0

    def h_cpu_count(self, *_):
        return 3

    # -- v2 files
    def fs(self, fn, call, sd=None):
        try:
            r = fn()
            return 0 if r is None else r
        except OSError as e:
            self.dp.fault(call, sd, e.errno or 5)
            return neg(e.errno or 5)

    def h_fs_open(self, p, flags, *_):
        fl = os.O_RDWR if flags & 3 == 3 else os.O_WRONLY if flags & 2 else os.O_RDONLY
        fl |= (os.O_CREAT if flags & 4 else 0) | (os.O_EXCL if flags & 8 else 0) | (os.O_TRUNC if flags & 0x10 else 0)
        fl |= os.O_APPEND if flags & 0x20 else 0
        path, sd = self.path(p), self.last_sd

        def op():
            fd = os.open(path, fl, 0o666)
            self.fds[fd] = path
            if fl & os.O_CREAT:
                self.dp.changed()
            return fd
        return self.fs(op, "open", sd)

    def _rd(self, call, fd, b, n, off=None):
        def op():
            d = os.read(fd, n) if off is None else os.pread(fd, n, off)
            self.m.uc.mem_write(b, d)
            return len(d)
        return self.fs(op, call, self.fds.get(fd))

    def h_fs_read(self, fd, b, n, *_):
        return self._rd("read", fd, b, n)

    def h_fs_pread(self, fd, b, n, off, *_):
        return self._rd("pread", fd, b, n, off)

    def h_fs_write(self, fd, b, n, *_):
        return self.fs(lambda: os.write(fd, bytes(self.m.uc.mem_read(b, n))), "write", self.fds.get(fd))

    def h_fs_pwrite(self, fd, b, n, off, *_):
        return self.fs(lambda: os.pwrite(fd, bytes(self.m.uc.mem_read(b, n)), off), "pwrite", self.fds.get(fd))

    def h_fs_seek(self, fd, off, whence, *_):
        return self.fs(lambda: os.lseek(fd, struct.unpack("<q", struct.pack("<Q", off))[0], whence), "seek")

    def h_fs_truncate(self, fd, size, *_):
        return self.fs(lambda: os.ftruncate(fd, size), "truncate")

    def h_fs_sync(self, fd, *_):
        return self.fs(lambda: os.fsync(fd), "sync")

    def put_stat(self, st, out):
        self.m.uc.mem_write(out, struct.pack("<IIQqqq", int(S.S_ISDIR(st.st_mode)), int(not st.st_mode & 0o200),
                                             st.st_size, int(st.st_mtime * 1e6), int(st.st_atime * 1e6),
                                             int(st.st_ctime * 1e6)))

    def h_fs_fstat(self, fd, out, *_):
        return self.fs(lambda: self.put_stat(os.fstat(fd), out), "fstat")

    def h_fs_close(self, fd, *_):
        self.fds.pop(fd, None)
        return self.fs(lambda: os.close(fd), "close")

    def h_fs_stat(self, p, out, *_):
        path, sd = self.path(p), self.last_sd
        return self.fs(lambda: self.put_stat(os.stat(path), out), "stat", sd)

    def _mutate(self, fn, call, sd):
        r = self.fs(fn, call, sd)
        self.dp.changed()
        return r

    def h_fs_mkdir(self, p, *_):
        path = self.path(p)
        return self._mutate(lambda: os.mkdir(path), "mkdir", self.last_sd)

    def h_fs_rmdir(self, p, *_):
        path = self.path(p)
        return self._mutate(lambda: os.rmdir(path), "rmdir", self.last_sd)

    def h_fs_remove(self, p, *_):
        path = self.path(p)
        return self._mutate(lambda: os.remove(path), "remove", self.last_sd)

    def h_fs_rename(self, a, b, *_):
        pa, sa = self.path(a), self.last_sd
        pb = self.path(b)
        return self._mutate(lambda: os.replace(pa, pb), "rename", sa)

    def h_fs_dir_open(self, p, *_):
        path, sd = self.path(p), self.last_sd
        try:
            names = sorted(os.listdir(path))
        except OSError as e:
            self.dp.fault("dir_open", sd, e.errno or 5)
            return 0
        d, self.next_dir = self.next_dir, self.next_dir + 1
        self.dirs[d] = names
        return d

    def h_fs_dir_next(self, d, buf, cap, *_):
        names = self.dirs.get(d)
        if not names:
            return 0
        n = names.pop(0).encode(errors="surrogateescape")[:cap - 1]
        self.m.uc.mem_write(buf, n + b"\0")
        return len(n)

    def h_fs_dir_close(self, d, *_):
        self.dirs.pop(d, None)
        return 0

    # -- v3 audio / input / memory / extensions
    def h_audio_open(self, rate, period, *_):
        self.audio_period = period
        return 0 if rate == 48000 else U64

    def h_audio_submit(self, pcm, frames, *_):
        self.audio_frames += frames
        return (("sleep", self.m.clock.now_ns() + frames * 1_000_000_000 // 48000), 0)

    def h_audio_close(self, *_):
        return 0

    def h_pad_read(self, i, out, *_):
        self.m.uc.mem_write(out, bytes(32))
        return 0

    def h_pad_vibrate(self, *_):
        return 0

    def h_input_poll(self, *_):
        return 0

    def h_map_fixed(self, addr, size, src, *_):
        return self.m.map_fixed(addr, size, src)

    def h_ext_call(self, module, fn, args, nargs, *_):
        return self.m.ext_call(module, fn, args, nargs)

    def ext_11(self, fn, args):
        """FNVNX_EXT_PLATFORM: only CPU_BOOST(mode) is served here (VM_PROTECT is the machine's); any other fn is not"""
        if fn == FNVNX_PLAT_CPU_BOOST:
            self.boosts.append(args[0] if args else 0)
            return 0
        raise ExtMissing(11, fn)
