#!/usr/bin/env python3
"""The host services of the boot stage: the 46 h_<slot> methods of the FnvnxHostApi v3 table. Policy (address space, scheduling, ext modules) lives in bootmachine.Machine; these
methods do the argument decoding and the host-side effect. A blocking call returns (wait spec, result when resumed).

Files: guest paths go through bootpath.BootPath (sdmc: prefix, case-folded components, the game mounts; its rule is
stated in bootpath.py). BootPath(root) creates <root>/switch/fnv, as the device always has it.

Host slots whose effect is not modelled (the observable contract is kept; see HOST SLOTS in the stage report):
 output_debug_string (kept in .debug, no sink),
 thread_free, thread_set_priority / thread_set_cpu (recorded in .placement only, no placement effect),
 wall_time_us (fixed epoch + virtual ns), random64 (deterministic splitmix64, seed 0),
 memory_info (4 GiB / 3 GiB), cpu_count (3), audio_open / audio_submit / audio_close (frames counted, samples
 dropped, submit sleeps the period in virtual time), pad_read / pad_vibrate / input_poll (the declared device states of a scenario, bootinput.py; none: nothing attached), module 11 CPU_BOOST (recorded in .boosts, returns 0: no platform config), MESSAGE (caption and text recorded in
 .messages, returns 0: the dialog is closed at once). The rest of module 11 (thread_activity, vm_*) is the machine's.
"""
import errno
import os
import stat as S
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bootinput  # noqa: E402
import bootmachine  # noqa: E402
import bootpath  # noqa: E402

U64 = (1 << 64) - 1
WALL_EPOCH_US = 1_700_000_000_000_000
INT64_MAX = (1 << 63) - 1
HE_NOTFOUND, HE_BADINPUT, HE_ADDR, HE_RANGE = bootmachine.FNVNX_HE_NOTFOUND, bootmachine.LIBNX_BAD_INPUT, 0xCC01, 0xD401


def neg(e):
    return (-e) & U64


