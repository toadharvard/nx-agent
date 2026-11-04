#!/usr/bin/env python3
"""Structural checks of an fnv-nx Switch package (toolchain/package/build.sh), without hardware.

    python3 -I tests/s5/check_package.py <sd dir> [--program-id <16 hex>] [--elf <guest ELF>] [--emulate]

<sd dir> is the SD card tree: switch/fnv/{fnv.nro,fnv-exefs.nsp,guest.nso} (NX1 launcher layout, fnvnx/sdlayout.h), or
the older atmosphere/contents/<program id>/exefs.nsp + fnvnx/guest.nso. Checks:
  exefs.nsp   PFS0 with exactly main + main.npdm;
  main.npdm   META/ACID/ACI0; is_64_bit, 32BitNoReserved; program id = the folder's and inside the ACID range;
              kernel capabilities decoded: thread priorities and cores cover the main thread's, every SVC the host
              (toolchain/host, startup/host, libnx basics) calls is enabled, application type, handle table,
              kernel version, at most one debug flag;
  main        NSO (LP64 host): magic, segment hashes, text at 0, segments page-aligned, ordered and disjoint,
              MOD0 at the offset text+4 names;
  guest.nso   NSO: hashes, the FNXG header (magic, ABI = FNVNX_HOSTAPI_VERSION, size = the image incl. bss, flags),
              and the decisions host.c's load_guest() makes, mirrored here: a relocatable guest needs a free
              range above the reserved window inside the small map, a fixed or pinned one its link range; the
              range must not overlap 0x00400000..0x02100000 and must end below 0x40000000;
  --elf       the guest ELF32: AArch64 ILP32, PIE (DF_1_PIE) with PT_DYNAMIC, PT_LOAD segments inside the link
              range and outside 0x00400000..0x02100000, dynamic relocations only R_AARCH64_P32_RELATIVE (RELA/RELR; NONE is skipped),
              no text relocations, segments identical to the NSO's;
  --emulate   loads guest.nso in tests/s5/emu_guest.py (the guest side of the load: header, hashes, self
              relocation, runtime start) and expects the guest's first boot line.
Exit status 0 when every check passes; each failure is printed as FAIL.
"""
import argparse
import hashlib
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, os.path.join(ROOT, "toolchain", "tools"))
import build_pfs0  # noqa: E402
import elf2nso  # noqa: E402
import npdmtool  # noqa: E402
import nxelf  # noqa: E402

RESERVED = (0x00400000, 0x02100000)
SMALL_MAP = (0x00200000, 0x40000000)
PAGE = 0x1000
# SVCs the host module uses (toolchain/host/host.c + host_v*.inc, startup/host/reserve_nx.c) and libnx's runtime
SVC_NEEDED = {
    "SetHeapSize": 0x01, "MapMemory": 0x04, "UnmapMemory": 0x05, "QueryMemory": 0x06, "ExitProcess": 0x07,
    "CreateThread": 0x08, "StartThread": 0x09, "ExitThread": 0x0A, "SleepThread": 0x0B, "SetThreadPriority": 0x0D,
    "SetThreadCoreMask": 0x0F, "CloseHandle": 0x16, "WaitSynchronization": 0x18, "ArbitrateLock": 0x1A,
    "ArbitrateUnlock": 0x1B, "WaitProcessWideKeyAtomic": 0x1C, "SignalProcessWideKey": 0x1D, "GetSystemTick": 0x1E,
    "ConnectToNamedPort": 0x1F, "SendSyncRequest": 0x21, "Break": 0x26, "OutputDebugString": 0x27, "GetInfo": 0x29,
    "WaitForAddress": 0x34, "SignalToAddress": 0x35, "CreateSession": 0x40, "ReplyAndReceive": 0x43,
    "SetProcessMemoryPermission": 0x73, "MapProcessCodeMemory": 0x77, "UnmapProcessCodeMemory": 0x78,
}

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failures.append(what)
    return cond


