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
           tpidr_el0, tpidrro_el0 (read), dczid_el0, cntfrq_el0, cntpct_el0 and ctr_el0 (read: Mesosphere's
           SCTLR_EL1 0x34D5D92D sets UCT, bit 15, kern_init_loader.cpp). cntvct_el0 is refused: Mesosphere does not
           open it to EL0 (CNTKCTL_EL1.EL0VCTEN);
           brk is counted (trap stubs), not refused: it ends in the crash handler with a log;
           nothing newer than ARMv8.0 (the A57 implements ARMv8.0 + CRC + crypto only): LSE atomics (ldadd/cas/swp...),
           LOR, RCpc (ldapr/ldapur), RDM, FP16 arithmetic (scalar and vector; fcvt to/from half is v8.0), DotProd,
           JSCVT, PAuth outside the hint space, FlagM, dc cvap (POST_V80, each class checked against GNU as);
  services every IPC service name in the host and launcher is reviewed. A name is a NUL-terminated rodata string
           that libnx can request (every smGetService*/smEncodeName literal of ext/libnx), is in our corpus, or has
           the name:sub shape. It must be in APP_SERVICES or among the alternatives libnx itself picks by service type at run time (vi:m/vi:s/vi:u,
           nvdrv/nvdrv:a/:s/:t, acc:u0/u1/su, set:fd). Both NPDMs grant service_access "*", so sm grants any name;
           an unreviewed one fails because it may not exist on retail firmware or may be single-session.
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
_LIBNX_SERVICES = None


def libnx_services() -> set[str]:
    """Every service name the pinned libnx passes to smGetService*/smEncodeName (ext/libnx/nx/source), read once: the
    names a libnx binary can carry in rodata. Empty when the checkout is missing (tools/fetch.sh)."""
    global _LIBNX_SERVICES
    if _LIBNX_SERVICES is None:
        _LIBNX_SERVICES = set()
        src = os.path.join(ROOT, "ext", "libnx", "nx", "source")
        rx = re.compile(r'(?:smGetService\w*|smEncodeName)\([^;"]*"([^"]+)"')
        for dp, _, fns in os.walk(src):
            for f in fns:
                if f.endswith(".c"):
                    _LIBNX_SERVICES.update(rx.findall(open(os.path.join(dp, f), errors="replace").read()))
    return _LIBNX_SERVICES


# an sm service name is at most 8 bytes (sm's ServiceName), up to two :sub parts ("pm:shell", "nfc:mf:u"); the
# 8-byte bound is checked on the match (V1 NX1-S5: the old pattern allowed 4 characters after the colon)
SERVICE_RX = re.compile(rb"(?<![\w:.-])([a-z][a-z0-9_-]{0,7}(?::[a-z0-9_]{1,7}){0,2})\x00")
# libnx's service names in the order smGetService sees them; we only look at names in ELF rodata that libnx or our
# code passes to smGetService/smGetServiceWrapper: the candidates are matched against this known-name corpus.
KNOWN_SERVICES = APP_SERVICES | {"pm:shell", "pm:dmnt", "pm:bm", "ns:am2", "vi:m", "nvdrv:s", "nvdrv:t", "fsp-ldr",
                                 "ldr:pm", "ldr:shel", "ldr:dmnt", "sm:m", "dmnt:cht", "set:fd", "spl:mig", "spl:fs",
                                 "spl:ssl", "spl:es", "spl:manu", "hid:dbg", "pgl", "tc", "fan", "i2c", "gpio",
                                 "pinmux", "pwm", "uart", "pcie", "ldn:u", "ldn:s", "nim", "nim:eca", "olsc:u",
                                 "dauth:0", "aoc:u", "fsp-pr", "bcat:u", "bcat:s", "news:a", "news:c", "news:m",
                                 "news:p", "news:v", "npns:u", "npns:s", "acc:aa", "acc:su", "bsd:a", "sfdnsres"}
LIBNX_ALTERNATIVES = {"vi:m", "vi:s", "vi:u", "nvdrv", "nvdrv:a", "nvdrv:s", "nvdrv:t", "acc:u0", "acc:u1", "acc:su",
                      "set:fd", "set:sys", "set:cal"}
SYSTEM_ONLY = {"pm:shell", "pm:dmnt", "pm:bm", "ns:am2", "vi:m", "nvdrv:s", "nvdrv:t", "ldr:pm", "ldr:shel",
               "ldr:dmnt", "sm:m", "dmnt:cht", "set:fd", "spl:mig", "spl:fs", "spl:ssl", "spl:es", "spl:manu", "hid:dbg",
               "i2c", "gpio", "pinmux", "pwm", "uart", "pcie", "acc:aa", "acc:su"}

