#!/usr/bin/env python3
"""slot_class.py - name the slot-order class of the failing virtual call in a boot log's fault (S5-boot, D22/D26).

    slot_class.py [LOG] --elf guest/fnvnx.elf [--addrmap link/out/addrmap.tsv] [--exe FalloutNV.exe]
                  [--vptr ADDR] [--slot N] [--site SYM+0xOFF|ADDR] [--nm aarch64-linux-gnu-nm]

LOG is an emulator log (tests/s5/emu_guest.py) with a "== trace (fault: ...)" block. The tool takes the call
sites in the order lr, then the backtrace frames #0.., looks at the instruction before each return address, and
takes the first `blr` that is a virtual call: `ldr wV,[xR]` (vptr), `ldr wF,[xV,#off]` (slot = off/4), `blr xF`
(copies through `mov` are followed). Frames are found by their symbol name in --elf when the log came from another
build of the guest (the offset in the name is kept), else by address. --site names the call site (the instruction
after the blr, i.e. a return address) when the log has none; with --slot the site is not decoded at all.

The receiver's vptr is what the log cannot show (memory): give it with --vptr, from an emulator --peek. Link
addresses (0x30000000 up), runtime addresses (0x05000000 up, minus 0x05000000 plus 0x30000000) and exe image
addresses (0x00fdf000..0x01200000) are told apart by range.

Classes (checked in this order):
  D26c image vptr   the vptr is an exe vftable stored by hand, no native vtable: the declaration (with a virtual
                    destructor) uses minus-one order, the object is exe order. Evidence: the RTTI locator at
                    vptr-4 and exe slots s and s-1 (the function that ran, the function meant).
  short vtable      the slot is past the end of the receiver's native _ZTV object, or the word there is not code
                    (0, an offset-to-top, a typeinfo): an unbound constructor-named or member-subobject vtable.
  trap              the slot holds a __fnvnx_vtrap_* stub: the virtual is defined nowhere.
  this-less slot    the slot holds an Itanium _Z<digits>FUN_... free function called with this.
  D26b call-only    the native vtable has GCC's D1/D0 pair at slots 0/1 and the slot is >= 1: native slot k+1 is
                    exe slot k, so a declaration in exe slot order (placeholder at slot 0) lands one slot early.
                    The binary cannot show the declaration: check it with callonly_vt.py.
  other             anything else (no vptr, vptr in no native vtable, slot fine).
Owners: D26b/D26c -> S1-rules / S2-bodies-2; short vtable, this-less slot -> S2-bodies-2; trap -> bodies."""
from __future__ import annotations

import argparse
import bisect
import os
import re
import struct
import subprocess
import sys

IMAGE_LO, IMAGE_HI = 0x00FDF000, 0x01200000
LINK_BASE, RUN_BASE = 0x30000000, 0x05000000
RUN_SPAN = 0x04000000

OWNERS = {
    "D26b call-only": "S1-rules / S2-bodies-2",
    "D26c image vptr": "S1-rules / S2-bodies-2",
    "short vtable": "S2-bodies-2",
    "this-less slot": "S2-bodies-2",
    "trap": "bodies",
    "other": "triage (orchestrator)",
}
CODE_TYPES = set("TtWw")
THISLESS = re.compile(r"^_Z\d+FUN_")


# --------------------------------------------------------------------------------------------- symbols and memory
class Syms:
    """Defined symbols: entries (addr, size, type, name), as `nm -S` prints them."""

    def __init__(self, entries):
        self.by_name, self.at = {}, {}
        vt, fn = [], []
        for addr, size, typ, name in entries:
            self.by_name.setdefault(name, []).append(addr)
            self.at.setdefault(addr, []).append(name)
            if name.startswith("_ZTV") and typ in "DdRrVv":
                vt.append((addr, size, name))
            if typ in CODE_TYPES:
                fn.append((addr, size, name))
        self.vt = sorted(vt)
        self.fn = sorted(fn)
        self._vt_starts = [v[0] for v in self.vt]
        self._fn_starts = [f[0] for f in self.fn]

    @classmethod
    def from_nm(cls, text):
        out = []
        for ln in text.splitlines():
            m = re.match(r"^([0-9a-fA-F]+) (?:([0-9a-fA-F]+) )?(\w) (\S+)$", ln)
            if m:
                out.append((int(m.group(1), 16), int(m.group(2), 16) if m.group(2) else 0, m.group(3), m.group(4)))
        return cls(out)

    def names_at(self, addr):
        return self.at.get(addr, [])

    def vtable_containing(self, addr):
        i = bisect.bisect_right(self._vt_starts, addr) - 1
        if i >= 0:
            a, size, name = self.vt[i]
            if a <= addr < a + max(size, 1):
                return a, size, name
        return None

    def func_containing(self, addr):
        i = bisect.bisect_right(self._fn_starts, addr) - 1
        if i >= 0:
            a, size, name = self.fn[i]
            if size == 0 or addr < a + size:
                return a, size, name
        return None

    def describe(self, addr):
        """the best name for a code address: 'name' or 'name+0xOFF'"""
        names = self.names_at(addr)
        if names:
            return min(names, key=lambda n: (n.startswith("__fnv_"), len(n)))
        f = self.func_containing(addr)
        return f"{f[2]}+{addr - f[0]:#x}" if f else f"{addr:#010x}"