def hostapi_version():
    text = open(os.path.join(ROOT, "toolchain", "include", "fnvnx", "hostapi.h")).read()
    return int(re.search(r"#define FNVNX_HOSTAPI_VERSION\s+(\d+)u", text).group(1))


def decode_caps(caps):
    """kernel capability descriptors -> dict (type by the number of trailing one bits)"""
    out = {"syscalls": set(), "kernel_flags": None, "application_type": None, "handle_table_size": None,
           "kernel_version": None, "debug_flags": None, "other": []}
    for c in caps:
        ones = (~c & (c + 1)).bit_length() - 1   # trailing ones
        v = c >> (ones + 1)
        if ones == 3:
            lo, hi = v & 0x3F, (v >> 6) & 0x3F
            out["kernel_flags"] = {"lowest_priority": lo, "highest_priority": hi, "min_core": (v >> 12) & 0xFF,
                                   "max_core": (v >> 20) & 0xFF}
        elif ones == 4:
            idx, mask = (v >> 24) & 7, v & 0xFFFFFF
            out["syscalls"] |= {idx * 24 + b for b in range(24) if mask >> b & 1}
        elif ones == 13:
            out["application_type"] = v & 7
        elif ones == 14:
            out["kernel_version"] = v & 0xFFFF
        elif ones == 15:
            out["handle_table_size"] = v & 0x3FF
        elif ones == 16:
            out["debug_flags"] = v & 7
        else:
            out["other"].append((ones, c))
    return out


def check_npdm(buf, program_id, nso_main):
    d = npdmtool.dump_npdm(buf)
    check(d["is_64_bit"], "npdm: is_64_bit")
    check(d["address_space"] == "32BitNoReserved", f"npdm: address space {d['address_space']} (want 32BitNoReserved)")
    lo, hi = d["program_id_range"]
    if program_id is not None:
        check(d["program_id"] == program_id, f"npdm: ACI0 program id {d['program_id']:016x} = folder {program_id:016x}")
    check(lo <= d["program_id"] <= hi, f"npdm: program id inside the ACID range {lo:016x}..{hi:016x}")
    caps = decode_caps(d["kernel_caps"])
    kf = caps["kernel_flags"]
    if check(kf is not None, "npdm: kernel_flags present"):
        check(kf["highest_priority"] <= d["main_thread_priority"] <= kf["lowest_priority"],
              f"npdm: main thread priority {d['main_thread_priority']} in {kf['highest_priority']}..{kf['lowest_priority']}")
        check(kf["min_core"] <= d["default_cpu_id"] <= kf["max_core"],
              f"npdm: default core {d['default_cpu_id']} in {kf['min_core']}..{kf['max_core']}")
        # toolchain/host/placement.h: the main thread on core 0, the game on 0-2, the core 3 opt-in (fnvnx.ini
        # core3=1) on 1-3, so the process must be allowed cores 0..3
        check(d["default_cpu_id"] == 0 and kf["min_core"] == 0 and kf["max_core"] == 3,
              f"npdm: cores {kf['min_core']}..{kf['max_core']}, default {d['default_cpu_id']} (want 0..3, default 0: "
              "core 3 for the placement opt-in)")
    missing = sorted(n for n, i in SVC_NEEDED.items() if i not in caps["syscalls"])
    check(not missing, f"npdm: {len(caps['syscalls'])} SVCs enabled, all {len(SVC_NEEDED)} the host needs"
          + (f" (missing: {', '.join(missing)})" if missing else ""))
    check(caps["application_type"] == 1, f"npdm: application type {caps['application_type']} (1 = application)")
    check(caps["handle_table_size"] and caps["handle_table_size"] >= 256,
          f"npdm: handle table size {caps['handle_table_size']}")
    check(caps["kernel_version"] is not None, f"npdm: min kernel version {caps['kernel_version']:#x}"
          if caps["kernel_version"] is not None else "npdm: min kernel version present")
    df = caps["debug_flags"] or 0
    check(bin(df).count("1") <= 1, f"npdm: debug flags {df:#x} (at most one)")
    check(d["main_thread_stack_size"] >= 0x40000 and d["main_thread_stack_size"] % PAGE == 0,
          f"npdm: main thread stack {d['main_thread_stack_size']:#x}")


