#!/usr/bin/env python3
"""Deterministic executed-instruction counter for a Unicorn AArch64 guest (the owner's perf metric).

Library: attach(uc, lo, hi, spec=None, bias=0) installs one UC_HOOK_BLOCK over [lo, hi) and returns an Icount;
the emulator tells it thread(tid), mark(name, out_of_order=False) and stop(reason, **events); write(path) or report()
gives the OUT.json document ("fnvnx-icount/1", see report()). CLI: `python3 -I verify/icount.py spec ...` builds the
SPEC.json that classes (ranges, entries) are read from; run `spec -h`.

Counting rule: a block of `size` bytes adds size/4 instructions. The static facts of a block (instructions per opcode
class, per range class, entry) are computed once per (address, size) from the block's bytes and cached, so the hook
is a dict lookup plus integer adds. Counters are kept per (interval, thread id); an interval is the span between two
consecutive marks ("start" -> first mark ... last mark -> "stop"); a mark that was out of order flags the interval it
closes and the interval it opens. Thread 1 is named "main", thread N is "tN" (thread(tid, name) overrides).

Classes (A64 encodings, ARM ARM "Loads and Stores" / "System"; mask/value in OPCODES):
  ldar_stlr    every acquire/release memory op: LDAR/LDARB/LDARH, STLR/STLRB/STLRH, plus the two sub-counters
  ldapr        LDAPR/LDAPRB/LDAPRH and the LDAPUR*/STLUR* family (FEAT_LRCPC / LRCPC2)
  ldaxr_stlxr  LDAXR/LDAXRB/LDAXRH/LDAXP and STLXR/STLXRB/STLXRH/STLXP
               (plain LDXR/STXR, LDLAR/STLLR and the CAS/SWP/LD<op> atomics are NOT counted)
  dmb, dsb, isb  DMB, DSB (SSBB/PSSBB are DSB encodings) and ISB, any option; dmb/dsb/isb are not in ldar_stlr.
Range classes count instructions by the address of each instruction (a block that straddles a range edge is split
exactly). Entry classes count the blocks that START exactly at a listed address (calls of those functions, and also
a fall-through into the address if the translator split the block there). Code outside [lo, hi) is not counted.

Exactness limits (also in OUT.json "exactness"): a block hook fires at block entry, so a block that does not run to its
end is counted in full: a fault inside a block, an emu_stop inside a block (and the re-entry that then starts a new
block at the resume pc counts that part twice), a count=/timer-limited slice that ends inside a block. stop() takes
the emulator's event counts (faults, budget slices, ...) as `events` and puts them in OUT.json; nothing else is
approximate. Threads that switch at host calls (blocks ended by a branch to a host page) are exact.
"""
import json
import re
import struct
import subprocess
import sys

FORMAT = "fnvnx-icount/1"
OPCODE_NAMES = ("ldar_stlr", "ldapr", "ldaxr_stlxr", "dmb", "dsb", "isb")
# (name, mask, value); word & mask == value. Bits: size[31:30] 001000 o2[23] L[22] o1[21] Rs[20:16] o0[15] Rt2[14:10]
OPCODES = (
    # LDAR/STLR (+B/H): 00 001000 1 L 0 11111 1 11111 Rn Rt (size, L free; o2=1 o1=0 Rs=Rt2=31 o0=1)
    ("ldar_plain", 0x3FBFFC00, 0x089FFC00),
    # LDAPR/LDAPRB/LDAPRH: size 111000 1 0 1 11111 1 100 00 Rn Rt (size free)
    ("ldapr", 0x3FFFFC00, 0x38BFC000),
    # LDAPUR*/STLUR* (RCpc imm9): size 011001 opc 0 imm9 00 Rn Rt (size, opc, imm9, Rn, Rt free)
    ("ldapur", 0x3F200C00, 0x19000000),
    # LDAXR/STLXR/LDAXP/STLXP: size 001000 o2=0 L o1 Rs o0=1 Rt2 Rn Rt (size, L, o1, Rs, Rt2 free)
    ("ldaxr_stlxr", 0x3F808000, 0x08008000),
    # DMB: 1101 0101 0000 0011 0011 CRm 1 01 11111 (CRm free)
    ("dmb", 0xFFFFF0FF, 0xD50330BF),
    # DSB: ... 0011 CRm 1 00 11111
    ("dsb", 0xFFFFF0FF, 0xD503309F),
    # ISB: ... 0011 CRm 1 10 11111
    ("isb", 0xFFFFF0FF, 0xD50330DF),
)
N_FIXED = 7   # counter slots: total, ldar_stlr, ldapr, ldaxr_stlxr, dmb, dsb, isb, then ranges, then entries
SLOT = {"total": 0, "ldar_stlr": 1, "ldapr": 2, "ldaxr_stlxr": 3, "dmb": 4, "dsb": 5, "isb": 6}