class ElfMem:
    """read32/is_text over the PT_LOAD segments of the guest ELF (the words are already relocated in the file)."""

    def __init__(self, path):
        from elftools.elf.elffile import ELFFile
        self.f = open(path, "rb")
        elf = ELFFile(self.f)
        self.segs = []
        for s in elf.iter_segments():
            if s["p_type"] == "PT_LOAD":
                self.segs.append((s["p_vaddr"], s["p_filesz"], s["p_memsz"], s["p_flags"], s.data()))

    def _seg(self, addr, n=4):
        for va, fsz, msz, flags, data in self.segs:
            if va <= addr and addr + n <= va + msz:
                return va, fsz, flags, data
        return None

    def read(self, addr, n):
        s = self._seg(addr, n)
        if s is None:
            return None
        va, fsz, _, data = s
        o = addr - va
        return bytes(data[o:o + n]) if o + n <= fsz else bytes(n)

    def read32(self, addr):
        b = self.read(addr, 4)
        return None if b is None else struct.unpack("<I", b)[0]

    def is_text(self, addr):
        s = self._seg(addr)
        return bool(s and s[2] & 1)


# --------------------------------------------------------------------------------------------- the log
FRAME = re.compile(r"^\s+(pc|lr|#\d+)\s+0x([0-9a-f]+)\s+(\S+?)(?: \[orig ([0-9a-f]+)\])?\+0x([0-9a-f]+)\s*$")
REG = re.compile(r"\b(x\d+) 0x([0-9a-f]+)")


def parse_log(text):
    """The last '== trace' block: ({label: (addr, name, off)}, {reg: value}, [labels in order])."""
    lines = text.splitlines()
    start = max((i for i, ln in enumerate(lines) if ln.startswith("== trace")), default=None)
    if start is None:
        return {}, {}, []
    frames, regs, order = {}, {}, []
    for ln in lines[start + 1:]:
        m = FRAME.match(ln)
        if m:
            frames[m.group(1)] = (int(m.group(2), 16), m.group(3), int(m.group(5), 16))
            order.append(m.group(1))
            continue
        if re.match(r"^\s+(x\d+ 0x[0-9a-f]+\s*)+$", ln):
            regs.update({k: int(v, 16) for k, v in REG.findall(ln)})
            continue
        if re.match(r"^\s+#\d+\s", ln):    # a frame without a symbol offset (entry stubs)
            continue
        if frames and ln.strip() and not re.match(r"^\s+(pc|lr|x\d+|#\d+)\b", ln):
            break
    return frames, regs, order


def resolve_frame(frame, syms):
    """(elf address, note) of a log frame (addr, name, off): by name when the log's build differs from the ELF."""
    addr, name, off = frame
    cands = syms.by_name.get(name, [])
    if cands:
        base = addr - off
        best = base if base in cands else cands[0]
        return best + off, ("" if best == base else f"resolved by name (log address {addr:#x})")
    if syms.func_containing(addr):
        return addr, "resolved by address (name not in the ELF)"
    return None, "unresolved"


# --------------------------------------------------------------------------------------------- the call site
def _reg(cs, r):
    n = cs.reg_name(r)
    return int(n[1:]) if n and n[0] in "wx" and n[1:].isdigit() else None


