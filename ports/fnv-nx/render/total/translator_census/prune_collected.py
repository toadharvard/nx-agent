#!/usr/bin/env python3
"""Deletes what reach.tsv says is collected from a patched mojoshader tree (input of the recipe's deletion patch; see PATCHES.md).
usage: prune_collected.py REACH_TSV TREE OBJ_DIR [KEEP_FILE]
KEEP_FILE lists function names to keep: collected functions that remaining code still calls (the out-of-line copy is unreferenced because
the compiler inlined every call; the loop in PATCHES.md adds each undefined symbol of the pruned objects, a build fact, until none is left).
Rule: a source file whose object has no non-empty section reached (all collected, or no code at all) is deleted; in every other file each
collected `.text.NAME` section is the function NAME (its signature through the closing `}` in column 0) and each collected
`.rodata.NAME.*` section goes with it. The result is verified by the recipe build and a census with 0 collected sections, not by this tool."""
import collections, os, re, sys
tsv, tree, objdir = sys.argv[1:4]
keep = set(open(sys.argv[4]).read().split()) if len(sys.argv) > 4 else set()
rows = [l.rstrip("\n").split("\t") for l in open(tsv)]
by = collections.defaultdict(lambda: collections.Counter())
fn = collections.defaultdict(list)
for o, s, st in rows:
    by[o][st] += 1
    if st == "collected" and s.startswith(".text."): fn[o].append(s[6:])
allobj = []
for d, _, fs in os.walk(objdir):
    for f in fs:
        if f.endswith(".o"): allobj.append(os.path.relpath(os.path.join(d, f), objdir))
def src(o):  # "mojoshader:profiles/x.c.o" -> tree path
    return o.split(":", 1)[1][:-2] if o.startswith("mojoshader:") else None
for o in sorted(allobj):
    p = src(o)
    if not p: continue
    c = by.get(o, collections.Counter())
    if c["reached"] == 0:
        os.remove(os.path.join(tree, p)); print("delete file", p); continue
    lines = open(os.path.join(tree, p)).read().split("\n")
    for name in [n for n in fn[o] if n not in keep]:
        pat = re.compile(r"(^|[\s\*])%s\s*\(" % re.escape(name))
        i = next((k for k, l in enumerate(lines) if l and not l[0].isspace() and l[0] not in "#/}{" and pat.search(l) and not l.rstrip().endswith(";")), None)
        if i is None: sys.exit("no definition of %s in %s" % (name, p))
        s = i
        while s > 0 and lines[s - 1] and not lines[s - 1].startswith(("#", "}", "/", " ", "\t")) and not lines[s-1].rstrip().endswith((";", "{")): s -= 1
        depth = 0; seen = False; e = None
        for k in range(i, len(lines)):
            for ch in re.sub(r"//.*|/\*.*?\*/|\"(\\.|[^\"])*\"|'(\\.|[^'])*'", "", lines[k]):
                if ch == "{": depth += 1; seen = True
                elif ch == "}": depth -= 1
            if seen and depth == 0: e = k; break
        if e is None: sys.exit("no end of %s in %s" % (name, p))
        del lines[s:e + 1]; print("delete", name, "in", p, e - s + 1, "lines")
    open(os.path.join(tree, p), "w").write("\n".join(lines))
