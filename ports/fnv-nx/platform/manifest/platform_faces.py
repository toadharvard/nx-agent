#!/usr/bin/env python3 -I
"""Static census of the font requests of the platform's own compiled code (L5 declared machine; part of the declared font set).

Rule (mechanical, from the compiled objects only; no source text, no names of callers):
  A face request of platform code is exactly one of
    (a) a call to the face accessor `w32_face_name(id)` (platform/win32/font_faces.h; the only place a face name is spelled), the id is the
        immediate loaded into %edi (x86-64) or w0 (AArch64 ILP32, the recipe's objects) right before the call;
    (b) a call to GetStockObject(id) with an immediate id 10..17 (a stock FONT; the stock table gdifont.cpp stock_lf names its faces through (a));
  and every call of either with a non-immediate argument is `unresolved` and ends the census with exit 1.
  Every compiled platform object is scanned (the product unit set of the recipe), so a site in unreachable code is counted too: the declared
  set is a superset by rule, and a site is taken out only by deleting it.
  Font-API sites (CreateFont*/AddFontResource*/EnumFont*) are listed per object with the number of face requests in the same function; a
  function with a font-API site and no accessor request is `unresolved` (its face does not come from the table).
Output: TSV rows kind, object, function, id, face. Faces are looked up in the X-macro table of font_faces.h.
Assumptions: OWNER 1 (the compiler is correct): a call is a call and its first integer argument is in %edi (x86-64 SysV, the host build of the objects).
Usage: platform_faces.py <font_faces.h> <out.tsv> <obj>...
"""
import re, subprocess, sys

API = re.compile(r"^(CreateFont|AddFontResource|AddFontMemResource|EnumFont)")


BASE = 0x46414300  # W32_FACE_BASE of font_faces.h (checked against the header in main)


def table(h):
    return re.findall(r'^\s*FACE\(\s*(\w+)\s*,\s*"([^"]*)"', open(h).read(), re.M)


NOWRITE = re.compile(r"^(st|cmp$|cmn$|tst$|ccmp$|ccmn$|b$|b\.|br$|ret$|cbz$|cbnz$|tbz$|tbnz$|nop$|dmb$|dsb$|isb$|prfm$)")
IMM = re.compile(r"^#(0x[0-9a-f]+|\d+)$")


def split_ops(o):
    return [x.strip() for x in re.split(r",\s*(?![^\[]*\])", o.split("//")[0].strip())]


def a64_ids(insns, k):
    """The set of immediates the argument register w0 holds at the call insns[k], or None.
    Rule (a superset of every id that can reach): the nearest earlier write of w0 inside the same straight-line block is `mov w0,#imm`, or
    `mov w0,wM` where every definition of wM in the function is `mov #imm`, `mov wX` or `csel wA,wB` of registers resolved the same way.
    Any other definition (a load, an arithmetic result, a call result, a parameter without a definition) makes it unresolved (None)."""
    targets = set()
    for (_, m, o, _) in insns:
        if m == "b" or m.startswith("b.") or m in ("cbz", "cbnz", "tbz", "tbnz"):
            targets.update(re.findall(r"\b([0-9a-f]+) <", o))

    def dest(m, ops, n):
        if m in ("bl", "blr"): return n <= 18
        if not NOWRITE.match(m) and ops and re.match(r"^[wx]%d$" % n, ops[0]): return True
        return m in ("ldp", "ldnp", "ldxp", "ldaxp") and len(ops) > 1 and re.match(r"^[wx]%d$" % n, ops[1]) is not None

    def val(r, seen):
        if r in ("wzr", "xzr"): return {0}
        r = "w" + r[1:]
        if r in seen: return set()
        n = int(r[1:])
        d = [(m, split_ops(o)) for (a, m, o, _) in insns if dest(m, split_ops(o), n)]
        if not d: return None
        acc = set()
        for m, ops in d:
            if m == "mov" and IMM.match(ops[1]): acc.add(int(ops[1][1:], 0))
            elif m == "mov" and re.match(r"^([wx]\d+|wzr)$", ops[1]):
                v = val(ops[1], seen | {r})
                if v is None: return None
                acc |= v
            elif m == "csel" and len(ops) >= 3:
                for q in ops[1:3]:
                    v = val(q, seen | {r})
                    if v is None: return None
                    acc |= v
            else:
                return None
        return acc

    def back(r, pos, depth=0):
        """r at instruction index pos: the nearest earlier write in the straight-line block; at a branch target (a merge) the union over every definition."""
        if r in ("wzr", "xzr"): return {0}
        r = "w" + r[1:]
        n = int(r[1:])
        for q in range(pos - 1, -1, -1):
            a, m, o, _ = insns[q]
            ops = split_ops(o)
            if dest(m, ops, n):
                if m == "mov" and IMM.match(ops[1]): return {int(ops[1][1:], 0)}
                if m == "mov" and re.match(r"^([wx]\d+|wzr)$", ops[1]): return back(ops[1], q) if depth < 8 else None
                if m == "csel" and len(ops) >= 3:
                    acc = set()
                    for x in ops[1:3]:
                        v = back(x, q) if depth < 8 else None
                        if v is None: return None
                        acc |= v
                    return acc
                return None
            if a in targets: return val(r, set())
        return None

    return back("w0", k)