def find_vcall(read, blr_addr, func_lo=None, lookback=40):
    """Decode a virtual call ending at the `blr` at blr_addr -> dict(slot, off, blr_reg, vptr_reg, recv_reg,
    lines) or None. read(addr, n) -> bytes."""
    import capstone
    cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    cs.detail = True
    lo = blr_addr - 4 * lookback
    if func_lo is not None:
        lo = max(lo, func_lo)
    code = read(lo, blr_addr + 4 - lo)
    if code is None:
        return None
    ins = list(cs.disasm(code, lo))
    if not ins or ins[-1].address != blr_addr or ins[-1].mnemonic != "blr":
        return None
    ops = ins[-1].operands
    if not ops:
        return None
    breg = _reg(cs, ops[0].reg)

    def definition(reg, before):
        """the last instruction before index `before` that writes reg, following reg-to-reg movs"""
        i = before - 1
        while i >= 0:
            c = ins[i]
            if c.mnemonic in ("bl", "blr") and reg <= 18:
                return None, i
            try:
                writes = [_reg(cs, w) for w in c.regs_access()[1]]
            except capstone.CsError:
                writes = []
            if reg in writes:
                if c.mnemonic == "mov" and len(c.operands) == 2 and c.operands[1].type == capstone.arm64.ARM64_OP_REG:
                    reg = _reg(cs, c.operands[1].reg)
                    if reg is None:
                        return None, i
                else:
                    return c, i
            i -= 1
        return None, -1

    def as_ldr(c):
        if c is None or c.mnemonic != "ldr" or len(c.operands) < 2 or c.operands[1].type != capstone.arm64.ARM64_OP_MEM:
            return None
        mem = c.operands[1].mem
        if mem.index:
            return None
        return _reg(cs, c.operands[0].reg), _reg(cs, mem.base), mem.disp

    d1, i1 = definition(breg, len(ins) - 1)
    l1 = as_ldr(d1)
    if not l1 or l1[2] % 4 or l1[1] is None:
        return None
    d2, i2 = definition(l1[1], i1)
    l2 = as_ldr(d2)
    if not l2 or l2[2] != 0 or l2[1] is None:
        return None
    lines = [f"{c.address:#x}: {c.mnemonic} {c.op_str}" for c in (d2, d1, ins[-1])]
    return dict(slot=l1[2] // 4, off=l1[2], blr_reg=breg, vptr_reg=l1[1], recv_reg=l2[1], lines=lines)


# --------------------------------------------------------------------------------------------- classification
class Result:
    def __init__(self, cls, evidence):
        self.cls, self.evidence = cls, evidence
        self.owner = OWNERS[cls]


def norm_vptr(v):
    """a vptr as given -> (kind, address): image exe addresses stay, runtime addresses become link addresses"""
    if IMAGE_LO <= v < IMAGE_HI:
        return "image", v
    if RUN_BASE <= v < RUN_BASE + RUN_SPAN:
        return "link", v - RUN_BASE + LINK_BASE
    return "link", v


def _exe_name(am, addr):
    if am and addr in am:
        _, link, nat = am[addr]
        return nat or link or ""
    return ""


def _fmt_exe(addr, am):
    nm = _exe_name(am, addr)
    return f"{addr:#010x}" + (f" {nm}" if nm else "")


def classify(slot, vptr, syms, mem, exe=None, am=None, target_hint=None):
    """slot: index, vptr: link or image address (None: unknown), mem: .read32/.is_text, exe: ExeInfo-like
    (slots(va), rtti(va)), am: {exe address: (kind, link_symbol, native_symbol)}, target_hint: the word the call
    jumped to when the vptr is unknown. -> Result"""
    ev = [f"slot {slot} (offset {slot * 4})"]
    if vptr is None:
        if target_hint is not None:
            return _by_target(target_hint, syms, None, slot, ev + ["vptr unknown (pass --vptr from an emulator --peek)"],
                              mem)
        return Result("other", ev + ["no vptr: pass --vptr (an emulator --peek of the receiver's first word)"])
    kind, vptr = norm_vptr(vptr)
    if kind == "image":
        ev.append(f"vptr {vptr:#010x} is an exe image vftable (stored by hand, no native vtable)")
        if exe is not None:
            col = exe.rtti(vptr)
            ev.append(f"RTTI locator at vptr-4: {col}" if col else "no RTTI complete-object locator at vptr-4")
            tg = exe.slots(vptr)
            ev.append(f"exe vftable has {len(tg)} slots")
            if slot < len(tg):
                ev.append(f"exe slot {slot} (what the call ran): {_fmt_exe(tg[slot], am)}")
            else:
                ev.append(f"exe slot {slot} is past the exe vftable's end")
            if 0 < slot <= len(tg):
                ev.append(f"exe slot {slot - 1} (meant by a minus-one declaration): {_fmt_exe(tg[slot - 1], am)}")
        if am and vptr in am and am[vptr][0] == "V":
            ev.append(f"addrmap maps {vptr:#010x} to native vtable {am[vptr][2] or am[vptr][1]} (the stored vptr is still the image address)")
        return Result("D26c image vptr", ev)
    vt = syms.vtable_containing(vptr)
    if vt is None:
        f = syms.func_containing(vptr)
        ev.append(f"vptr {vptr:#010x} is in no native _ZTV object" + (" (inside code)" if f else ""))
        return Result("other", ev)
    va, size, name = vt
    end = va + size
    nslots = (end - vptr) // 4
    ev.append(f"vptr {vptr:#010x} = {name}+{vptr - va:#x} ({nslots} slots to the end of the {size}-byte object)")
    saddr = vptr + 4 * slot
    word = mem.read32(saddr) if saddr < end else mem.read32(saddr)
    if saddr >= end:
        ev.append(f"slot {slot} at {saddr:#010x} is past the vtable's end ({end:#010x}); the word there is "
                  + (f"{word:#x}" if word is not None else "unreadable"))
        return Result("short vtable", ev)
    ev.append(f"slot {slot} word = {word:#x}" if word is not None else f"slot {slot} word unreadable")
    return _by_target(word, syms, (vptr, mem), slot, ev, mem, am)


def _by_target(word, syms, pair_ctx, slot, ev, mem, am=None):
    if word is None or word == 0 or not mem.is_text(word):
        ev.append("the word is not code (0, an offset-to-top, a typeinfo or data): the vtable is shorter than the slot")
        return Result("short vtable", ev)
    names = syms.names_at(word)
    ev.append(f"slot symbol: {syms.describe(word)}" + (f" (aliases: {', '.join(n for n in names)})" if len(names) > 1 else ""))
    if any(n.startswith("__fnvnx_vtrap_") for n in names):
        return Result("trap", ev + ["virtual defined nowhere (vtrap stub)"])
    if any(THISLESS.match(n) for n in names):
        return Result("this-less slot", ev + ["free function in a virtual slot: this arrives as the first argument"])
    if pair_ctx is not None:
        vptr, _ = pair_ctx
        w0, w1 = mem.read32(vptr), mem.read32(vptr + 4)
        n0, n1 = syms.names_at(w0) if w0 else [], syms.names_at(w1) if w1 else []
        pair = any(re.search(r"D[12]Ev$", n) for n in n0) and any(re.search(r"D0Ev$", n) for n in n1)
        ev.append(("D1/D0 destructor pair at slots 0/1: " if pair else "no D1/D0 pair at slots 0/1: ")
                  + f"{syms.describe(w0) if w0 else '-'} / {syms.describe(w1) if w1 else '-'}")
        if pair and slot >= 1:
            nxt = mem.read32(vptr + 4 * (slot + 1))
            landed = f"landed on native slot {slot} = exe slot {slot - 1}: {syms.describe(word)}"
            lex = [a for a, (_, lk, nt) in (am or {}).items() if nt in names or lk in names]
            ev.append(landed + (f" (exe {lex[0]:#010x})" if lex else ""))
            if nxt and mem.is_text(nxt):
                nn = syms.names_at(nxt)
                nex = [a for a, (_, lk, nt) in (am or {}).items() if nt in nn or lk in nn]
                ev.append(f"a caller in exe order meant exe slot {slot} = native slot {slot + 1}: {syms.describe(nxt)}"
                          + (f" (exe {nex[0]:#010x})" if nex else ""))
            ev.append("the caller's declaration (placeholder at slot 0, no virtual destructor) is not visible in the "
                      "binary: confirm with callonly_vt.py")
            return Result("D26b call-only", ev)
    return Result("other", ev + ["the slot holds a normal function in a vtable without a destructor-pair shift"])


# --------------------------------------------------------------------------------------------- the exe
class ExeInfo:
    """image vftables of FalloutNV.exe (loader/tools/fnvnx_pe.py)"""

    def __init__(self, path):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "loader", "tools"))
        import fnvnx_pe
        with open(path, "rb") as f:
            self.image = fnvnx_pe.Image(fnvnx_pe.PE(f.read()))

    def slots(self, va):
        return self.image.vtable_slots(va)

    def rtti(self, va):
        col = self.image.read32(va - 4)
        if not col or self.image.read32(col) != 0:
            return None
        td = self.image.read32(col + 12)
        s = self.image.string(td + 8, 256) if td else None
        return f"locator {col:#010x}, type {s[0].decode('latin-1')}" if s else None