# system registers (op0, op1, CRn, CRm, op2) readable/writable at EL0 on Horizon
SYSREG_OK = {
    (3, 3, 4, 2, 0): "nzcv", (3, 3, 4, 4, 0): "fpcr", (3, 3, 4, 4, 1): "fpsr", (3, 3, 13, 0, 2): "tpidr_el0",
    (3, 3, 13, 0, 3): "tpidrro_el0", (3, 3, 0, 0, 7): "dczid_el0", (3, 3, 14, 0, 0): "cntfrq_el0",
    (3, 3, 14, 0, 1): "cntpct_el0",
    # ctr_el0 (read): Mesosphere's SCTLR_EL1 = 0x34D5D92D (kernel_ldr/source/kern_init_loader.cpp SctlrValue) has UCT
    # (bit 15) set, so EL0 reads of CTR_EL0 do not trap; libnx's armDCacheFlush (deko3d's flushes) reads it
    (3, 3, 0, 0, 1): "ctr_el0",
}
SYSREG_NAMES = {(3, 3, 14, 0, 2): "cntvct_el0", (3, 3, 0, 0, 1): "ctr_el0", (3, 0, 0, 0, 0): "midr_el1",
                (3, 0, 0, 0, 5): "mpidr_el1", (3, 3, 4, 2, 1): "daif", (3, 0, 4, 2, 2): "currentel"}
SYS_OK = {(3, 7, 4, 1): "dc zva", (3, 7, 10, 1): "dc cvac", (3, 7, 11, 1): "dc cvau", (3, 7, 14, 1): "dc civac",
          (3, 7, 5, 1): "ic ivau"}   # not dc cvap: ARMv8.2 FEAT_DPB, UNDEFINED on the A57 (V1 NX1-S1)

# Encoding classes of instructions newer than ARMv8.0 (UNDEFINED on the Cortex-A57 of the Switch). Each row:
# (mask, value, feature). Verified against GNU as: every row of a positive corpus assembled with
# -march=armv8.5-a+fp16+dotprod+rcpc+lse+rdm+pauth+flagm matches, no word of an ARMv8.0 corpus does.
POST_V80 = [
    (0x3F200C00, 0x38200000, "v8.1 LSE atomic (ldadd/ldclr/ldeor/ldset/ldsmax/ldumin/swp; v8.3 ldapr)"),
    (0x3FA07C00, 0x08A07C00, "v8.1 LSE cas"),
    (0xBFA07C00, 0x08207C00, "v8.1 LSE casp"),
    (0x3FBFFC00, 0x089F7C00, "v8.1 LOR ldlar/stllr"),
    (0x3F200C00, 0x19000000, "v8.4 RCpc ldapur/stlur"),
    (0xBF20F400, 0x2E008400, "v8.1 RDM sqrdmlah/sqrdmlsh (vector)"),
    (0xFF20F400, 0x7E008400, "v8.1 RDM sqrdmlah/sqrdmlsh (scalar)"),
    (0x9F60C400, 0x0E400400, "v8.2 FP16 vector arithmetic"),
    (0x9F20FC00, 0x0E009400, "v8.2 DotProd sdot/udot"),
    (0xFFFFFC00, 0x1E7E0000, "v8.3 JSCVT fjcvtzs"),
    (0xFF200400, 0xF8200400, "v8.3 PAuth ldraa/ldrab"),
    (0xFFFF0000, 0xDAC10000, "v8.3 PAuth pac*/aut*/xpac* (non-hint)"),
    (0xFE000800, 0xD6000800, "v8.3 PAuth braa/blraa/retaa/eretaa"),
    (0xFFE07C10, 0xBA000400, "v8.4 FlagM rmif"),
    (0xFFFFBC1F, 0x3A00080D, "v8.4 FlagM setf8/setf16"),
]


def fp16_scalar(w):
    """scalar FP data processing with ftype 11 (half precision): ARMv8.2 FEAT_FP16; FCVT to/from half is ARMv8.0"""
    if (w & 0x5F200000) != 0x1E200000 or ((w >> 22) & 3) != 3:
        return False
    is_fcvt = ((w >> 17) & 0xF) == 1 and ((w >> 10) & 0x1F) == 0x10
    return not is_fcvt


def post_v80(w):
    for m, v, name in POST_V80:
        if w & m == v:
            return name
    if fp16_scalar(w):
        return "v8.2 FP16 scalar arithmetic/convert"
    if w & 0xFFFFFFE0 == 0xD50B7C20:   # dc cvap (v8.2 FEAT_DPB)
        return "v8.2 DPB dc cvap"
    return None



def classify(w: int):
    """None for an ordinary instruction, else (kind, detail)."""
    newer = post_v80(w)   # the Switch's Cortex-A57 is ARMv8.0: anything newer is UNDEFINED there (V1 NX1-S1/S2)
    if newer:
        return ("bad", newer)
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
            if k in ((3, 3, 13, 0, 3), (3, 3, 0, 0, 7), (3, 3, 14, 0, 0), (3, 3, 14, 0, 1), (3, 3, 0, 0, 1)) and L == 0:
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
                    if len(s) > 8:
                        continue
                    # a service name: one libnx can request, one of our corpus, or any name:sub string (a colon
                    # name nobody listed is still a service name, V1 NX1-S3)
                    if s in KNOWN_SERVICES or s in libnx_services() or re.fullmatch(r"[a-z][a-z0-9_-]*:[a-z0-9_]+", s):
                        found.add(s)
        unknown = sorted(found - APP_SERVICES - LIBNX_ALTERNATIVES)
        c.ok(f"{role}.services_reviewed", not unknown, f"services not reviewed for an application: {unknown}")
        if found & SYSTEM_ONLY - LIBNX_ALTERNATIVES:
            print(f"info {role}: system services named (granted by service_access *): {sorted(found & SYSTEM_ONLY)}")
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
