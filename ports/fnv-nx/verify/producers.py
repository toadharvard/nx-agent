#!/usr/bin/env python3
"""producers.py - rule (f) of gate linkstrict: every object linked into one image was built with ONE semantic flag set.
Own units are proven by their CC stage documents (gate datamodel: the whole set, in order, per language). What the recipe's link takes from an external
(a prebuilt object or archive named in the link stage's argv, `-lNAME` through the `-L` directories of that argv) has no stage document of ours; its
build is read from the object itself: the DW_AT_producer of each compile unit (`readelf --debug-dump=info --dwarf-depth=1`, which applies the
relocations), the compiler's own record of the options it was run with.
  EXPECTED, per language (c, cxx): the producer the pinned driver writes for a trivial unit of that language compiled with `-g` and
  recipe.semantics_for(driver, language) (the set minus what the driver says does not apply), its option tokens minus the per-root classes
  (recipe.PER_ROOT: -W*, -D*, -std=, -O*, -g*, -ffunction-sections, -fdata-sections). Nothing here is a flag list: the driver renders its own set.
  A CU is certified iff its option tokens, minus the same per-root classes, equal the expected set of its language; otherwise `flags-differ`
  (the extra and the missing tokens are the detail). A foreign object with no DW_AT_producer (built without -g) cannot show its flags: `no-producer`.
  A foreign assembler CU (`GNU AS`) records no options: `asm-unproven`. A foreign shared object in the link: `shared-input`.
Not covered (stated): every member of a foreign archive is read, not only the members the linker pulls in (conservative: an unused foreign member with
other flags is a finding); the compiler version and language standard in the producer's head are not compared; an option the compiler does not record
in the producer (-U_FORTIFY_SOURCE, preprocessor options) is not seen; own units are not read here (their stage documents are the proof)."""
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recipe  # noqa: E402

READELF, AR = "aarch64-linux-gnu-readelf", "aarch64-linux-gnu-ar"   # binutils-aarch64-linux-gnu of env.json
SRC = {"c": ("c", "int fnvnx_probe;\n"), "cxx": ("c++", "int fnvnx_probe;\n")}


def kind_of(producer):
    """"c", "cxx" or "asm" of a producer string's head ("GNU C17 13.3.0 ...", "GNU C++17 ...", "GNU AS 2.42"); None for another producer."""
    h = producer.split()
    if len(h) < 3 or h[0] != "GNU":
        return None
    return "asm" if h[1] == "AS" else "cxx" if h[1].startswith("C++") else "c" if re.fullmatch(r"C\d*", h[1]) else None


def options(producer):
    """The option tokens of a GNU producer minus the per-root classes (recipe.PER_ROOT) -> frozenset."""
    return frozenset(t for t in producer.split()[3:] if not recipe.PER_ROOT.fullmatch(t))