def classify(word):
    """names of the opcode classes an instruction word belongs to, in OUT.json order"""
    hit = set()
    for name, mask, val in OPCODES:
        if word & mask == val:
            hit.add({"ldar_plain": "ldar_stlr", "ldapur": "ldapr"}.get(name, name))
            if name in ("ldapr", "ldapur", "ldaxr_stlxr"):
                hit.add("ldar_stlr")
    return tuple(n for n in OPCODE_NAMES if n in hit)


def attach(uc, lo, hi, spec=None, bias=0):
    """count every block executed in [lo, hi); spec: SPEC.json dict or path (link-time addresses), bias: load bias"""
    ic = Icount(spec, bias)
    from unicorn import UC_HOOK_BLOCK
    ic.uc = uc
    uc.hook_add(UC_HOOK_BLOCK, ic._block, begin=lo, end=hi - 1)
    return ic


class Icount:
    def __init__(self, spec=None, bias=0):
        if isinstance(spec, str):
            with open(spec) as f:
                spec = json.load(f)
        spec = spec or {}
        self.rnames = sorted(spec.get("ranges", {}))
        self.enames = sorted(spec.get("entries", {}))
        self.ranges = [[(lo + bias, hi + bias) for lo, hi in spec["ranges"][n]] for n in self.rnames]
        self.entry_at = {}
        for j, n in enumerate(self.enames):
            for a in spec["entries"][n]:
                self.entry_at.setdefault(a + bias, []).append(N_FIXED + len(self.rnames) + j)
        self.nslots = N_FIXED + len(self.rnames) + len(self.enames)
        self.cache = {}
        self.names = {}                   # tid -> name
        self.intervals = []               # [from, to, out_of_order, {tid: counters}]
        self.tid = 1
        self.names[1] = "main"
        self._open("start", False)
        self.stopped = None
        self.events = {}
        self.slices = 0
        self.uc = None

    # -- state changes (called by the emulator)
    def _open(self, frm, ooo):
        self.intervals.append([frm, None, ooo, {}])
        self._resolve()

    def _resolve(self):
        per = self.intervals[-1][3]
        c = per.get(self.tid)
        if c is None:
            c = per[self.tid] = [0] * self.nslots
        self.cur = c

    def thread(self, tid, name=None):
        """the running thread is tid from now on"""
        self.slices += 1
        self.tid = tid
        self.names[tid] = name or self.names.get(tid) or ("main" if tid == 1 else f"t{tid}")
        self._resolve()

    def mark(self, name, out_of_order=False):
        """a milestone (or frame) was hit: close the open interval at `name`, open the next one"""
        if self.stopped is not None:
            return
        cur = self.intervals[-1]
        cur[1] = name
        cur[2] = cur[2] or bool(out_of_order)
        self._open(name, bool(out_of_order))

    def stop(self, reason, **events):
        """the run ended (idempotent: the first call counts); events: counts of the emulator's partial-block events"""
        if self.stopped is None:
            self.stopped = reason
            self.events = dict(events)
            self.intervals[-1][1] = "stop"

    # -- the hook
    def _block(self, uc, addr, size, _):
        f = self.cache.get((addr, size))
        if f is None:
            f = self.cache[(addr, size)] = self._facts(addr, size)
        c = self.cur
        for i, n in f:
            c[i] += n

    def _facts(self, addr, size):
        n = size // 4
        words = struct.unpack(f"<{n}I", bytes(self.uc.mem_read(addr, n * 4)))
        add = {0: n}
        for w in words:
            for cl in classify(w):
                add[SLOT[cl]] = add.get(SLOT[cl], 0) + 1
        for r, rs in enumerate(self.ranges):
            k = sum(1 for i in range(n) if any(lo <= addr + 4 * i < hi for lo, hi in rs))
            if k:
                add[N_FIXED + r] = k
        for i in self.entry_at.get(addr, ()):
            add[i] = add.get(i, 0) + 1
        return tuple(sorted(add.items()))

    # -- output
    def _view(self, c):
        d = {k: c[i] for k, i in SLOT.items()}
        d["ranges"] = {n: c[N_FIXED + j] for j, n in enumerate(self.rnames)}
        d["entries"] = {n: c[N_FIXED + len(self.rnames) + j] for j, n in enumerate(self.enames)}
        return d

    def report(self):
        tot = {}
        ivs = []
        for frm, to, ooo, per in self.intervals:
            ivs.append({"from": frm, "to": to if to is not None else "stop", "out_of_order": ooo,
                        "per_thread": {str(t): self._view(per[t]) for t in sorted(per)}})
            for t, c in per.items():
                a = tot.setdefault(t, [0] * self.nslots)
                for i, v in enumerate(c):
                    a[i] += v
        allc = [sum(c[i] for c in tot.values()) for i in range(self.nslots)]
        totals = {str(t): self._view(tot[t]) for t in sorted(tot)}
        totals["all"] = self._view(allc)
        return {
            "format": FORMAT,
            "threads": {str(t): self.names[t] for t in sorted(self.names)},
            "intervals": ivs, "totals": totals, "blocks_cached": len(self.cache),
            "stop": self.stopped,
            "exactness": {
                "counting": "UC_HOOK_BLOCK at block entry: size/4 instructions per block entered, exact for blocks that "
                            "run to their end",
                "partial_blocks": "a block left before its end (fault, emu_stop, count/timer-limited slice) is counted "
                                  "in full; a resume at a mid-block pc counts the rest again",
                "thread_slices": self.slices,
                "events": self.events,
                "code_outside_hooked_range": "not counted",
            },
        }

    def summary(self):
        """the totals as a small dict (the boot summary's "icount")"""
        t = self.report()["totals"]["all"]
        return {k: t[k] for k in SLOT} | {"ranges": t["ranges"], "entries": t["entries"]}

    def write(self, path):
        with open(path, "w") as f:
            json.dump(self.report(), f, indent=1, sort_keys=True)
            f.write("\n")