def check_nso_layout(info, what):
    segs = info["segments"]
    check(all(s["hash_ok"] for s in segs), f"{what}: segment SHA-256 hashes")
    check(segs[0]["dst"] == 0, f"{what}: text at offset 0")
    ends = 0
    ok = True
    for s in segs:
        ok &= s["dst"] % PAGE == 0 and s["dst"] >= ends
        ends = s["dst"] + s["size"] + (s.get("bss", 0) if s["name"] == "data" else 0)
    check(ok, f"{what}: segments page-aligned, in order, disjoint")
    return segs


def check_host_nso(buf):
    info = elf2nso.parse_nso(buf)
    segs = check_nso_layout(info, "main (host)")
    text = segs[0]["bytes"]
    mod0 = struct.unpack_from("<I", text, 4)[0]
    data = b"".join(s["bytes"] for s in segs)
    flat = bytearray(segs[2]["dst"] + segs[2]["size"])
    for s in segs:
        flat[s["dst"]:s["dst"] + s["size"]] = s["bytes"]
    check(flat[mod0:mod0 + 4] == b"MOD0", f"main (host): MOD0 at {mod0:#x}")
    del data


def check_guest_nso(buf):
    info = elf2nso.parse_nso(buf)
    segs = check_nso_layout(info, "guest.nso")
    text, ro, data = segs
    magic, abi, base, size, flags = struct.unpack_from("<IIIII", text["bytes"], 8)
    check(magic == elf2nso.GUEST_MAGIC, "guest.nso: FNXG magic at text+8")
    want_abi = hostapi_version()
    check(abi == want_abi, f"guest.nso: ABI {abi} = FNVNX_HOSTAPI_VERSION {want_abi}")
    span = (data["dst"] + data["size"] + data["bss"] + PAGE - 1) & ~(PAGE - 1)
    check(size == span, f"guest.nso: header size {size:#x} = image incl. bss {span:#x}")
    relocatable, pinned = bool(flags & 1), bool(flags & 2)
    kind = ("pinned" if pinned else "relocatable") if relocatable else "fixed"
    print(f"     guest: link base {base:#x}, {size:#x} bytes ({size >> 20} MiB), {kind}")
    # host.c load_guest(): relocatable -> pick_guest_base() (first free range from RESERVED_END), else w[4]
    if relocatable and not pinned:
        check(RESERVED[1] + size <= SMALL_MAP[1], "host loader: a relocatable guest fits above the reserved window")
    else:
        check(base % PAGE == 0 and base >= SMALL_MAP[0] and base + size <= SMALL_MAP[1],
              f"host loader: link range {base:#x}..{base + size:#x} inside the small map")
        check(not (base < RESERVED[1] and base + size > RESERVED[0]),
              "host loader: link range outside 0x00400000..0x02100000")
    return info, base, size