def load_addrmap(path):
    am = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if ln.startswith("#"):
                continue
            c = ln.rstrip("\n").split("\t")
            if len(c) >= 4 and re.fullmatch(r"[0-9a-fA-F]{8}", c[0]):
                am[int(c[0], 16)] = (c[1], c[2], c[3])
    return am


# --------------------------------------------------------------------------------------------- driver
def locate_site(frames, order, syms, mem, site_arg=None):
    """-> (blr_addr, vcall dict, labels tried) for the first call site that is a virtual call"""
    tried = []
    cands = []
    if site_arg:
        m = re.fullmatch(r"(\S+?)\+(0x[0-9a-f]+)", site_arg)
        if m and m.group(1) in syms.by_name:
            cands.append(("--site", syms.by_name[m.group(1)][0] + int(m.group(2), 16), site_arg))
        else:
            cands.append(("--site", int(site_arg, 0), site_arg))
    else:
        for label in ["lr"] + [o for o in order if o.startswith("#")]:
            if label in frames:
                a, why = resolve_frame(frames[label], syms)
                if a is not None:
                    cands.append((label, a, f"{frames[label][1]}+{frames[label][2]:#x}" + (f" [{why}]" if why else "")))
    for label, ra, desc in cands:
        blr = ra - 4
        f = syms.func_containing(blr)
        vc = find_vcall(mem.read, blr, f[0] if f else None)
        tried.append(f"{label} {desc}: " + ("virtual call" if vc else "not a virtual call"))
        if vc:
            return blr, vc, tried, desc
    return None, None, tried, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", nargs="?", help="emulator log with a '== trace' fault block")
    ap.add_argument("--elf", required=True, help="guest ELF (link addresses; symbols from nm)")
    ap.add_argument("--addrmap", help="link out/addrmap.tsv (names exe functions in the evidence)")
    ap.add_argument("--exe", help="FalloutNV.exe (RTTI and slots of image vftables)")
    ap.add_argument("--vptr", type=lambda s: int(s, 0), help="receiver's vptr (link, runtime or exe address)")
    ap.add_argument("--slot", type=int, help="slot index, instead of decoding the call site")
    ap.add_argument("--site", help="call site: the return address after the blr, SYMBOL+0xOFF or an ELF address")
    ap.add_argument("--nm", default="aarch64-linux-gnu-nm")
    a = ap.parse_args(argv)

    nm = subprocess.run([a.nm, "-S", "--defined-only", a.elf], capture_output=True, text=True, check=True).stdout
    syms = Syms.from_nm(nm)
    mem = ElfMem(a.elf)
    am = load_addrmap(a.addrmap) if a.addrmap else None
    exe = ExeInfo(a.exe) if a.exe else None
    frames, regs, order = {}, {}, []
    if a.log:
        with open(a.log, encoding="utf-8", errors="replace") as f:
            frames, regs, order = parse_log(f.read())
        if not frames:
            print("slot_class: no '== trace' fault block in the log", file=sys.stderr)

    slot, hint, site_lines = a.slot, None, []
    if slot is None:
        blr, vc, tried, desc = locate_site(frames, order, syms, mem, a.site)
        site_lines = tried
        if vc is None:
            print("class: other (owner: triage)\n  no virtual call found at the candidate call sites:")
            for t in tried:
                print("   ", t)
            return 1
        slot = vc["slot"]
        site_lines = tried + [f"    {l}" for l in vc["lines"]] + [f"receiver x{vc['recv_reg']}, vptr x{vc['vptr_reg']}, "
                                                    f"slot load x{vc['blr_reg']}"]
        r = regs.get(f"x{vc['blr_reg']}")
        if r is not None and tried and tried[0].startswith("lr") and "virtual call" in tried[0].split(": ", 1)[1][:12]:
            hint = r
            tgt = r - RUN_BASE + LINK_BASE if RUN_BASE <= r < RUN_BASE + RUN_SPAN else r
            site_lines.append(f"log x{vc['blr_reg']} = {r:#x} -> {syms.describe(tgt)} (the call's target, if the callee left it)")
            hint = tgt
    res = classify(slot, a.vptr, syms, mem, exe, am, target_hint=hint if a.vptr is None else None)
    print(f"class: {res.cls}\nowner: {res.owner}")
    for ln in site_lines + res.evidence:
        print("  " + ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
