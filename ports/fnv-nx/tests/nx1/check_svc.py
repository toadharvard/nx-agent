#!/usr/bin/env python3
"""NX1 check 3: SVCs, IPC services and instructions the console binaries use.

    check_svc.py [--guest-elf fnvnx.elf] [--host-elf host.elf] [--nro-elf fnv.elf] [--require host,nro,guest]

Every executable word of each ELF's executable PT_LOAD segments is decoded at the encoding level (AArch64 is
fixed-width, the scan is exact for code and conservative for data placed in text):
  svc      guest: none at all (the ILP32 guest reaches the kernel only through the host API). host and launcher:
           each `svc #imm` must be in APP_SVCS, the SVCs a retail application process uses; debug, process
           creation, device address space, secure monitor and register access SVCs are refused even though our NPDM
           and hbloader's grant "all" (the kernel or sm refuse them to an application on retail, or they are a bug);
  insn     nothing that traps at EL0 on Horizon: hvc, smc, eret, hlt, wfi, msr DAIF*/SPSel and other PSTATE writes,
           SYS ops except dc zva/cvac/cvau/civac/cvap and ic ivau, system registers except nzcv, fpcr, fpsr,
           tpidr_el0, tpidrro_el0 (read), dczid_el0, cntfrq_el0 and cntpct_el0 (read). cntvct_el0 and ctr_el0 are
           refused too: Mesosphere does not open them to EL0 (CNTKCTL_EL1.EL0VCTEN, SCTLR_EL1.UCT);
           brk is counted (trap stubs), not refused: it ends in the crash handler with a log;
  services every IPC service name in the host and launcher (string literals of the form libnx's sm uses) is in
           APP_SERVICES, the services an application (appletOE side) may open; system-only services (pm:*, ns:am2,
           vi:m, nvdrv:s, ...) fail.
"""
from __future__ import annotations

import argparse
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "toolchain", "tools"))
import nxelf  # noqa: E402
from check_nro import Checks  # noqa: E402

# SVCs of a retail application: memory, threads, sync, IPC, info, code memory (hbloader's NRO loading, our guest
# loader), transfer/shared memory, the process-memory calls on our own handle (svcMapProcessCodeMemory & co.).
APP_SVCS = {
    0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x12, 0x13,
    0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26,
    0x27, 0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x2D, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x40, 0x41, 0x42, 0x43,
    0x44, 0x45, 0x4B, 0x4C, 0x50, 0x51, 0x52, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78,
}
SVC_NAMES = {0x2E: "GetDebugFutureThreadInfo", 0x2F: "GetLastThreadInfo", 0x3C: "KernelDebug", 0x3D: "ChangeKernelTraceState",
             0x4E: "ReadWriteRegister", 0x4D: "SleepSystem", 0x6F: "GetSystemInfo", 0x7F: "CallSecureMonitor",
             0x79: "CreateProcess", 0x7B: "TerminateProcess", 0x65: "GetProcessList", 0x60: "DebugActiveProcess"}

# Services an application may open with sm (libnx names). Anything else needs a review: add it here with the reason.
APP_SERVICES = {
    "sm:", "fsp-srv", "fsp-ldr", "appletOE", "appletAE", "hid", "hid:sys", "irs", "audren:u", "audout:u", "audin:u",
    "audctl", "nvdrv", "nvdrv:a", "nvmemp", "vi:u", "vi:s", "set", "set:sys", "set:cal", "time:u", "time:a", "time:s",
    "apm", "apm:p", "psm", "pl:u", "pl:s", "acc:u0", "acc:u1", "fatal:u", "lr", "ldr:ro", "ro:1", "csrng", "spl:",
    "pctl", "pctl:s", "pctl:a", "friend:u", "nifm:u", "bsd:u", "bsd:s", "sfdnsres", "ssl", "nsd:u", "socket:u",
    "caps:u", "caps:su", "caps:sc", "caps:ss", "lbl", "mmu", "mm:u", "aud:a", "aud:d", "hidbus", "xcd:sys", "bpc",
    "ts", "clkrst", "pcv", "usb:ds", "usb:hs", "btm:u", "bt", "btdrv", "btm", "btm:sys", "ns:am", "ns:ec", "ns:su",
    "ns:vm", "ns:dev", "ns:web", "ns:rid", "ns:ro", "aoc:u", "lm", "pm:info", "ectx:r", "ectx:w", "ovln:rcv",
    "nfp:user", "nfc:user", "mii:u", "mii:e", "prepo:u", "prepo:a", "prepo:a2", "erpt:c", "es", "ncm", "pgl",
    "applet", "appletAE", "ins:r", "pdm:ntfy", "pdm:qry", "omm", "notif:a", "notif:s", "capmtp", "fsp-pr",
}
SERVICE_RX = re.compile(rb"(?<![\w:.-])([a-z][a-z0-9_-]{1,7}(?::[a-z0-9]{1,4})?)\x00")
# libnx's service names in the order smGetService sees them; we only look at names in ELF rodata that libnx or our
# code passes to smGetService/smGetServiceWrapper: the candidates are matched against this known-name corpus.
KNOWN_SERVICES = APP_SERVICES | {"pm:shell", "pm:dmnt", "pm:bm", "ns:am2", "vi:m", "nvdrv:s", "nvdrv:t", "fsp-ldr",
                                 "ldr:pm", "ldr:shel", "ldr:dmnt", "sm:m", "dmnt:cht", "set:fd", "spl:mig", "spl:fs",
                                 "spl:ssl", "spl:es", "spl:manu", "hid:dbg", "pgl", "tc", "fan", "i2c", "gpio",
                                 "pinmux", "pwm", "uart", "pcie", "ldn:u", "ldn:s", "nim", "nim:eca", "olsc:u",
                                 "dauth:0", "aoc:u", "fsp-pr", "bcat:u", "bcat:s", "news:a", "news:c", "news:m",
                                 "news:p", "news:v", "npns:u", "npns:s", "acc:aa", "acc:su", "bsd:a", "sfdnsres"}