def check_guest_elf(path, nso_info, base, size):
    e = nxelf.read(path)
    check(not e.is64, "guest ELF: ELF32 (ILP32)")
    dyn = e.dynamic()
    check(bool(dyn), "guest ELF: PT_DYNAMIC")
    check(bool(dyn.get(0x6FFFFFFB, 0) & 0x08000000) or e.type == 3, "guest ELF: PIE (DF_1_PIE)")
    check(0x16 not in dyn and not dyn.get(0x1E, 0) & 0x4, "guest ELF: no text relocations")
    loads = e.loads()
    inside = all(base <= p.vaddr and p.vaddr + p.memsz <= base + size for p in loads)
    check(inside, f"guest ELF: {len(loads)} PT_LOAD segments inside the link range")
    clear = all(p.vaddr + p.memsz <= RESERVED[0] or p.vaddr >= RESERVED[1] for p in loads)
    check(clear, "guest ELF: PT_LOAD segments outside 0x00400000..0x02100000")
    rel = e.relocations()   # runtime.c's self-relocation applies P32_RELATIVE and skips NONE, nothing else
    bad = {t for _, t, _, _ in rel if t not in (nxelf.R_AARCH64_P32_RELATIVE, nxelf.R_AARCH64_NONE)}
    nones = sum(1 for _, t, _, _ in rel if t == nxelf.R_AARCH64_NONE)
    check(not bad, f"guest ELF: {len(rel)} dynamic relocations, all R_AARCH64_P32_RELATIVE ({nones} NONE)"
          + (f" (also {sorted(bad)})" if bad else ""))
    same = all(hashlib.sha256(e.segment_bytes(p)).digest() == hashlib.sha256(s["bytes"]).digest()
               for p, s in zip(loads[1:], nso_info["segments"][1:]))
    check(same, "guest ELF: rodata and data segments identical to guest.nso's")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("sd")
    ap.add_argument("--program-id")
    ap.add_argument("--elf")
    ap.add_argument("--emulate", action="store_true")
    a = ap.parse_args(argv)
    launcher = os.path.join(a.sd, "switch", "fnv", "fnv-exefs.nsp")
    if os.path.isfile(launcher):
        # NX1 layout (toolchain/include/fnvnx/sdlayout.h): the launcher fnv.nro installs the exefs for the title it
        # was started from, and ldr rewrites the NPDM's program id to that title's, so no folder fixes the id.
        tid = None
        check(not os.path.isdir(os.path.join(a.sd, "atmosphere")), "sd: no atmosphere/ in the package (the launcher installs the override)")
        check(os.path.isfile(os.path.join(a.sd, "switch", "fnv", "fnv.nro")), "sd: switch/fnv/fnv.nro")
        nsp = open(launcher, "rb").read()
        gpath = os.path.join(a.sd, "switch", "fnv", "guest.nso")
    else:
        contents = os.path.join(a.sd, "atmosphere", "contents")
        tids = sorted(os.listdir(contents)) if os.path.isdir(contents) else []
        check(len(tids) == 1, f"sd: one exefs override under atmosphere/contents ({', '.join(tids) or 'none'})")
        if not tids:
            return 1
        tid = tids[0]
        check(re.fullmatch(r"[0-9a-f]{16}", tid) is not None, f"sd: program id folder {tid} is 16 lower-case hex digits")
        if a.program_id:
            check(tid == f"{int(a.program_id, 16):016x}", f"sd: folder = requested program id {a.program_id}")
        nsp = open(os.path.join(contents, tid, "exefs.nsp"), "rb").read()
        gpath = os.path.join(a.sd, "fnvnx", "guest.nso")
    files = {n: nsp[o:o + s] for n, o, s in build_pfs0.parse_pfs0(nsp)}
    check(sorted(files) == ["main", "main.npdm"], f"exefs.nsp: {sorted(files)}")
    if "main.npdm" in files:
        check_npdm(files["main.npdm"], int(tid, 16) if tid else None, files.get("main"))
    if "main" in files:
        check_host_nso(files["main"])
    if check(os.path.isfile(gpath), f"sd: {os.path.relpath(gpath, a.sd)}"):
        info, base, size = check_guest_nso(open(gpath, "rb").read())
        if a.elf:
            check_guest_elf(a.elf, info, base, size)
        if a.emulate:
            r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "emu_guest.py"), gpath, "--budget", "20000000",
                                "--expect", "fnvnx boot: early init"], capture_output=True, text=True, timeout=1200)
            out = r.stdout + r.stderr
            check("fnvnx boot: early init" in out and "hash mismatch" not in out and "not an fnv-nx guest" not in out,
                  "emulator: guest.nso loads, relocates itself and starts the boot")
    print(f"{'PASS' if not failures else 'FAIL'}: {len(failures)} failed checks")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
