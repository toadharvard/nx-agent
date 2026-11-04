#!/usr/bin/env python3
"""Count trapping initializer functions in a linked game guest: __fnvnx_dyninit_* / __fnvnx_dynexit_* (S10's XC1
rewrite) whose code holds a `brk` (__builtin_trap, e.g. a FALL1 false positive), without running anything.

    python3 -I tests/s5/dynbrk.py <out>/fnvnx.elf [nm]      (nm defaults to aarch64-none-elf-nm)
"""
import struct
import subprocess
import sys


def main(argv):
    elf, nm = argv[0], (argv[1] if len(argv) > 1 else "aarch64-none-elf-nm")
    data = open(elf, "rb").read()
    if data[:4] != b"\x7fELF" or data[4] != 1:
        raise SystemExit("expected an ELF32 guest")
    shoff, = struct.unpack_from("<I", data, 0x20)
    shentsize, shnum = struct.unpack_from("<HH", data, 0x2E)
    code = []
    for i in range(shnum):
        _, typ, flags, addr, off, size = struct.unpack_from("<IIIIII", data, shoff + i * shentsize)
        if typ == 1 and flags & 4:   # SHT_PROGBITS, SHF_EXECINSTR
            code.append((addr, off, size))

    def read(addr, n):
        for a, o, s in code:
            if a <= addr < a + s:
                return data[o + addr - a:o + addr - a + n]
        return b""

    out = subprocess.run([nm, "-S", "--defined-only", elf], capture_output=True, text=True, check=True).stdout
    stats = {"dyninit": [0, []], "dynexit": [0, []]}
    for line in out.splitlines():
        f = line.split()
        if len(f) != 4 or not f[3].startswith(("__fnvnx_dyninit_", "__fnvnx_dynexit_")):
            continue
        kind = "dyninit" if "dyninit" in f[3] else "dynexit"
        body = read(int(f[0], 16), int(f[1], 16))
        words = struct.iter_unpack("<I", body[:len(body) // 4 * 4])
        stats[kind][0] += 1
        if any(w & 0xFFE0001F == 0xD4200000 for (w,) in words):   # brk #imm
            stats[kind][1].append(f[3])
    for kind, (n, hits) in stats.items():
        print(f"{kind}: {n} functions, {len(hits)} contain brk" + (f" (first: {', '.join(hits[:5])})" if hits else ""))
    return 1 if any(h for _, h in stats.values()) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