SYSTEM_ONLY = {"pm:shell", "pm:dmnt", "pm:bm", "ns:am2", "vi:m", "nvdrv:s", "nvdrv:t", "ldr:pm", "ldr:shel",
               "ldr:dmnt", "sm:m", "dmnt:cht", "set:fd", "spl:mig", "spl:fs", "spl:ssl", "spl:es", "spl:manu", "hid:dbg",
               "i2c", "gpio", "pinmux", "pwm", "uart", "pcie", "acc:aa", "acc:su"}

# system registers (op0, op1, CRn, CRm, op2) readable/writable at EL0 on Horizon
SYSREG_OK = {
    (3, 3, 4, 2, 0): "nzcv", (3, 3, 4, 4, 0): "fpcr", (3, 3, 4, 4, 1): "fpsr", (3, 3, 13, 0, 2): "tpidr_el0",
    (3, 3, 13, 0, 3): "tpidrro_el0", (3, 3, 0, 0, 7): "dczid_el0", (3, 3, 14, 0, 0): "cntfrq_el0",
    (3, 3, 14, 0, 1): "cntpct_el0",
}
SYSREG_NAMES = {(3, 3, 14, 0, 2): "cntvct_el0", (3, 3, 0, 0, 1): "ctr_el0", (3, 0, 0, 0, 0): "midr_el1",
                (3, 0, 0, 0, 5): "mpidr_el1", (3, 3, 4, 2, 1): "daif", (3, 0, 4, 2, 2): "currentel"}
SYS_OK = {(3, 7, 4, 1): "dc zva", (3, 7, 10, 1): "dc cvac", (3, 7, 11, 1): "dc cvau", (3, 7, 14, 1): "dc civac",
          (3, 7, 12, 1): "dc cvap", (3, 7, 5, 1): "ic ivau"}


def classify(w: int):
    """None for an ordinary instruction, else (kind, detail)."""
    if w & 0xFF000000 == 0xD4000000:     # exception generation
        opc, ll = (w >> 21) & 7, w & 0x1F
        imm = (w >> 5) & 0xFFFF
        if opc == 0 and ll == 1:
            return ("svc", imm)
        if opc == 0 and ll in (2, 3):
            return ("bad", "hvc" if ll == 2 else "smc")
        if opc == 1 and ll == 0:
            return ("brk", imm)
        if opc == 2 and ll == 0:
            return ("bad", "hlt")
        if opc == 5 and ll in (1, 2, 3):
            return ("bad", "dcps")
        return None
    if w in (0xD69F03E0, 0xD6BF03E0):
        return ("bad", "eret/drps")
    if w & 0xFFC00000 == 0xD5000000:     # system instruction class
        L, op0, op1 = (w >> 21) & 1, (w >> 19) & 3, (w >> 16) & 7
        crn, crm, op2, rt = (w >> 12) & 15, (w >> 8) & 15, (w >> 5) & 7, w & 31
        if op0 == 0:
            if crn == 2 and rt == 31:     # hints
                return ("bad", "wfi") if (crm, op2) == (0, 3) else None
            if crn == 3 and rt == 31:     # barriers, clrex
                return None
            if crn == 4 and rt == 31:     # msr (immediate) to PSTATE: DAIFSet/Clr, SPSel, UAO, PAN... none at EL0 here
                return ("bad", f"msr pstate op1={op1} op2={op2}")
            return ("bad", f"system op0=0 crn={crn}")
        if op0 == 1:                      # sys / sysl: dc, ic, at, tlbi
            k = (op1, crn, crm, op2)
            return None if k in SYS_OK and L == 0 else ("bad", f"sys {k}")
        k = (op0, op1, crn, crm, op2)     # mrs / msr register
        if k in SYSREG_OK:
            if k in ((3, 3, 13, 0, 3), (3, 3, 0, 0, 7), (3, 3, 14, 0, 0), (3, 3, 14, 0, 1)) and L == 0:
                return ("bad", f"msr {SYSREG_OK[k]} (read-only at EL0)")
            return None
        return ("bad", ("mrs " if L else "msr ") + SYSREG_NAMES.get(k, f"s{op0}_{op1}_c{crn}_c{crm}_{op2}"))
    return None