def scan_a64(obj):
    out = subprocess.run(["aarch64-linux-gnu-objdump", "-dr", "--no-show-raw-insn", "-C", obj], capture_output=True, text=True, check=True).stdout
    rows, funcs, bad, groups, lines = [], {}, [], [], out.splitlines()
    for i, l in enumerate(lines):
        m = re.match(r"^[0-9a-f]+ <(.*)>:$", l)
        if m:
            groups.append((m.group(1), [])); continue
        m = re.match(r"^\s+([0-9a-f]+):\s+(\S+)\s*(.*)$", l)
        if m and "R_AARCH64" not in l and groups:
            r = lines[i + 1] if i + 1 < len(lines) else ""
            sym = r.split()[-1].rsplit("-0x", 1)[0].rsplit("+0x", 1)[0] if "R_AARCH64_P32_" in r else None
            groups[-1][1].append((m.group(1), m.group(2), m.group(3), sym))
    for fn, insns in groups:
        funcs.setdefault(fn, {"face": 0, "api": []})
        for k, (a, m, o, sym) in enumerate(insns):
            if m not in ("bl", "b") or not sym: continue
            if sym in ("w32_face_name", "GetStockObject"):
                ids = a64_ids(insns, k)
                if not ids:
                    bad.append((obj, fn, sym)); continue
                for v in sorted(ids):
                    if sym == "w32_face_name":
                        rows.append(("face", obj, fn, v)); funcs[fn]["face"] += 1
                    elif 10 <= v <= 17:
                        rows.append(("stock", obj, fn, v))
            elif API.match(sym):
                funcs[fn]["api"].append(sym)
    return rows, funcs, bad


def scan(obj):
    if open(obj, "rb").read(20)[18:20] == b"\xb7\x00":  # EM_AARCH64: the recipe's guest objects (ILP32)
        return scan_a64(obj)
    return scan_x86(obj)


def scan_x86(obj):
    out = subprocess.run(["objdump", "-dr", "--no-show-raw-insn", "-C", obj], capture_output=True, text=True, check=True).stdout
    CALL, RELOC, ARG = (("call",), "R_X86_64_", r"\$(0x[0-9a-f]+|\d+),%edi")
    rows, fn, last, funcs, bad = [], None, None, {}, []
    lines = out.splitlines()
    for i, l in enumerate(lines):
        m = re.match(r"^[0-9a-f]+ <(.*)>:$", l)
        if m:
            fn, last = m.group(1), None
            funcs.setdefault(fn, {"face": 0, "api": []})
            continue
        m = re.match(r"^\s+[0-9a-f]+:\s+(\S+)\s+(.*)$", l)
        if m and "R_X86" not in l:
            ins = (m.group(1), m.group(2))
            if ins[0] not in CALL:
                last = ins
            r = lines[i + 1] if i + 1 < len(lines) else ""
            if ins[0] in CALL and RELOC in r:
                sym = r.split()[-1].rsplit("-0x", 1)[0].rsplit("+0x", 1)[0]
                if sym in ("w32_face_name", "GetStockObject"):
                    a = re.match(ARG, last[1]) if last and last[0] in ("mov", "movl") else None
                    if not a:
                        bad.append((obj, fn, sym))
                    else:
                        v = int(a.group(1), 0)
                        if sym == "w32_face_name":
                            rows.append(("face", obj, fn, v)); funcs[fn]["face"] += 1
                        elif 10 <= v <= 17:
                            rows.append(("stock", obj, fn, v))
                elif API.match(sym):
                    funcs[fn]["api"].append(sym)
            last = None if ins[0] in CALL else last
    return rows, funcs, bad


def main(h, out, objs):
    tab = table(h)
    m = re.search(r"W32_FACE_BASE\s*=\s*(0x[0-9a-fA-F]+)", open(h).read())
    if not m or int(m.group(1), 16) != BASE:
        sys.exit("platform_faces: W32_FACE_BASE of %s differs from the census base" % h)
    rows, bad = [], []
    for o in objs:
        r, funcs, b = scan(o)
        rows += r; bad += b
        for fn, f in funcs.items():
            if f["api"] and not f["face"] and not any(x[2] == fn and x[0] == "stock" for x in r):
                bad.append((o, fn, "font API " + ",".join(sorted(set(f["api"]))) + " without a face request"))
    with open(out, "w") as f:
        for k, o, fn, v in rows:
            face = tab[v - BASE][1] if k == "face" and 0 <= v - BASE < len(tab) else ""
            f.write("%s\t%s\t%s\t%d\t%s\n" % (k, o.split("/")[-1], fn, v, face))
    bad += [(o, fn, "face id %#x outside the table" % v) for k, o, fn, v in rows if k == "face" and not 0 <= v - BASE < len(tab)]
    faces = sorted({tab[v - BASE][1] for k, o, fn, v in rows if k == "face" and 0 <= v - BASE < len(tab)})
    print("objects %d; face requests %d; distinct faces %d; stock font sites %d; unresolved %d" % (
        len(objs), sum(1 for r in rows if r[0] == "face"), len(faces), sum(1 for r in rows if r[0] == "stock"), len(bad)), file=sys.stderr)
    for b in bad:
        print("unresolved:", *b, file=sys.stderr)
    for fc in faces:
        print(fc)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3:]) if len(sys.argv) > 3 else print(__doc__))
