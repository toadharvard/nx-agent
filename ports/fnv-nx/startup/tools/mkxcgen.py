#!/usr/bin/env python3
"""mkxcgen.py - natives for the exe's dynamic initializers that the decomp lacks (stream S10, docs/STARTUP.md; D21
class E: about 600 __xc slots of FalloutNV.exe 1.4.0.525 have no native).

    startup/tools/mkxcgen.py --exe FalloutNV.exe --addrmap out/addrmap.tsv --nm fnvnx.nm --out xcgen.c [--tsv xcgen.tsv]

An initializer the decomp does not have is usually straight-line code: it constructs a static object, registers its
destructor with atexit, or copies a constant into a global. This tool translates that shape from the exe's bytes
into C, one weak `void __fnvnx_dyninit_<addr>(void)` each, calling the natives the address map gives for the
callees. mkxctab.py then finds them like any other dyninit. An initializer the translator does not understand
completely is left out (listed in the TSV with the reason). Nothing is guessed.

Understood instructions:
  push ebp / mov ebp, esp / pop ebp      frame (ignored)
  push imm32 | push imm8                 an argument
  mov ecx, imm32                         this for the next thiscall
  call rel32                             a call to a native (address map F/C row)
  jmp rel32                              a tail call
  add esp, imm8 / pop ecx                caller cleanup (cdecl)
  mov eax, [imm32] / mov [imm32], eax    a 32-bit global copy
  fld dword [imm32] / fstp dword [imm32] a float global copy
  ret                                    the end

A callee's argument count and which arguments are floats come from its mangled native name (Itanium: f = float).
A member function (_ZN...) takes this from ecx as its first argument. Arguments come in AAPCS64 order: x0 = this,
then the pushed values from the last pushed (first parameter) on. atexit (the exe's 0x00ec658f) registers a native
destructor through the guest's atexit when the pushed function has a native, and is dropped when it has none: the
missing initializer already ran nothing at exit.

The output is derived from the exe and the decomp's address map: local, never committed (docs/ARCHITECTURE.md,
Legal).
"""
from __future__ import annotations

import argparse
import re
import struct
import subprocess
import sys

ATEXIT = 0x00EC658F
XC_LO, XC_HI = 0x00FDF39C, 0x01011414   # the exe's __xc_a .. __xc_z


class PE:
    def __init__(self, data):
        self.d = data
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        n = struct.unpack_from("<H", data, pe + 6)[0]
        opt = struct.unpack_from("<H", data, pe + 20)[0]
        self.base = struct.unpack_from("<I", data, pe + 24 + 28)[0]
        self.secs = [struct.unpack_from("<8sIIII", data, pe + 24 + opt + 40 * i)[:5] for i in range(n)]

    def read(self, va, n):
        r = va - self.base
        for _, vs, sva, rs, ro in self.secs:
            if sva <= r < sva + rs:
                return self.d[ro + r - sva:ro + r - sva + n]
        return b""


def params(mangled):
    """(member, [is_float per parameter]) from an Itanium name, or None when it cannot be read"""
    out = subprocess.run(["c++filt", mangled], capture_output=True, text=True).stdout.strip()
    if "(" not in out:
        return None
    member = mangled.startswith("_ZN")
    args = out[out.index("(") + 1:out.rindex(")")]
    if args.strip() in ("", "void"):
        return member, []
    depth, cur, parts = 0, "", []
    for ch in args:
        depth += ch in "<(["
        depth -= ch in ">)]"
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    parts.append(cur.strip())
    if any(p in ("...", "double", "long long", "unsigned long long") or "&&" in p for p in parts):
        return None   # varargs or a 64-bit argument: two stack words, not handled here
    return member, [p == "float" for p in parts]