def symbolizer(elf: nxelf.Elf):
    syms = []
    sh = elf.section(".symtab")
    st = elf.section(".strtab")
    if sh and st:
        data, strs = elf.data[sh.offset:sh.offset + sh.size], elf.data[st.offset:st.offset + st.size]
        ent = 24 if elf.is64 else 16
        for o in range(0, len(data) - ent + 1, ent):
            if elf.is64:
                nm, info, _o, _sh, val, sz = struct.unpack_from("<IBBHQQ", data, o)
            else:
                nm, val, sz, info, _o, _sh = struct.unpack_from("<IIIBBH", data, o)
            if info & 15 == 2 and val:
                syms.append((val, sz, strs[nm:strs.find(b"\0", nm)].decode(errors="replace")))
    syms.sort()
    import bisect
    keys = [s[0] for s in syms]

    def at(addr):
        i = bisect.bisect_right(keys, addr) - 1
        if i >= 0 and addr < syms[i][0] + max(syms[i][1], 4):
            return f"{syms[i][2]}+{addr - syms[i][0]:#x}"
        return f"{addr:#x}"
    return at


def scan(path: str, role: str, c: Checks):
    elf = nxelf.read(path)
    at = symbolizer(elf)
    hits = {"svc": {}, "bad": {}, "brk": 0}
    words = 0
    for p in elf.phdrs:
        if p.type != nxelf.PT_LOAD or not p.flags & 1:
            continue
        seg = elf.data[p.offset:p.offset + p.filesz]
        n = len(seg) // 4
        words += n
        for i, w in enumerate(struct.unpack_from(f"<{n}I", seg)):
            if w & 0xFF000000 not in (0xD4000000, 0xD5000000, 0xD6000000):
                continue
            r = classify(w)
            if not r:
                continue
            kind, det = r
            if kind == "brk":
                hits["brk"] += 1
            else:
                hits[kind].setdefault(det, []).append(p.vaddr + 4 * i)
    c.ok(f"{role}.scanned", words > 0, f"{path}: no executable segment")
    if role == "guest":
        c.ok("guest.no_svc", not hits["svc"], "guest issues svc " + ", ".join(
            f"{k:#x} at {at(v[0])}" for k, v in list(hits["svc"].items())[:5]) + ": the guest must go through the host API")
    else:
        bad = {k: v for k, v in hits["svc"].items() if k not in APP_SVCS}
        c.ok(f"{role}.svc_allowlist", not bad, "; ".join(
            f"svc {k:#x} {SVC_NAMES.get(k, '')} x{len(v)} first at {at(v[0])}" for k, v in sorted(bad.items())[:6]))
        c.ok(f"{role}.svc_present", bool(hits["svc"]), "no svc at all: not a libnx binary?")
    c.ok(f"{role}.insn", not hits["bad"], "; ".join(f"{k} x{len(v)} first at {at(v[0])}" for k, v in
                                                     sorted(hits["bad"].items(), key=lambda kv: -len(kv[1]))[:6]))
    print(f"info {role}: {words} words, svc {sorted(hits['svc'])}, brk {hits['brk']}")
    if role != "guest":
        found = set()
        for p in elf.phdrs:
            if p.type == nxelf.PT_LOAD and not p.flags & 1:
                for m in SERVICE_RX.finditer(elf.data[p.offset:p.offset + p.filesz]):
                    s = m.group(1).decode()
                    if s in KNOWN_SERVICES:
                        found.add(s)
        sysonly = sorted(found & SYSTEM_ONLY)
        unknown = sorted(found - APP_SERVICES - SYSTEM_ONLY)
        c.ok(f"{role}.services_app", not sysonly, f"system-only services: {sysonly}")
        c.ok(f"{role}.services_known", not unknown, f"services not reviewed for applications: {unknown}")
        print(f"info {role}: services {sorted(found)}")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--guest-elf")
    ap.add_argument("--host-elf")
    ap.add_argument("--nro-elf")
    ap.add_argument("--require", default="")
    a = ap.parse_args(argv)
    c = Checks()
    for role, path in (("guest", a.guest_elf), ("host", a.host_elf), ("nro", a.nro_elf)):
        if path:
            scan(path, role, c)
        elif role in a.require.split(","):
            c.ok(f"{role}.present", False, f"no {role} ELF given")
    c.print()
    print(f"svc: {sum(r[1] for r in c.rows)}/{len(c.rows)} pass -> {'PASS' if c.passed and c.rows else 'FAIL'}")
    return 0 if c.passed and c.rows else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
