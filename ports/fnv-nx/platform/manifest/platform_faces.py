#!/usr/bin/env python3 -I
"""Static census of the font requests of the platform's own compiled code (L5 declared machine; part of the declared font set).

Rule (mechanical, from the compiled objects only; no source text, no names of callers):
  A face request of platform code is exactly one of
    (a) a call to the face accessor `w32_face_name(id)` (platform/win32/font_faces.h; the only place a face name is spelled), the id is the
        immediate loaded into %edi right before the call;
    (b) a call to GetStockObject(id) with an immediate id 10..17 (a stock FONT; the stock table gdifont.cpp stock_lf names its faces through (a));
  and every call of either with a non-immediate argument is `unresolved` and ends the census with exit 1.
  Every compiled platform object is scanned (the product unit set of the recipe), so a site in unreachable code is counted too: the declared
  set is a superset by rule, and a site is taken out only by deleting it.
  Font-API sites (CreateFont*/AddFontResource*/EnumFont*) are listed per object with the number of face requests in the same function; a
  function with a font-API site and no accessor request is `unresolved` (its face does not come from the table).
Output: TSV rows kind, object, function, id, face. Faces are looked up in the X-macro table of font_faces.h.
Assumptions: ASM-001 (the compiler is correct): a call is a call and its first integer argument is in %edi (x86-64 SysV, the host build of the objects).
Usage: platform_faces.py <font_faces.h> <out.tsv> <obj>...
"""
import re, subprocess, sys

API = re.compile(r"^(CreateFont|AddFontResource|AddFontMemResource|EnumFont)")


def table(h):
    return re.findall(r'^\s*FACE\(\s*(\w+)\s*,\s*"([^"]*)"', open(h).read(), re.M)


def scan(obj):
    out = subprocess.run(["objdump", "-dr", "--no-show-raw-insn", "-C", obj], capture_output=True, text=True, check=True).stdout
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
            if ins[0] != "call":
                last = ins
            r = lines[i + 1] if i + 1 < len(lines) else ""
            if ins[0] == "call" and "R_X86_64_" in r:
                sym = r.split()[-1].rsplit("-0x", 1)[0].rsplit("+0x", 1)[0]
                if sym in ("w32_face_name", "GetStockObject"):
                    a = re.match(r"\$(0x[0-9a-f]+|\d+),%edi", last[1]) if last and last[0] in ("mov", "movl") else None
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
            last = None if ins[0] == "call" else last
    return rows, funcs, bad


def main(h, out, objs):
    tab = table(h)
    rows, bad = [], []
    for o in objs:
        r, funcs, b = scan(o)
        rows += r; bad += b
        for fn, f in funcs.items():
            if f["api"] and not f["face"] and not any(x[2] == fn and x[0] == "stock" for x in r):
                bad.append((o, fn, "font API " + ",".join(sorted(set(f["api"]))) + " without a face request"))
    with open(out, "w") as f:
        for k, o, fn, v in rows:
            face = tab[v][1] if k == "face" and v < len(tab) else ""
            f.write("%s\t%s\t%s\t%d\t%s\n" % (k, o.split("/")[-1], fn, v, face))
    faces = sorted({tab[v][1] for k, o, fn, v in rows if k == "face" and v < len(tab)})
    print("objects %d; face requests %d; distinct faces %d; stock font sites %d; unresolved %d" % (
        len(objs), sum(1 for r in rows if r[0] == "face"), len(faces), sum(1 for r in rows if r[0] == "stock"), len(bad)), file=sys.stderr)
    for b in bad:
        print("unresolved:", *b, file=sys.stderr)
    for fc in faces:
        print(fc)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3:]) if len(sys.argv) > 3 else print(__doc__))
