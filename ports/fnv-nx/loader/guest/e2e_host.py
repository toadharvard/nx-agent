#!/usr/bin/env python3
"""e2e_host - the emulator additions the boot glue's end-to-end run needs, as a subclass of tests/s5/emu_guest.Emu.

    python3 -I loader/guest/e2e_host.py guest.nso [emu_guest.py arguments]      (env E2E_EXE = the exe, for the check)

The emulator stage proper (L4) must provide what this file adds; tests/s5 is not touched.  What it adds:
  - FNVNX_EXT_PLATFORM as hostapi.h's totality table states it: ONE dispatch (unknown fn -> NO_SERVICE, too few args -> BAD_NARGS), fn 8
    VM_PROTECT (addr, size, prot 0/1/3) on guest-owned pages (VM extents, map_fixed ranges, the windows) with the header's results, fn 9
    DISPLAY_DOCKED (env E2E_DOCKED, default docked), map_fixed / UNMAP_FIXED with the header's checks (UNMAP_FIXED in any protection),
    the VM services 5..7 with extents (a whole extent in a decommit is unmapped, a partial one zeroed and read-write), thread ids that
    name nothing -> NOTFOUND;
  - the VM service on (it is opt-in in emu_guest);
  - FNVNX_EXT_PLATFORM fn 11..14 SECTION_CREATE / MAP / UNMAP / FREE (include/fnvnx/hostapi.h; the device: toolchain/host/host_section.inc)
    with the same semantics, errors and limits (128 sections, 512 views), and its interplay with VM services 5..8 (a range holding a
    view: commit / decommit / protect / release -> 0xD401). Construction: a section is ONE anonymous host mmap; every view is
    uc.mem_map_ptr of that buffer at the view's address, so the views are the same host memory (Unicorn translates a guest access to the
    host pointer of the region; no copy exists, so a write through one view is the very byte another view reads, exactly like the
    kernel's shared physical pages). A view is always read-write;
  - at the end of the run: the protection map of [image base, + SizeOfImage) as the emulator holds it, compared page by
    page with the PE section table (R if MEM_READ, W if MEM_WRITE, never X, headers R, gaps no access), and nothing
    mapped inside the reserved address set (keys range, exe, every row of modules.tsv) outside the loaded images.  Prints `e2e-host: ...` lines.
"""
import ctypes, mmap, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tests", "s5"))
import emu_guest
from unicorn import UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC, UcError

P = 0x1000


def pe_expect(path, rebase=None):
    """(base, size_of_image, {page va: prot}) from the file's headers, Windows rule: prot from the section's bits"""
    with open(path, "rb") as f:
        d = f.read(0x1000)
        lf = struct.unpack_from("<I", d, 0x3C)[0]
        nsec, optsz = struct.unpack_from("<H", d, lf + 6)[0], struct.unpack_from("<H", d, lf + 20)[0]
        opt = lf + 24
        base, size = struct.unpack_from("<I", d, opt + 28)[0], struct.unpack_from("<I", d, opt + 56)[0]
        shoff = opt + optsz
        f.seek(0)
        d = f.read(shoff + 40 * nsec)
    base = rebase if rebase is not None else base
    size = (size + P - 1) & ~(P - 1)
    pages = {base + k: 0 for k in range(0, size, P)}
    soh = struct.unpack_from("<I", d, opt + 60)[0]
    for k in range(0, (soh + P - 1) & ~(P - 1), P):
        pages[base + k] = UC_PROT_READ
    secs = []
    for i in range(nsec):
        h = d[shoff + 40 * i: shoff + 40 * i + 40]
        vs, rva, raw = struct.unpack_from("<III", h, 8)
        ch = struct.unpack_from("<I", h, 36)[0]
        vs = vs or raw
        prot = (UC_PROT_READ if ch & 0x40000000 else 0) | (UC_PROT_WRITE if ch & 0x80000000 else 0)
        for k in range(0, (vs + P - 1) & ~(P - 1), P):
            pages[base + rva + k] = prot
        secs.append((h[:8].rstrip(b"\0").decode(), base + rva, vs, prot))
    return base, size, pages, secs


