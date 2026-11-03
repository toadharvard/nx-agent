#!/usr/bin/env python3
"""Classify the symbols still undefined in the full guest link (toolchain/game/build.sh -> undefined.txt).

    python3 -I toolchain/game/undef_report.py <out>/undefined.txt [<S2 out>/addrmap.tsv] > undefined_report.txt
"""
import collections
import re
import subprocess
import sys

names = [l.strip() for l in open(sys.argv[1]) if l.strip()]
dem = subprocess.run(["c++filt"], input="\n".join(names), capture_output=True, text=True).stdout.splitlines()
kinds = {}
if len(sys.argv) > 2:
    for l in open(sys.argv[2]):
        if l[:1] in "0123456789abcdef" and "\t" in l:
            f = l.split("\t")
            kinds[int(f[0], 16)] = f[1]

D3DX = re.compile(r"^D3DX")
WIN32 = {"CreateFileA", "CloseHandle", "GetLastError", "Sleep", "WSAStartup", "LeaveCriticalSection", "EnterCriticalSection"}


def classify(n, d):
    if n.startswith("__fnv_"):
        m = re.match(r"__fnv_([0-9a-f]{8})", n)
        a = int(m.group(1), 16) if m else 0
        if a >= 0x00fdf000:
            return "image data address without alias (S2 aliases.ld)"
        k = kinds.get(a)
        if k == "E":
            return "image .text address, no native definition: CRT/thunk/helper (S4)"
        return "image .text address, no native definition: function not compiled or unmatched (S1/S2)"
    if n.startswith("__fnvnx_br_"):
        return "ABI bridge not generated (S2)"
    if D3DX.match(n):
        return "D3DX9 (S6)"
    if n.startswith(("Bink", "_Bink")):
        return "Bink (S6)"
    if "Steam" in n:
        return "Steam API (S4)"
    if re.match(r"^(DAT|PTR|UNK|g)_[0-9a-fA-F]+", n):
        return "Ghidra data placeholder name (S2 by-address)"
    if re.match(r"^_Z.*FUN_[0-9a-f]{8}", n) or n.startswith("FUN_"):
        return "Ghidra function placeholder (FUN_xxxxxxxx) (S2 by-address)"
    if d.startswith(("typeinfo for", "vtable for", "typeinfo name for", "VTT for", "construction vtable")):
        return "C++ vtable/typeinfo: key function not compiled (S1)"
    if n.startswith("_Z"):
        if re.search(r"(std::|__cxx|__gnu)", d):
            return "C++ standard library (toolchain)"
        return "C++ function/variable without definition (S1/S2)"
    if n in WIN32 or re.match(r"^(Get|Set|Create|Close|Open|Read|Write|Reg|Wait|Find|Load|Free|Virtual|Heap|Local|Global|Query|Interlocked|Sleep|WSA|Message|Peek|Dispatch|Show|Post|Send|Translate|Def|Register|Unregister|Update|Destroy)[A-Z]", n):
        return "Win32 API by plain name (S4)"
    if re.match(r"^_?(mem|str|wcs|_strn?icmp|sprintf|printf|f[a-z]+|ato|abs|qsort|rand|srand|time|clock|_ftol|_CI|_alloca|_chk|__security)", n):
        return "C runtime / MSVC helper (S4)"
    if n.startswith("_mm_") or n.startswith("__builtin"):
        return "SSE intrinsic / builtin (S8)"
    return "other C symbol"


groups = collections.defaultdict(list)
for n, d in zip(names, dem):
    groups[classify(n, d)].append(d)
print(f"undefined symbols: {len(names)}\n")
for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1])):
    print(f"{len(v):6}  {k}")
for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1])):
    print(f"\n== {k} ({len(v)}), first 25:")
    for d in v[:25]:
        print("   ", d[:160])