def translate(pe, addr, natives, sig):
    """C statements for the initializer at addr, or (None, reason)"""
    b = pe.read(addr, 1024)
    i, stack, ecx, code, decls, fpu = 0, [], None, [], {}, None   # stack: (C expression, is_float, value or None)

    def call(target, tail, next_bytes):
        nonlocal ecx, stack
        caller_cleans = next_bytes[:2] == b"\x83\xc4" or next_bytes[:1] == b"\x59"   # cdecl: add esp / pop ecx
        if target == ATEXIT:
            fn = stack[-1][2] if stack else None
            if fn is None:
                return "atexit without an argument"
            if fn in natives:
                decls[natives[fn]] = "void"
                code.append(f"atexit(fnvnx_x_{natives[fn]});")
            if not caller_cleans:
                stack.pop()
            return None
        if target not in natives:
            return f"callee {target:#010x} has no native"
        name = natives[target]
        p = sig(name)
        if p is None:
            return f"callee {name}: signature not usable"
        member, floats = p
        n = len(floats)
        if len(stack) < n:
            return f"callee {name}: {n} parameters, {len(stack)} pushed"
        args = [stack[-1 - k] for k in range(n)]   # first parameter = last pushed
        for (expr, isf, _), fl in zip(args, floats):
            if isf and not fl:
                return f"callee {name}: a float where it takes an integer"
        if not caller_cleans:                      # thiscall / stdcall: the callee pops its arguments
            del stack[len(stack) - n:]
        if member:
            if ecx is None:
                return f"callee {name}: thiscall without mov ecx"
            args = [(f"0x{ecx:08x}u", False, ecx)] + args
            floats = [False] + floats
        decls[name] = floats
        lits = [(e if isf or not fl else f"fnvnx_f32({e})") for (e, isf, _), fl in zip(args, floats)]
        code.append(f"fnvnx_x_{name}({', '.join(lits)});")
        ecx = None
        if tail:
            code.append("return;")
        return None

    while i < len(b):
        op = b[i]
        if op == 0x55:                                   # push ebp
            i += 1
        elif b[i:i + 2] == b"\x8b\xec":                  # mov ebp, esp
            i += 2
        elif op == 0x5D:                                 # pop ebp
            i += 1
        elif op == 0x68:
            v = struct.unpack_from("<I", b, i + 1)[0]
            stack.append((f"0x{v:08x}u", False, v)); i += 5
        elif op == 0x6A:
            v = struct.unpack_from("<b", b, i + 1)[0] & 0xFFFFFFFF
            stack.append((f"0x{v:08x}u", False, v)); i += 2
        elif op == 0x51:                                 # push ecx: a slot a following fstp [esp] fills
            stack.append(("0u", False, None)); i += 1
        elif b[i:i + 3] == b"\xd9\x1c\x24":              # fstp dword [esp]: a float argument
            if fpu is None or not stack:
                return None, "fstp [esp] without a value"
            stack[-1] = (fpu, True, None); fpu = None; i += 3
        elif b[i:i + 2] == b"\xd9\x05":                  # fld dword [a]
            fpu = f"*(volatile float *)0x{struct.unpack_from('<I', b, i + 2)[0]:08x}u"; i += 6
        elif b[i:i + 2] == b"\xd9\xee":                  # fldz
            fpu = "0.0f"; i += 2
        elif b[i:i + 2] == b"\xd9\xe8":                  # fld1
            fpu = "1.0f"; i += 2
        elif b[i:i + 2] == b"\xd9\x1d":                  # fstp dword [b]
            if fpu is None:
                return None, "fstp without a value"
            code.append(f"*(volatile float *)0x{struct.unpack_from('<I', b, i + 2)[0]:08x}u = {fpu};")
            fpu = None; i += 6
        elif b[i:i + 2] == b"\xc7\x05":                  # mov dword [a], imm32
            dst, v = struct.unpack_from("<II", b, i + 2)
            code.append(f"*(volatile uint32_t *)0x{dst:08x}u = 0x{v:08x}u;"); i += 10
        elif op == 0xB9:
            ecx = struct.unpack_from("<I", b, i + 1)[0]; i += 5
        elif op in (0xE8, 0xE9):
            t = (addr + i + 5 + struct.unpack_from("<i", b, i + 1)[0]) & 0xFFFFFFFF
            why = call(t, op == 0xE9, b[i + 5:i + 8])
            if why:
                return None, why
            i += 5
            if op == 0xE9:
                return code, decls
        elif b[i:i + 2] == b"\x83\xc4":                  # add esp, imm8 (cdecl cleanup)
            k = b[i + 2] // 4
            stack = stack[:len(stack) - k] if k <= len(stack) else []
            i += 3
        elif op == 0x59:                                 # pop ecx (cleanup of one word)
            stack = stack[:-1]; i += 1
        elif op == 0xA1 and b[i + 5] == 0xA3:            # mov eax, [a]; mov [b], eax
            src, dst = struct.unpack_from("<II", b, i + 1)[0], struct.unpack_from("<I", b, i + 6)[0]
            code.append(f"*(volatile uint32_t *)0x{dst:08x}u = *(volatile uint32_t *)0x{src:08x}u;")
            i += 10
        elif op == 0xC3:
            return code, decls
        else:
            return None, f"instruction {b[i:i + 4].hex()} at +{i:#x}"
    return None, "no ret"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--exe", required=True)
    ap.add_argument("--addrmap", required=True)
    ap.add_argument("--nm", required=True, help="nm output of the objects (or guest) whose dyninits exist")
    ap.add_argument("--out", required=True)
    ap.add_argument("--tsv")
    a = ap.parse_args(argv)
    pe = PE(open(a.exe, "rb").read())
    natives = {}
    for line in open(a.addrmap):
        f = line.rstrip("\n").split("\t")
        if len(f) > 3 and f[1] in "FC" and f[3]:
            natives.setdefault(int(f[0], 16), f[3])
    present = set()
    for line in open(a.nm):
        m = re.search(r"__fnvnx_dyninit_([0-9a-f]{8})$", line.strip())
        if m:
            present.add(int(m.group(1), 16))
    raw = pe.read(XC_LO, XC_HI - XC_LO)
    slots = [s for s in struct.unpack(f"<{len(raw) // 4}I", raw) if s]
    missing = sorted({s for s in slots if s not in present and s not in natives})
    cache = {}

    def sig(name):
        if name not in cache:
            cache[name] = params(name)
        return cache[name]
    rows, funcs, decls = [], [], {}
    for addr in missing:
        try:
            code, info = translate(pe, addr, natives, sig)
        except (struct.error, IndexError):
            code, info = None, "ran past the bytes read (no ret)"
        if code is None:
            rows.append((addr, "left", info))
            continue
        decls.update(info)
        funcs.append(f"__attribute__((weak)) void __fnvnx_dyninit_{addr:08x}(void) {{\n    "
                     + "\n    ".join(code) + "\n}")
        rows.append((addr, "generated", f"{len(code)} statements"))
    out = ["/* generated by startup/tools/mkxcgen.py from the exe and the address map: local output, never committed */",
           "#include <stdint.h>", "#include <stdlib.h>",
           "static inline float fnvnx_f32(uint32_t b) { union { uint32_t u; float f; } v = { b }; return v.f; }"]
    for name, floats in sorted(decls.items()):
        if floats == "void":
            out.append(f'extern void fnvnx_x_{name}(void) __asm__("{name}");')
        else:
            ps = ", ".join("float" if fl else "uint32_t" for fl in floats) or "void"
            out.append(f'extern void fnvnx_x_{name}({ps}) __asm__("{name}");')
    out += funcs
    open(a.out, "w").write("\n".join(out) + "\n")
    if a.tsv:
        with open(a.tsv, "w") as t:
            t.write("orig\tresult\tinfo\n")
            for addr, r, info in rows:
                t.write(f"{addr:#010x}\t{r}\t{info}\n")
    gen = sum(1 for r in rows if r[1] == "generated")
    print(f"mkxcgen: {len(missing)} initializers without a native, {gen} generated, {len(missing) - gen} left "
          f"(see {a.tsv or 'the TSV'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
