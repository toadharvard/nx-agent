#!/usr/bin/env python3
"""link/guest.ld and link/guest_image.ld (link/guest.json: script, image_script), read mechanically:
  - every symbol an ASSERT of guest.ld names is assigned by guest.ld itself (the synthesised-names links read guest.ld alone, with no crt0);
  - guest_image.ld holds nothing but ASSERT statements (they name symbols an input object defines, so only the image link, which has the objects, passes it);
  - the profile names exactly these two files.
Run: python3 -I <this file>."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
from fnvnx.verify import undefset  # noqa: E402

LINK = os.path.join(VERIFY, "..", "link")
fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


def asserts(text):
    """(statements, rest): the ASSERT(...) statements of TEXT (comments removed, balanced parentheses, strings kept whole) and what is left outside them."""
    t = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    out, rest, i = [], [], 0
    while i < len(t):
        m = re.compile(r"ASSERT\s*\(").search(t, i)
        if not m:
            rest.append(t[i:])
            break
        rest.append(t[i:m.start()])
        j, depth, instr = m.end(), 1, False
        while depth:
            c = t[j]
            instr = (not instr) if c == '"' else instr
            depth += 0 if instr else (c == "(") - (c == ")")
            j += 1
        out.append(t[m.start():j])
        i = j
    return out, "".join(rest)


def names(stmt):
    body = re.sub(r"\b0[xX][0-9a-fA-F]+\b|\b\d+\b", " ", re.sub(r'"[^"]*"', "", stmt))
    return set(re.findall(r"[A-Za-z_.$][\w.$]*", body)) - {"ASSERT", "DEFINED", "ALIGN", "SIZEOF", "ADDR", "LOADADDR", "ABSOLUTE", "MAX", "MIN"}


prof = json.load(open(os.path.join(LINK, "guest.json")))
check("the profile names exactly script and image_script", set(prof) == {"script", "image_script"}, prof)
a = open(os.path.join(LINK, os.path.basename(prof["script"]))).read()
b = open(os.path.join(LINK, os.path.basename(prof["image_script"]))).read()
sa, _ = asserts(a)
defined = undefset.script_defined(a)
bad = sorted((n, s) for s in sa for n in names(s) if n not in defined)
check("every symbol an ASSERT of guest.ld names is assigned in guest.ld", not bad, bad)
sb, rest = asserts(b)
check("guest_image.ld holds ASSERT statements and nothing else", sb and not rest.strip(), rest)
check("no ASSERT of guest_image.ld is in guest.ld", not [s for s in sb if re.sub(r"\s+", "", s) in {re.sub(r"\s+", "", x) for x in sa}])
check("the image assertions name symbols that guest.ld does not assign (an input object defines them)", all(names(s) - defined for s in sb), [names(s) for s in sb])
sys.exit(1 if fails else 0)
