#!/usr/bin/env python3
"""fnv-nx P6n: run perf/native_drv.nso (ILP32 AArch64 guest) under Unicorn (S5's emulated host, tests/s5/emu_guest.py)
and count the AArch64 instructions it executes between measurement marks.

    python3 emu_count.py <native_drv.nso> <stream.nxd3d> <work dir> [--dry] [--fif N] [--json out.json]
                         [--profile <mark id> --elf <native_drv.elf>]   per-function counts from that mark on (2 marks)

Counter: a UC_HOOK_BLOCK hook adds size/4 per translated basic block entered (AArch64 instructions are 4 bytes;
a block is entered as a whole unless an exception cuts it, which this guest does not do between marks).
Marks: the guest calls ext_call(module 0x50, id); the current count is recorded under id. Every instruction of
the guest is counted (the front end, translator, renderer, newlib's memcpy/memset/malloc, the stream decoder);
host calls (the S5 slots) count as their one stub instruction. The decoder's own cost is measured with --dry and
subtracted by perf/report.py.
"""
import json, os, shutil, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "tests", "s5"))
import emu_guest  # noqa: E402
from unicorn import UC_HOOK_BLOCK  # noqa: E402

PERF_EXT_MODULE = 0x50


class CountEmu(emu_guest.Emu):
    def start_count(self, profile_from=None):
        self.insns = 0
        self.marks = {}
        self.prof = {}                      # block address -> instructions, after mark profile_from
        self.profiling = False
        self.profile_from = profile_from

        def on_block(uc, addr, size, _):
            self.insns += size >> 2
            if self.profiling:
                self.prof[addr] = self.prof.get(addr, 0) + (size >> 2)
        self.uc.hook_add(UC_HOOK_BLOCK, on_block)

    def h_ext_call(self, module, fn, args, nargs, *_):
        if module == PERF_EXT_MODULE:
            self.marks[fn] = self.insns
            if self.profile_from is not None:
                self.profiling = self.profile_from <= fn < self.profile_from + 2
            return 0
        return super().h_ext_call(module, fn, args, nargs)


def symbolize(prof, elf, base, link_base=0):
    """per-function instruction counts (nm of the guest ELF; the NSO runs at base, the ELF links at link_base)"""
    import bisect, subprocess
    nm = shutil.which("aarch64-none-elf-nm") or "aarch64-linux-gnu-nm"
    out = subprocess.run([nm, "-n", "-C", "--defined-only", elf], capture_output=True, text=True).stdout
    syms = []
    for line in out.splitlines():
        p = line.split(" ", 2)
        if len(p) == 3 and p[1] in "tTwW":
            syms.append((int(p[0], 16), p[2]))
    addrs = [a for a, _ in syms]
    agg = {}
    for addr, n in prof.items():
        i = bisect.bisect_right(addrs, addr - base + link_base) - 1
        name = syms[i][1] if i >= 0 else "?"
        agg[name] = agg.get(name, 0) + n
    return dict(sorted(agg.items(), key=lambda kv: -kv[1]))


def count(nso, stream, work, dry=False, fif=3, profile_from=None, elf=None):
    os.makedirs(os.path.join(work, "perf"), exist_ok=True)
    shutil.copyfile(stream, os.path.join(work, "perf", "stream.nxd3d"))
    with open(os.path.join(work, "perf", "args.txt"), "w") as f:
        f.write("sdmc:/perf/stream.nxd3d %d %d\n" % (1 if dry else 0, fif))
    emu = CountEmu(nso, os.path.abspath(work))
    emu.budget = 40_000_000_000
    emu.start_count(profile_from)
    rc = emu.run()
    res = open(os.path.join(work, "perf", "result.txt")).read().strip()
    r = {"rc": rc, "result": res, "total": emu.insns, "marks": {str(k): v for k, v in sorted(emu.marks.items())}}
    if profile_from is not None and elf:
        r["profile"] = symbolize(emu.prof, elf, emu.base, emu.link_base)
    return r


def main():
    a = sys.argv[1:]
    if len(a) < 3:
        raise SystemExit(__doc__)
    dry = "--dry" in a
    fif = int(a[a.index("--fif") + 1]) if "--fif" in a else 3
    pf = int(a[a.index("--profile") + 1]) if "--profile" in a else None
    elf = a[a.index("--elf") + 1] if "--elf" in a else None
    r = count(a[0], a[1], a[2], dry, fif, pf, elf)
    print(r["result"])
    print("rc=%s instructions=%d marks=%s" % (r["rc"], r["total"], r["marks"]))
    for k, v in list(r.get("profile", {}).items())[:25]:
        print("  %10d  %s" % (v, k))
    if "--json" in a:
        json.dump(r, open(a[a.index("--json") + 1], "w"), indent=1)
    return 0 if r["rc"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