# -- spec subcommand
def _run(*cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def build_spec(elf, ranges, entries):
    """ranges: ["NAME=section:S" | "NAME=symrange:A,B"], entries: ["NAME=symfile:FILE"] -> SPEC dict; ValueError on a miss"""
    secs, syms = {}, {}
    for line in _run("aarch64-linux-gnu-readelf", "-S", "-W", elf).splitlines():
        m = re.match(r"\s*\[\s*\d+\]\s+(\S+)\s+\S+\s+([0-9a-f]+)\s+[0-9a-f]+\s+([0-9a-f]+)\s", line)
        if m:
            secs.setdefault(m[1], []).append((int(m[2], 16), int(m[3], 16)))
    for line in _run("aarch64-linux-gnu-nm", "--defined-only", elf).splitlines():
        p = line.split()
        if len(p) == 3:
            syms.setdefault(p[2], []).append(int(p[0], 16))

    def sym(n):
        if n not in syms:
            raise ValueError(f"symbol {n!r} not in {elf}")
        if len(set(syms[n])) != 1:
            raise ValueError(f"symbol {n!r} is ambiguous in {elf}: {sorted(set(syms[n]))}")
        return syms[n][0]
    spec = {"ranges": {}, "entries": {}}
    for item in ranges:
        name, _, how = item.partition("=")
        kind, _, arg = how.partition(":")
        if kind == "section":
            if arg not in secs:
                raise ValueError(f"section {arg!r} not in {elf}")
            spec["ranges"].setdefault(name, []).extend([a, a + n] for a, n in secs[arg])
        elif kind == "symrange":
            a, _, b = arg.partition(",")
            lo, hi = sym(a), sym(b)
            if hi <= lo:
                raise ValueError(f"symrange {arg}: end {hi:#x} is not above start {lo:#x}")
            spec["ranges"].setdefault(name, []).append([lo, hi])
        else:
            raise ValueError(f"--range {item!r}: want NAME=section:S or NAME=symrange:A,B")
    for item in entries:
        name, _, how = item.partition("=")
        kind, _, arg = how.partition(":")
        if kind != "symfile":
            raise ValueError(f"--entries {item!r}: want NAME=symfile:FILE")
        with open(arg) as f:
            names = [l.rstrip("\n") for l in f if l.strip()]
        spec["entries"].setdefault(name, []).extend(sym(n) for n in names)
    for k in spec["entries"]:
        spec["entries"][k] = sorted(set(spec["entries"][k]))
    return spec


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="icount tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("spec", help="build SPEC.json (range and entry classes, link-time addresses) from an ELF")
    sp.add_argument("--elf", required=True)
    sp.add_argument("--range", action="append", default=[], metavar="NAME=section:S|symrange:A,B")
    sp.add_argument("--entries", action="append", default=[], metavar="NAME=symfile:FILE")
    sp.add_argument("-o", required=True)
    a = ap.parse_args(argv)
    try:
        spec = build_spec(a.elf, a.range, a.entries)
    except (ValueError, OSError, subprocess.CalledProcessError) as e:
        print(f"icount spec: {e}", file=sys.stderr)
        return 1
    with open(a.o, "w") as f:
        json.dump(spec, f, indent=1, sort_keys=True)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