def cu_producers(path):
    """{member: [DW_AT_producer of each compile unit, ...]} of an object or archive PATH ("" for a plain object); a member with no compile unit -> []."""
    members = [""]
    r = subprocess.run([AR, "t", path], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        members = [m for m in r.stdout.splitlines() if m]
    out = {m: [] for m in members}
    r = subprocess.run([READELF, "--debug-dump=info", "--dwarf-depth=1", path], capture_output=True, text=True, env=dict(os.environ, LC_ALL="C"))
    cur = ""
    for line in r.stdout.splitlines():
        m = re.match(r"File: .*\((.*)\)$", line)
        if m:
            cur = m.group(1)
        m = re.search(r"DW_AT_producer\s*:\s*(?:\([^)]*\):\s*)?(.*)$", line)
        if m:
            out.setdefault(cur, []).append(m.group(1).strip())
    return out


def expected(driver, kind):
    """The option set the pinned DRIVER records in the producer of a unit of KIND (c, cxx) built under the recipe's semantic set."""
    x, src = SRC[kind]
    with tempfile.TemporaryDirectory() as d:
        s, o = os.path.join(d, "p.c"), os.path.join(d, "p.o")
        open(s, "w").write(src)
        r = subprocess.run([driver, "-x", x, "-g", "-c", "-o", o] + recipe.semantics_for(driver, kind) + [s], capture_output=True, text=True,
                           env=dict(os.environ, LC_ALL="C"))
        if r.returncode:
            raise recipe.BuildRed({"stage": "producer-probe:" + kind, "exit": r.returncode, "stderr": r.stderr.splitlines()[-10:]})
        ps = [p for ps in cu_producers(o).values() for p in ps]
    if len(ps) != 1:
        raise recipe.BuildRed({"stage": "producer-probe:" + kind, "exit": None, "stderr": ["expected one compile unit, got %d" % len(ps)]})
    return options(ps[0])


def judge(foreign, exp):
    """FOREIGN {object id: [producer, ...]} (one id per object or archive member); EXP {"c": set, "cxx": set} -> ([ids], [(id, kind, detail)]).
    Pure function of its arguments."""
    items, finds = sorted(foreign), []
    for oid in items:
        ps = foreign[oid]
        if not ps:
            finds.append((oid, "no-producer", "no DW_AT_producer: its flags cannot be shown (built without -g)"))
        for p in ps:
            k = kind_of(p)
            if k == "asm":
                finds.append((oid, "asm-unproven", "assembler compile unit records no options: %s" % p))
            elif k is None:
                finds.append((oid, "flags-differ", "producer is not a GNU C or C++ compiler's: %s" % p[:80]))
            else:
                have, want = options(p), exp[k]
                if have != want:
                    finds.append((oid, "flags-differ", "%s: extra %s, missing %s" % (k, sorted(have - want), sorted(want - have))))
    return items, finds


def link_inputs(doc, at_to_host):
    """Foreign input files of a link stage document DOC: the argv tokens that name a file under an external's mount point (AT_TO_HOST {at: host dir}),
    and -lNAME through the argv's -L directories (libNAME.a, else libNAME.so). -> ([(display name, host path)] static inputs, [(display name, host path)] shared objects)."""
    argv, files, shared, ldirs = doc["argv"][1:], [], [], []
    host = lambda p: next((h + p[len(a):] for a, h in at_to_host.items() if p == a or p.startswith(a + "/")), None)  # noqa: E731
    i = 0
    while i < len(argv):
        t = argv[i]
        if t in ("-o", "-T", "-L", "-e", "-m", "-z", "-rpath") and i + 1 < len(argv):
            if t == "-L":
                ldirs.append(argv[i + 1])
            i += 2
            continue
        if t.startswith("-L"):
            ldirs.append(t[2:])
        elif t.startswith("-l"):
            for d in ldirs:
                for ext in (".a", ".so"):
                    p = host(d + "/lib" + t[2:] + ext)
                    if p and os.path.exists(p):
                        (files if ext == ".a" else shared).append((t, p))
                        break
                else:
                    continue
                break
        elif not t.startswith("-") and not t.startswith("/objs/"):
            p = host(t)
            if p:
                (shared if p.endswith(".so") else files).append((t, p))
        i += 1
    return files, shared


def foreign_items(root, doc, at_to_host, exp):
    """Rule (f) for ROOT whose link stage document is DOC: -> (items [(id, name)], findings [(id, name, kind, detail)]) in gate_linkstrict's shape.
    One item per foreign object or archive member (`<root>:<argv token>(<member>)`) and per shared object."""
    files, shared = link_inputs(doc, at_to_host)
    foreign = {}
    for disp, path in files:
        for m, ps in cu_producers(path).items():
            foreign["%s:%s%s" % (root, disp, "(%s)" % m if m else "")] = ps
    its, fs = judge(foreign, exp)
    items = [(i, "<foreign>") for i in its] + [("%s:%s" % (root, d), "<foreign>") for d, _ in shared]
    return items, [(i, "<foreign>", k, d) for i, k, d in fs] + [("%s:%s" % (root, d), "<foreign>", "shared-input", "a shared object in the link: " + p) for d, p in shared]