SEC_E_SIZE, SEC_E_ALIGN, SEC_E_RANGE, SEC_E_REGION, SEC_E_ID, SEC_E_NOMEM, SEC_E_FULL = 0xCA01, 0xCC01, 0xD401, 0xDA01, 0xE401, 0xD001, 0xCE01
SEC_MAX_SIZE, SEC_SECTIONS, SEC_VIEWS = 0x80000000, 128, 512
M64 = (1 << 64) - 1
E_NOMEM, E_ALREADY, E_NOTFOUND, E_BADINPUT = 0x559, 0xF59, 0x1359, 0x1759   # libnx results, hostapi.h FNVNX_E_*
E_SIZE, E_ADDR, E_RANGE, E_PERM = 0xCA01, 0xCC01, 0xD401, 0xD801               # kernel results
NO_MODULE, NO_SERVICE, BAD_NARGS = M64 - 254, M64 - 253, M64 - 252              # FNVNX_EXT_NO_MODULE (-255), _NO_SERVICE (-254), _BAD_NARGS (-253)


def sec_err(code):
    return (-code) & M64


class E2EEmu(emu_guest.Emu):
    vm_service = True

    # -- memory sections (hostapi.h 11..14) ------------------------------------------------------------------
    def sec_state(self):
        if not hasattr(self, "sections"):
            self.sections, self.sec_views, self.sec_next = {}, {}, 1   # id -> [mmap, size, live, view count]; addr -> (size, off, id)
        return self.sections, self.sec_views

    def view_overlap(self, a, n):
        return any(va < a + n and a < va + vs for va, (vs, _o, _i) in self.sec_state()[1].items())

    def sec_release(self, sid):
        self.sections[sid][0].close()
        del self.sections[sid]

    def section_call(self, fn, av):
        secs, views = self.sec_state()
        ra = lambda k: av[k] if k < len(av) else 0
        if fn == 11:    # CREATE (size)
            size = ra(0)
            if not size or size & (P - 1) or size > SEC_MAX_SIZE:
                return sec_err(SEC_E_SIZE)
            if len(secs) >= SEC_SECTIONS:
                return sec_err(SEC_E_FULL)
            if self.sec_next > 0x7FFFFFFF:
                return sec_err(SEC_E_NOMEM)
            sid, self.sec_next = self.sec_next, self.sec_next + 1
            secs[sid] = [mmap.mmap(-1, size), size, True, 0]   # anonymous: zero
            return sid
        if fn == 12:    # MAP (id, addr, offset, size)
            sid, addr, off, size = ra(0), ra(1), ra(2), ra(3)
            if not size or size & (P - 1) or size > SEC_MAX_SIZE:
                return sec_err(SEC_E_SIZE)
            if (addr | off) & (P - 1):
                return sec_err(SEC_E_ALIGN)
            sec = secs.get(sid)
            if not sec or not sec[2]:
                return sec_err(SEC_E_ID)
            if off + size > sec[1]:
                return sec_err(SEC_E_RANGE)
            if len(views) >= SEC_VIEWS:
                return sec_err(SEC_E_FULL)
            if addr:
                if not any(b <= addr and addr + size <= b + s for b, s in getattr(self, "vm_resv", {}).items()):
                    return sec_err(SEC_E_REGION)
                a = addr
            else:   # the host's own range: 64 KiB aligned, from the top of the arena band downward
                a = next((c for c in range((self.VM_ANY[1] - size) & ~(self.VM_ALIGN - 1), self.VM_ANY[0], -self.VM_ALIGN) if self.vm_free(c, size)), 0)
                if not a:
                    return sec_err(SEC_E_NOMEM)
            if any(b <= a + size - 1 and a < e + 1 for b, e, _p in self.uc.mem_regions()):   # kernel-free: nothing mapped there
                return sec_err(SEC_E_RANGE)
            ptr = ctypes.addressof(ctypes.c_char.from_buffer(sec[0])) + off
            self.uc.mem_map_ptr(a, size, UC_PROT_READ | UC_PROT_WRITE, ptr)
            views[a] = (size, off, sid)
            sec[3] += 1
            return a
        if fn == 13:    # UNMAP (addr, size): exactly a view
            a, size = ra(0), ra(1)
            v = views.get(a)
            if not v or v[0] != size:
                return sec_err(SEC_E_RANGE)
            self.uc.mem_unmap(a, size)
            del views[a]
            sec = secs[v[2]]
            sec[3] -= 1
            if not sec[2] and not sec[3]:
                self.sec_release(v[2])
            return 0
        if fn == 14:    # FREE (id): deferred while views live
            sec = secs.get(ra(0))
            if not sec or not sec[2]:
                return sec_err(SEC_E_ID)
            sec[2] = False
            if not sec[3]:
                self.sec_release(ra(0))
            return 0
        return 0xFFFFFFFFFFFFFF01

    def mapped_perms(self, lo, hi):
        """{page: perms} of the mapped pages in [lo, hi)"""
        out = {}
        for b, e, perm in self.uc.mem_regions():
            for a in range(max(b, lo), min(e + 1, hi), P):
                out[a] = perm
        return out

    # -- the platform services as hostapi.h states them (totality table): ONE dispatch, every (fn, nargs) has one answer
    NEED = {1: 1, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 1, 8: 3, 9: 0, 11: 1, 12: 4, 13: 2, 14: 1, 15: 3}   # fn -> least nargs; 10 (access fault) is not served
    WINDOW = (0x00400000, 0x02100000)   # the windows the host claimed at start (original image range + data window), owned from the start
    MF_MAX = 512

    def u64s(self, args, k):
        return struct.unpack("<%dQ" % k, bytes(self.uc.mem_read(args, 8 * k))) if k else ()

    def cstr(self, p):
        out = b""
        while p:
            c = bytes(self.uc.mem_read(p + len(out), 1))
            if c == b"\0":
                break
            out += c
        return out.decode("latin-1")

    def mf_overlap(self, lo, hi):
        return any(a < hi and lo < a + n for (a, n) in getattr(self, "mf_live", {}))

    def in_window(self, a, n):
        return self.WINDOW[0] <= a and a + n <= self.WINDOW[1]

    def window_fill(self, lo, hi):
        """claimed memory exists: window pages not mapped yet are read-write zero (the host mapped them at start)"""
        pg = lo
        while pg < hi:
            if not any(b <= pg <= e for b, e, _p in self.uc.mem_regions()):
                self.uc.mem_map(pg, P, UC_PROT_READ | UC_PROT_WRITE)
            pg += P

    def owned(self, lo, hi):
        """every page of [lo, hi) is guest-owned: a VM extent, a map_fixed range or a window page"""
        mapped = getattr(self, "vm_mapped", {})
        mf = getattr(self, "mf_live", {})
        return all(pg in mapped or self.WINDOW[0] <= pg < self.WINDOW[1] or any(a <= pg < a + n for (a, n) in mf) for pg in range(lo, hi, P))

    # map_fixed (addr, size, src): size 0, wrap or unaligned -> BADINPUT; in a window: read-write and zero; src 0 or unaligned -> BADINPUT;
    # 512 live -> NOMEM; mapped -> ALREADY; else mapped (reprotectable)
    def h_map_fixed(self, addr, size, src, *_):
        if not size or addr + size >= 1 << 64 or (addr | size) & (P - 1):
            return E_BADINPUT
        if self.in_window(addr, size):
            self.window_fill(addr, addr + size)
            self.uc.mem_protect(addr, size, UC_PROT_READ | UC_PROT_WRITE)
            self.uc.mem_write(addr, bytes(size))
            self.mf_win = getattr(self, "mf_win", 0) + 1
            return 0
        if not src or src & (P - 1):
            return E_BADINPUT
        if not hasattr(self, "mf_live"):
            self.mf_live = {}
        if len(self.mf_live) >= self.MF_MAX:
            return E_NOMEM
        if any(b <= addr + size - 1 and addr <= e for b, e, _p in self.uc.mem_regions()):
            return E_ALREADY
        try:
            self.uc.mem_map(addr, size, UC_PROT_READ | UC_PROT_WRITE)
        except UcError:
            return E_RANGE
        self.mapped_fixed = getattr(self, "mapped_fixed", []) + [(addr, size)]
        self.mf_live[(addr, size)] = src
        return 0

    # UNMAP_FIXED (addr, size, src): alignment, size, wrap, then a window (read-write and zero) or exactly one live map_fixed, in any protection
    def unmap_fixed(self, addr, size, src):
        if (addr | src) & (P - 1):
            return E_ADDR
        if not size or size & (P - 1):
            return E_SIZE
        if addr + size >= 1 << 64:
            return E_RANGE
        if self.in_window(addr, size):
            self.window_fill(addr, addr + size)
            self.uc.mem_protect(addr, size, UC_PROT_READ | UC_PROT_WRITE)
            self.uc.mem_write(addr, bytes(size))
            return 0
        if getattr(self, "mf_live", {}).get((addr, size)) != src:
            return E_RANGE
        self.uc.mem_unmap(addr, size)
        del self.mf_live[(addr, size)]
        self.mf_unmaps = getattr(self, "mf_unmaps", 0) + 1
        return 0

    # VM_PROTECT (address, size, prot): address, size, wrap, prot (0, 1, 3), ownership; views and non-owned pages -> RANGE
    def vm_protect(self, addr, size, prot):
        if addr & (P - 1):
            return E_ADDR
        if not size or size & (P - 1):
            return E_SIZE
        if addr + size >= 1 << 64:
            return E_RANGE
        if prot > 3 or prot == 2:
            return E_PERM
        if self.view_overlap(addr, size) or not self.owned(addr, addr + size):
            return E_RANGE
        if self.in_window(addr, size):
            self.window_fill(addr, addr + size)
        self.uc.mem_protect(addr, size, (UC_PROT_READ if prot & 1 else 0) | (UC_PROT_WRITE if prot & 2 else 0))
        self.protects = getattr(self, "protects", 0) + 1
        return 0

    # VM services (host_vm.inc): extents are one per gap of a commit; decommit unmaps extents wholly inside, zeroes the rest read-write
    def vm_commit(self, a, n):
        if not n or a + n + P > 1 << 64:
            return E_BADINPUT
        lo, hi = a & ~(P - 1), (a + n + P - 1) & ~(P - 1)
        resv, mapped = getattr(self, "vm_resv", {}), self.vm_mapped
        base = next((b for b, s in resv.items() if b <= lo and hi <= b + s), None)
        if base is None:
            return E_BADINPUT
        if self.view_overlap(lo, hi - lo) or self.mf_overlap(lo, hi):
            return E_RANGE
        if not hasattr(self, "vm_ext"):
            self.vm_ext = []
        pg = lo
        while pg < hi:
            if pg in mapped:
                pg += P
                continue
            run = pg
            while run < hi and run not in mapped:
                run += P
            try:
                self.uc.mem_map(pg, run - pg, UC_PROT_READ | UC_PROT_WRITE)
            except UcError:
                return E_NOMEM
            for q in range(pg, run, P):
                mapped[q] = base
            self.vm_ext.append([pg, run - pg])
            pg = run
        return 0

    def vm_decommit(self, a, n):
        if not n:
            return 0
        if a + n + P > 1 << 64:
            return E_RANGE
        lo, hi = a & ~(P - 1), (a + n + P - 1) & ~(P - 1)
        if self.view_overlap(lo, hi - lo) or self.mf_overlap(lo, hi):
            return E_RANGE
        for e in list(getattr(self, "vm_ext", [])):
            s, sz = e
            if s >= hi or s + sz <= lo:
                continue
            if s >= lo and s + sz <= hi:
                self.uc.mem_unmap(s, sz)
                for q in range(s, s + sz, P):
                    self.vm_mapped.pop(q, None)
                self.vm_ext.remove(e)
            else:
                x, y = max(s, lo), min(s + sz, hi)
                self.uc.mem_protect(x, y - x, UC_PROT_READ | UC_PROT_WRITE)
                self.uc.mem_write(x, bytes(y - x))
        return 0

    def vm_release(self, a):
        resv = getattr(self, "vm_resv", {})
        if a not in resv:
            return E_BADINPUT
        r = self.vm_decommit(a, resv[a])
        if r:
            return r
        del resv[a]
        return 0

    def h_thread_start(self, tid, *_):
        return super().h_thread_start(tid) if self.live_thread(tid) else E_NOTFOUND

    def h_thread_join(self, tid, *_):
        if not self.live_thread(tid):
            return E_NOTFOUND
        if self.threads[tid].state == "new":
            return E_BADINPUT
        return super().h_thread_join(tid)

    def h_thread_free(self, tid, *_):
        if not self.live_thread(tid):
            return E_NOTFOUND
        self.freed_threads = getattr(self, "freed_threads", set()) | {tid}
        return 0

    def live_thread(self, tid):
        return tid in self.threads and tid not in getattr(self, "freed_threads", set())

    def h_thread_set_priority(self, tid, prio, *_):
        return super().h_thread_set_priority(tid, prio) if (tid == 0 or self.live_thread(tid)) else E_NOTFOUND

    def h_thread_set_cpu(self, tid, spec, *_):
        return super().h_thread_set_cpu(tid, spec) if (tid == 0 or self.live_thread(tid)) else E_NOTFOUND

    def h_fs_dir_next(self, d, buf, cap, *_):
        return super().h_fs_dir_next(d, buf, cap) if cap else 0

    def h_ext_call(self, module, fn, args, nargs, *_):
        if module != 11:
            return super().h_ext_call(module, fn, args, nargs)
        if fn > 0xFFFFFFFF or fn not in self.NEED:
            return NO_SERVICE
        nargs = min(nargs, 0xFFFFFFFF)
        if nargs < self.NEED[fn] or (nargs and not args):
            return BAD_NARGS
        av = self.u64s(args, self.NEED[fn])
        if fn == 1:
            return super().h_ext_call(module, fn, args, nargs)
        if fn == 2:   # THREAD_ACTIVITY (id, paused): 0 is the caller, an id that names no live thread
            return E_BADINPUT if av[0] == 0 else (0 if self.live_thread(av[0]) else E_NOTFOUND)
        if fn == 3:   # MESSAGE: the dialog closes at once in the model
            print("   message box: %s | %s" % (self.cstr(av[0]), self.cstr(av[1])))
            return 0
        if fn == 4:
            self.vm_mapped = getattr(self, "vm_mapped", {})
            return 0 if av[1] > 0x100000000 else self.vm_call(4, av[0], av[1])
        if fn in (5, 6, 7):
            self.vm_mapped = getattr(self, "vm_mapped", {})
            return {5: self.vm_commit, 6: self.vm_decommit, 7: self.vm_release}[fn](*av[:2 if fn != 7 else 1])
        if fn == 8:
            return self.vm_protect(*av)
        if fn == 9:   # DISPLAY_DOCKED
            return 1 if os.environ.get("E2E_DOCKED", "1") == "1" else 0
        if fn == 15:
            return self.unmap_fixed(*av)
        return self.section_call(fn, av)

    def arena_lo(self):
        resv = getattr(self, "vm_resv", {})
        return min(resv) if resv else self.VM_ANY[0]

    def run(self):
        rc = super().run()
        exe = os.environ.get("E2E_EXE")
        if exe:
            self.image_protection_report(exe)
        return rc

    def image_protection_report(self, exe):
        base, size, want, secs = pe_expect(exe)
        extra = []   # the static modules of modules.tsv (E2E_MODULES), at their assigned bases
        mt, md = os.environ.get("E2E_MODULES"), os.environ.get("E2E_MODDIR")
        if mt:
            for ln in open(mt):
                c = ln.rstrip("\n").split("\t")
                if ln[:1] != "#" and c[8] == "1":
                    b2, s2, w2, _ = pe_expect(os.path.join(md, c[0]), int(c[4], 16))
                    extra.append((b2, s2, w2))
        for b2, s2, w2 in extra:
            want.update(w2)
        have = {}
        for b0, s0 in [(base, size)] + [(b2, s2) for b2, s2, _ in extra]:
            have.update(self.mapped_perms(b0, b0 + s0))
        bad = [hex(a) for a in want if have.get(a) != want[a]]
        x = [hex(a) for a, p in have.items() if p & UC_PROT_EXEC]
        # nothing else is mapped below the arena: the reserved address set is the exe, the static modules and the keys' range
        rows = [(0xf0000, 0x200000), (base, base + size)]
        if mt:
            for ln in open(mt):
                c = ln.rstrip("\n").split("\t")
                if ln[:1] != "#" and c[8] == "1":   # an on-demand range may hold what a REFUSED load leaves mapped (fnvnx_module_load); the fixture checks that every load that succeeded unloads
                    rows.append((int(c[4], 16), int(c[4], 16) + int(c[5], 16)))
        low = sorted(hex(a) for lo, hi in rows for a in self.mapped_perms(lo, hi) if a not in have)
        runs = []
        for a in sorted(have):
            if runs and runs[-1][1] == a and runs[-1][2] == have[a]:
                runs[-1][1] = a + P
            else:
                runs.append([a, a + P, have[a]])
        if low:
            print("e2e-host: unexpected pages %s .. %s (reserved set)" % (low[0], low[-1]))
        for s, e, p in runs:
            print("e2e-host: map %08x-%08x %s%s%s" % (s, e, "r" if p & 1 else "-", "w" if p & 2 else "-", "x" if p & 4 else "-"))
        for name, va, vs, prot in secs:
            print("e2e-host: section %-8s %08x +%06x %s%s" % (name, va, vs, "r" if prot & 1 else "-", "w" if prot & 2 else "-"))
        print("e2e-host: VM_PROTECT calls %d; image pages %d, differing from the section table %d, executable %d; "
              "mapped in the reserved address set outside the loaded images %d" % (getattr(self, "protects", 0), len(want), len(bad), len(x), len(low)))
        print("e2e-host: image protection %s" % ("ok" if not (bad or x or low) and getattr(self, "protects", 0) else "MISMATCH"))


if __name__ == "__main__":
    emu_guest.Emu = E2EEmu
    sys.exit(emu_guest.main(sys.argv[1:]))
