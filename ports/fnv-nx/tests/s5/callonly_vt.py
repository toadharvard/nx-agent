#!/usr/bin/env python3
"""callonly_vt.py - the call-only placeholder vtable declarations of a source tree (S5-boot, D22 counterpart).

    callonly_vt.py <src dir> [--list FILE] [--json FILE]

A *call-only dispatch declaration* is a struct/class that declares virtual functions, defines none of its virtuals
(no in-class body, no out-of-class `Name::virtual(` definition anywhere in the tree; other members may be defined), has no base class and no virtual
destructor, and whose first virtual is a placeholder (v0, vf0, Unk_00, FNV_...Unknown0, ...). Code uses such a struct
only to dispatch on an object whose real class it does not name, by the exe's slot numbers: slot k = exe slot k.

Under D22 every class that does define its vtable has GCC's D1/D0 pair where MSVC's deleting destructor is, and its
later slots shift by one (native slot k+1 = exe slot k). A call-only declaration that still has the placeholder at
slot 0 is therefore one slot short for every k >= 1: HeapStandIn::Allocate (slot 2) reaches MemoryHeap's GetName,
FNV_Heap's release (slot 3) reaches Allocate, FUN_00aa45a0_vt::v4 (ContainsBlock) reaches Deallocate (the S10 heap
fault, J-0155). The fix per D22 is the same for these: `virtual ~X()` in place of the slot-0 placeholder.

Prints the counts and, with --list, one line per declaration: file, line, name, number of virtuals, first virtual."""
import argparse
import json
import os
import re
import sys

PLACEHOLDER = re.compile(r"^(?:v|vf|vfn|vt|vfunc|virt|virtual|slot|func|fn|unk|unknown|Unk_?|Unknown|Func|Virtual|Reserved|reserved|pad|Pad|"
                         r"Slot|V|F)_?0+$|^\w*?Unknown_?0*$|^\w*_v0$|^v0_\w*$|^vfunc0\w*$|^Unk(?:nown)?0+\w*$",
                         re.IGNORECASE)
HEAD = re.compile(r"\b(struct|class)\s+(?:__declspec\([^)]*\)\s*)?([A-Za-z_]\w*)\s*(final\s*)?(:[^{;]*)?\{")
VIRT = re.compile(r"\bvirtual\b([^;{]*?)\b(~?[A-Za-z_]\w*)\s*\(([^;{]*?)\)\s*(?:const\s*)?(?:override\s*)?"
                  r"(=\s*0\s*)?(;|\{)")


def block_end(s, i):
    depth = 0
    for j in range(i, len(s)):
        c = s[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(s)


def strip_comments(s):
    s = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), s, flags=re.S)
    return re.sub(r"//[^\n]*", "", s)


def scan(src):
    files = []
    for dp, _, fs in os.walk(src):
        for f in fs:
            if f.endswith((".cpp", ".h", ".hpp", ".inl", ".c")):
                files.append(os.path.join(dp, f))
    texts = {}
    for p in sorted(files):
        try:
            texts[p] = strip_comments(open(p, encoding="utf-8", errors="replace").read())
        except OSError:
            continue
    defined = set()   # (class, member) with an out-of-class definition somewhere: Name::fn( ... ) {
    for t in texts.values():
        for m in re.finditer(r"\b([A-Za-z_]\w*)::(~?[A-Za-z_]\w*)\s*\([^;{)]*\)\s*(?:const\s*)?\{", t):
            defined.add((m.group(1), m.group(2)))
    decls = []
    for p, t in texts.items():
        for m in HEAD.finditer(t):
            kind, name, _final, bases = m.groups()
            i = m.end() - 1
            j = block_end(t, i)
            body = t[i + 1:j]
            # only the top level of the body (not nested structs)
            top, depth = [], 0
            for c in body:
                if c == "{":
                    depth += 1
                    top.append(c)
                elif c == "}":
                    depth -= 1
                    top.append(c)
                else:
                    top.append(c if depth == 0 else " ")
            top = "".join(top)
            virts = [(v.group(2), bool(v.group(4)), v.group(5) == "{") for v in VIRT.finditer(top)]
            if not virts:
                continue
            line = t.count("\n", 0, m.start()) + 1
            decls.append({"file": os.path.relpath(p, src), "line": line, "name": name, "bases": (bases or "").strip(),
                          "virtuals": [v[0] for v in virts], "inline_body": any(v[2] for v in virts),
                          "pure": all(v[1] for v in virts), "dtor": any(v[0].startswith("~") for v in virts),
                          "defined": any((name, v[0]) in defined for v in virts)})
    return decls


def classify(d):
    if d["dtor"]:
        return "has-dtor"
    if d["bases"]:
        return "derived"
    if d["defined"] or d["inline_body"]:
        return "defines-vtable"
    if not PLACEHOLDER.match(d["virtuals"][0]):
        return "named-slot0"
    return "callonly-placeholder"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--list")
    ap.add_argument("--json")
    a = ap.parse_args()
    decls = scan(a.src)
    counts = {}
    for d in decls:
        d["class"] = classify(d)
        counts[d["class"]] = counts.get(d["class"], 0) + 1
    hits = [d for d in decls if d["class"] == "callonly-placeholder"]
    multi = [d for d in hits if len(d["virtuals"]) > 1]
    print(f"declarations with virtuals: {len(decls)}")
    for k in sorted(counts, key=lambda k: -counts[k]):
        print(f"  {counts[k]:6d}  {k}")
    print(f"call-only placeholder declarations dispatching past slot 0 (one slot short under D22): {len(multi)} "
          f"in {len({d['file'] for d in multi})} files")
    if a.list:
        with open(a.list, "w") as f:
            for d in sorted(hits, key=lambda d: (d["file"], d["line"])):
                f.write(f"{d['file']}\t{d['line']}\t{d['name']}\t{len(d['virtuals'])}\t{d['virtuals'][0]}\t"
                        f"{d['virtuals'][-1]}\n")
    if a.json:
        json.dump(decls, open(a.json, "w"), indent=0)


if __name__ == "__main__":
    sys.exit(main())
