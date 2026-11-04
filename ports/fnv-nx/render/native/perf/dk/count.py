#!/usr/bin/env python3
"""fnv-nx P6n: count the AArch64 (LP64) instructions of the deko3d backend's per-frame recording - nxgpu.c's
record() with the real libdeko3d - under Unicorn, for the .nxcap captures perf/run.sh writes.

    python3 -I count.py <work dir> <out.json>          (needs <work>/dk/dkrec.elf, <work>/out/*.nxcap,
                                                         <work>/b/n1/shaders/*.dksh)
    python3 -I count.py --one <dkrec.elf> <capture.nxcap> <shaders dir>

Counted per frame: dkCmdBufClear + dkCmdBufAddMemory + record() (stream decode, state-block replay, every dkCmdBuf*
call, dkCmdBufFinishList, dkQueueSubmitCommands, swapchain acquire/present bookkeeping). The libnx services under
it are stand-ins that return at once (perf/dk/stubs.c): the kernel/ioctl time of a real submit is not counted.
"""
import json, os, struct, subprocess, sys
from unicorn import Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_BLOCK, UC_HOOK_CODE, UC_HOOK_INTR, UC_PROT_ALL
from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_PC, UC_ARM64_REG_CPACR_EL1

INPUT = 0x70000000
STACK_TOP = 0x7F000000


def syms(elf):
    nm = "aarch64-none-elf-nm"
    out = subprocess.run([nm, elf], capture_output=True, text=True, check=True).stdout
    return {p[2]: int(p[0], 16) for p in (l.split() for l in out.splitlines()) if len(p) == 3}


def load(uc, path):
    d = open(path, "rb").read()
    phoff, = struct.unpack_from("<Q", d, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", d, 0x36)
    entry, = struct.unpack_from("<Q", d, 0x18)
    for i in range(phnum):
        t, flags, off, va, pa, fsz, msz, al = struct.unpack_from("<IIQQQQQQ", d, phoff + i * phentsize)
        if t == 1 and fsz:
            uc.mem_write(va, d[off:off + fsz])
    return entry


def pack_shaders(sdir):
    names = ["lit.vert", "land.vert", "depth.vert", "nolight_tex.vert", "lit.frag", "lit_spec.frag", "land.frag",
             "depth.frag", "nolight_tex.frag", "tex.frag"]
    out = struct.pack("<I", len(names))
    for n in names:
        b = open(os.path.join(sdir, n + ".dksh"), "rb").read()
        out += struct.pack("<II", 0 if n.endswith(".vert") else 1, len(b)) + b + b"\0" * ((-len(b)) % 8)
    return out


def run(elf, cap, sdir, profile=False):
    s = syms(elf)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)
    uc.mem_map(0x10000000, 0x50000000, UC_PROT_ALL)
    capb = open(cap, "rb").read()
    pk = pack_shaders(sdir)
    poff = 64 + ((len(capb) + 4095) & ~4095)
    blob = struct.pack("<QQQ", len(capb), poff, len(pk)).ljust(64, b"\0") + capb
    blob = blob.ljust(poff, b"\0") + pk
    uc.mem_map(INPUT, (len(blob) + 0xFFFFF) & ~0xFFFFF, UC_PROT_ALL)
    uc.mem_write(INPUT, blob)
    uc.mem_map(STACK_TOP - 0x1000000, 0x1000000, UC_PROT_ALL)
    entry = load(uc, elf)
    st = {"insns": 0, "marks": {}, "exit": None, "intr": [], "prof": {}, "on": False, "last": None}

    def on_block(u, addr, size, _):
        st["insns"] += size >> 2
        if st["on"]:
            st["prof"][addr] = st["prof"].get(addr, 0) + (size >> 2)

    def on_mark(u, addr, size, _):
        if addr == s["perf_mark"]:
            mid = u.reg_read(UC_ARM64_REG_X0)
            st["marks"][mid] = st["insns"]
            if profile:     # keep only the latest frame: reset at each frame's start mark
                if mid & 1:
                    st["prof"] = {}; st["on"] = True
                else:
                    st["on"] = False; st["last"] = dict(st["prof"])
        elif addr == s["perf_exit"]:
            st["exit"] = u.reg_read(UC_ARM64_REG_X0)
            u.emu_stop()

    def on_intr(u, intno, _):
        st["intr"].append((intno, hex(u.reg_read(UC_ARM64_REG_PC))))
        u.emu_stop()
    uc.hook_add(UC_HOOK_BLOCK, on_block)
    uc.hook_add(UC_HOOK_CODE, on_mark, begin=s["perf_mark"], end=s["perf_mark"])
    uc.hook_add(UC_HOOK_CODE, on_mark, begin=s["perf_exit"], end=s["perf_exit"])
    uc.hook_add(UC_HOOK_INTR, on_intr)
    uc.reg_write(UC_ARM64_REG_SP, STACK_TOP - 0x100)
    err = None
    try:
        uc.emu_start(entry, 0, count=20_000_000_000)
    except UcError as e:
        err = "%s at pc %#x" % (e, uc.reg_read(UC_ARM64_REG_PC))
    res = struct.unpack("<16I", uc.mem_read(s["perf_result"], 64))
    m = st["marks"]
    frames = res[7]
    per = [m[2 * f + 2] - m[2 * f + 1] for f in range(frames) if (2 * f + 2) in m]
    prof = None
    if profile and st["last"]:
        import bisect
        out = subprocess.run(["aarch64-none-elf-nm", "-n", "-C", "--defined-only", elf], capture_output=True, text=True).stdout
        sy = [(int(p[0], 16), p[2]) for p in (l.split(" ", 2) for l in out.splitlines()) if len(p) == 3 and p[1] in "tTwW"]
        ad = [a for a, _ in sy]
        agg = {}
        for a, n in st["last"].items():
            i = bisect.bisect_right(ad, a) - 1
            nm = sy[i][1] if i >= 0 else "?"
            agg[nm] = agg.get(nm, 0) + n
        prof = dict(sorted(agg.items(), key=lambda kv: -kv[1])[:40])
    return {"profile_last_frame": prof, "exit": st["exit"], "error": err, "intr": st["intr"][:3], "result": list(res[:8]), "frames": frames,
            "record_per_frame": per, "record": per[-1] if per else None,
            "last_frame": {"draws": res[2], "state_binds": res[3], "block_binds": res[4], "words": res[5],
                           "captured_blocks": res[6]}}


def main():
    a = sys.argv[1:]
    if a and a[0] == "--one":
        print(json.dumps(run(a[1], a[2], a[3]), indent=1))
        return 0
    work, out = a
    elf = os.path.join(work, "dk", "dkrec.elf")
    sdir = os.path.join(work, "b", "n1", "shaders")
    res = {"method": __doc__.strip().split("\n\n")[2], "streams": {}}
    for f in sorted(os.listdir(os.path.join(work, "out"))):
        if f.endswith(".nxcap"):
            r = run(elf, os.path.join(work, "out", f), sdir, profile=f.startswith("mix_hi"))
            if r["exit"] != 0:
                raise SystemExit("%s: %s" % (f, r))
            res["streams"][f[:-6]] = r
            print(f, r["record"], r["last_frame"])
    json.dump(res, open(out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
