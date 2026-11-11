#!/usr/bin/env python3
"""e2e_host - the emulator additions the boot glue's end-to-end run needs, as a subclass of tests/s5/emu_guest.Emu.

    python3 -I loader/guest/e2e_host.py guest.nso [emu_guest.py arguments]      (env E2E_EXE = the exe, for the check)

The emulator stage proper (L4) must provide what this file adds; tests/s5 is not touched.  What it adds:
  - FNVNX_EXT_PLATFORM fn 8 VM_PROTECT (addr, size, prot): R bit0, W bit1, 0 = no access; 0, or 1 when a page of the range
    is not mapped (map_fixed and VM-commit memory both);
  - a VM commit / decommit gives read-write pages again, whatever VM_PROTECT did to them (guard pages of a freed stack);
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

    # -- map_fixed / UNMAP_FIXED (hostapi.h 15): exactly the (addr, size, src) of one earlier map_fixed, once
    def h_map_fixed(self, addr, size, src, *_):
        r = super().h_map_fixed(addr, size, src)
        if r == 0:
            if not hasattr(self, "mf_live"):
                self.mf_live = {}
            self.mf_live[(addr, size)] = src
        return r

    def unmap_fixed(self, addr, size, src):
        if addr & (P - 1) or src & (P - 1):
            return 0xCC01
        if not size or size & (P - 1):
            return 0xCA01
        if getattr(self, "mf_live", {}).get((addr, size)) != src:
            return 0xD401   # the kernel: not the mapping of that source
        try:
            self.uc.mem_unmap(addr, size)
        except UcError:
            return 0xD401
        del self.mf_live[(addr, size)]
        self.mf_unmaps = getattr(self, "mf_unmaps", 0) + 1
        return 0

    def h_ext_call(self, module, fn, args, nargs, *_):
        if module == 11 and fn == 15 and nargs >= 3:   # FNVNX_PLAT_UNMAP_FIXED
            return self.unmap_fixed(*struct.unpack("<QQQ", bytes(self.uc.mem_read(args, 24))))
        if module == 11 and 11 <= fn <= 14:
            av = struct.unpack("<%dQ" % nargs, bytes(self.uc.mem_read(args, 8 * nargs))) if nargs and args else ()
            return self.section_call(fn, av)
        if module == 11 and fn in (5, 6, 7) and nargs >= 1:   # VM commit / decommit / release versus views (host_vm.inc vm_has_view)
            a0, a1 = struct.unpack("<QQ", bytes(self.uc.mem_read(args, 16)))
            if fn == 7:
                resv = getattr(self, "vm_resv", {})
                a0, a1 = (a0, resv[a0]) if a0 in resv else (0, 0)
            if a1 and self.view_overlap(a0 & ~(P - 1), ((a0 + a1 + P - 1) & ~(P - 1)) - (a0 & ~(P - 1))):
                return 0xD401
        if module == 11 and fn == 8:   # FNVNX_PLAT_VM_PROTECT
            addr, size, prot = struct.unpack("<QQQ", bytes(self.uc.mem_read(args, 24)))
            if size and self.view_overlap(addr, size):
                return 0xD401   # SharedCode cannot be reprotected
            if addr & (P - 1) or size & (P - 1) or not size or prot & ~3 or addr + size > 1 << 32:
                return 1
            if len(self.mapped_perms(addr, addr + size)) != size // P:
                return 1
            try:
                self.uc.mem_protect(addr, size, (UC_PROT_READ if prot & 1 else 0) | (UC_PROT_WRITE if prot & 2 else 0))
            except UcError:
                return 1
            self.protects = getattr(self, "protects", 0) + 1
            return 0
        r = super().h_ext_call(module, fn, args, nargs, *_)
        # DECOMMIT as hostapi.h states it: the protection of a page that stays mapped is NOT reset (the loader restores
        # read-write with VM_PROTECT before it decommits); only a COMMIT hands out fresh read-write pages
        if module == 11 and fn == 5 and r == 0:
            a, n = struct.unpack("<QQ", bytes(self.uc.mem_read(args, 16)))
            for pg in range(a & ~(P - 1), (a + n + P - 1) & ~(P - 1), P):
                if pg in getattr(self, "vm_mapped", {}):
                    self.uc.mem_protect(pg, P, UC_PROT_READ | UC_PROT_WRITE)
        return r

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