class Services:
    def __init__(self, root, game=None, scenario=None, watch_reads=False):
        self.m = None
        self.input = bootinput.InputModel(scenario, watch_reads)   # R-INPUT: the declared device states, read as host_v3.inc does
        self.dp = bootpath.BootPath(root, game)
        self.debug, self.fds = [], {}
        self.audio_period, self.audio_frames, self.boosts, self.placement, self.messages = 0, 0, [], {}, []
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
        self.last_ro = self.dp.read_only(self.last_sd)
        return self.dp.resolve(self.last_sd)

    def _erofs(self, call, sd):
        """a create/change/remove under a read-only declared input (bootpath.READ_ONLY): the call fails with EROFS"""
        self.dp.fault(call, sd, errno.EROFS)
        return neg(errno.EROFS)

    # -- v1
    def h_output_debug_string(self, s, n, *_):
        self.debug.append(bytes(self.m.uc.mem_read(s, n)).decode(errors="replace"))
        return 0

    def h_sleep_ns(self, ns, *_):
        return (("sleep", self.m.clock.now_ns() + min(ns, INT64_MAX)), 0)   # above INT64_MAX sleeps INT64_MAX ns

    def h_exit(self, code, *_):
        self.m.exit(code)
        return 0

    def h_console_write(self, b, n, *_):
        return self.m.console_write(bytes(self.m.uc.mem_read(b, n)))

    def h_system_tick(self, *_):
        return self.m.clock.read()

    def h_system_tick_freq(self, *_):
        return self.m.clock.FREQ

    # -- v2 threads
    def h_thread_create(self, entry, arg, stack, prio, cpu, *_):
        h = self.m.thread_create(entry, arg)
        if h:
            self.placement[self.m.ids.get("thread", h).tid] = {"prio": prio, "cpu": cpu}
        return h

    def h_thread_start(self, h, *_):
        return self.m.thread_start(h)

    def h_thread_join(self, h, *_):
        t = self.m.ids.get("thread", h)
        if t is None:
            return HE_NOTFOUND
        if isinstance(t, bootmachine.SelfRecord) or t.state == "new":   # a THREAD_SELF record, or never started: it would never end
            return HE_BADINPUT
        return 0 if t.state == "done" else (("join", t.tid), 0)

    def h_thread_free(self, h, *_):
        """the id dies at once (a running thread is detached); a second free, or any later use, is NotFound"""
        return 0 if self.m.ids.free("thread", h) else HE_NOTFOUND

    def h_thread_exit(self, *_):
        return (("done",), 0)

    def h_thread_yield(self, *_):
        return (("yield",), 0)

    def _placed(self, h):
        """the placement record of thread id h (0 = the calling thread), None for an id that names no live thread"""
        t = self.m.cur if h == 0 else self.m.ids.get("thread", h)
        if isinstance(t, bootmachine.SelfRecord):
            t = t.t
        return None if t is None else self.placement.setdefault(t.tid, {})

    def h_thread_set_priority(self, h, prio, *_):
        p = self._placed(h)
        if p is None:
            return HE_NOTFOUND
        p["prio"] = prio
        return 0

    def h_thread_set_cpu(self, h, spec, *_):
        p = self._placed(h)
        if p is None:
            return HE_NOTFOUND
        if spec >> 40 & 0x7FFFFF == 0x7FFFFF:   # a negative spec (bits 40..62 set): nothing changed
            return 0
        p["cpu"] = spec
        return 0

    def h_wait_on_address(self, addr, expected, timeout, *_):
        if addr % 4:
            return HE_ADDR
        if not self.m.accessible(addr, addr + 4, bootmachine.UC_PROT_READ):
            return HE_RANGE
        (v,) = struct.unpack("<I", self.m.uc.mem_read(addr, 4))
        if v != (expected & 0xFFFFFFFF):
            return 2
        if timeout == 0:
            return 1
        return (("addr", addr, None if timeout > INT64_MAX else self.m.clock.now_ns() + timeout), 0)

    def h_wake_address(self, addr, count, *_):
        if addr % 4:
            return HE_ADDR
        if not self.m.accessible(addr, addr + 4, bootmachine.UC_PROT_READ):
            return HE_RANGE
        self.m.wake(addr, U64 if count > 0x7FFFFFFF else count)
        return 0

    def h_wall_time_us(self, *_):
        return WALL_EPOCH_US + self.m.clock.now_ns() // 1000

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
        if self.last_ro and fl & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            return self._erofs("open", sd)

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
        if self.last_ro:
            return self._erofs("mkdir", self.last_sd)
        return self._mutate(lambda: os.mkdir(path), "mkdir", self.last_sd)

    def h_fs_rmdir(self, p, *_):
        path = self.path(p)
        if self.last_ro:
            return self._erofs("rmdir", self.last_sd)
        return self._mutate(lambda: os.rmdir(path), "rmdir", self.last_sd)

    def h_fs_remove(self, p, *_):
        path = self.path(p)
        if self.last_ro:
            return self._erofs("remove", self.last_sd)
        return self._mutate(lambda: os.remove(path), "remove", self.last_sd)

    def h_fs_rename(self, a, b, *_):
        pa, sa, ra = self.path(a), self.last_sd, self.last_ro
        pb = self.path(b)
        if ra or self.last_ro:
            return self._erofs("rename", sa if ra else self.last_sd)
        return self._mutate(lambda: os.replace(pa, pb), "rename", sa)

    def h_fs_dir_open(self, p, *_):
        path, sd = self.path(p), self.last_sd
        try:
            names = sorted(os.listdir(path))   # . and .. are not listed
        except OSError as e:
            self.dp.fault("dir_open", sd, e.errno or 5)
            return 0
        return self.m.ids.alloc("dir", names)   # 0 when the id table is full

    def h_fs_dir_next(self, d, buf, cap, *_):
        names = self.m.ids.get("dir", d)
        if not names or cap == 0:   # an id that names no open directory, cap 0, or the end: 0, nothing consumed
            return 0
        n = names.pop(0).encode(errors="surrogateescape")[:cap - 1]
        self.m.uc.mem_write(buf, n + b"\0")
        return len(n)

    def h_fs_dir_close(self, d, *_):
        self.m.ids.free("dir", d)   # an id that names no open directory: nothing done
        return 0

    # -- v3 audio / input / memory / extensions
    def h_audio_open(self, rate, period, *_):
        """already open: 0, arguments ignored; -1 for a rate other than 48000 or a period outside 1..8192"""
        if self.audio_period:
            return 0
        if rate != 48000 or not 1 <= period <= 8192:
            return U64
        self.audio_period = period
        return 0

    def h_audio_submit(self, pcm, frames, *_):
        if not self.audio_period or frames != self.audio_period:   # -1, nothing read
            return U64
        self.audio_frames += frames
        return (("sleep", self.m.clock.now_ns() + frames * 1_000_000_000 // 48000), 0)

    def h_audio_close(self, *_):
        self.audio_period = 0
        return 0

    def h_pad_read(self, i, out, *_):
        """index 0..3: the declared pad state (not connected by default: 0, zeros written); index > 3: 0, nothing written (bootinput)"""
        return self.input.pad_read(self.m, i, out)

    def h_pad_vibrate(self, i, lo, hi, *_):
        """recorded as an event pad-vibrate (index <= 3), returns 0 (bootinput)"""
        return self.input.pad_vibrate(self.m, i, lo, hi)

    def h_input_poll(self, out, cap, *_):
        """host_v3.inc's input_poll over the declared device states (bootinput)"""
        return self.input.input_poll(self.m, out, cap)

    def h_map_fixed(self, addr, size, src, *_):
        return self.m.map_fixed(addr, size, src)

    def h_ext_call(self, module, fn, args, nargs, *_):
        return self.m.ext_call(module, fn, args, nargs)

    def plat_cpu_boost(self, args):
        """FNVNX_PLAT_CPU_BOOST(mode) -> 0 set"""
        self.boosts.append(args[0])
        return 0

    def plat_message(self, args):
        """FNVNX_PLAT_MESSAGE(caption, text) -> 0 after the dialog closed"""
        self.messages.append((self.cstr(args[0]), self.cstr(args[1])))
        return 0
