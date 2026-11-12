#!/usr/bin/env python3
"""Tests for verify/producers.py (rule (f) of gate linkstrict): the objects a link takes from externals show the recipe's semantic flag set in their
DW_AT_producer. Real objects and archives built by the pinned msvcmodel compiler (fetched by the pin of verify/guest_compiler.json; red with the reason
when it cannot be fetched, never skipped). Plain script: PASS/FAIL lines, exit 0 on pass."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import test_plugins as tp  # noqa: E402
from test_plugins import BASE  # noqa: E402
import producers as P  # noqa: E402
import recipe  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402


def cc(drv, x, src, out, flags):
    open(src, "w").write("int fnvnx_x;\nint f(void) { return fnvnx_x; }\n" if x != "assembler-with-cpp" else ".text\n.globl sx\nsx: ret\n")
    r = subprocess.run([drv, "-x", x, "-c", "-o", out] + flags + [src], capture_output=True, text=True)
    case("compile %s", r.returncode == 0, r.stderr[-300:], key=os.path.basename(out))


def main():
    # --- pure: judge and link_inputs
    exp = {"c": frozenset({"-a", "-b"}), "cxx": frozenset({"-a", "-b", "-c"})}
    prod = lambda lang, *o: "GNU %s 13.3.0 %s" % (lang, " ".join(o))  # noqa: E731
    items, finds = P.judge({"x.o": [prod("C17", "-a", "-b", "-O2", "-g", "-Wall", "-DX=1")], "y.o": [prod("C++17", "-a", "-b", "-c", "-std=gnu++17")]}, exp)
    case("judge: producers with the expected options (per-root classes ignored) certify", finds == [] and items == ["x.o", "y.o"], finds)
    items, finds = P.judge({"x.o": [prod("C17", "-a")], "y.o": [prod("C17", "-a", "-b", "-ffast-math")], "z.o": [], "w.o": ["GNU AS 2.42"], "v.o": ["clang version 17"]}, exp)
    kinds = {i: k for i, k, _ in finds}
    case("judge: missing, extra, no producer, assembler, other compiler", kinds == {"x.o": "flags-differ", "y.o": "flags-differ", "z.o": "no-producer", "w.o": "asm-unproven",
                                                                                      "v.o": "flags-differ"}, finds)
    case("judge: the detail names the extra and the missing option", any("-ffast-math" in d for _, _, d in finds) and any("'-b'" in d for _, _, d in finds), finds)
    case("judge: a C++ unit needs the C++ set", P.judge({"a.o": [prod("C++17", "-a", "-b")]}, exp)[1][0][1] == "flags-differ")
    d = BASE + "/li"
    os.makedirs(d + "/lib")
    for n in ("libc.a", "libs.so", "x.o"):
        open("%s/lib/%s" % (d, n), "w").write("")
    doc = {"argv": ["/ext/ld", "-T", "/ld/s.ld", "-o", "/out/e.elf", "-L/ext/a/lib", "/objs/u.o", "/ext/a/lib/x.o", "-lc", "-ls", "-lnone", "/other/y.o"]}
    files, shared = P.link_inputs(doc, {"/ext/a": d})
    case("link_inputs: explicit path, -l through -L (.a), shared object, own objects and unknown paths ignored",
          files == [("/ext/a/lib/x.o", d + "/lib/x.o"), ("-lc", d + "/lib/libc.a")] and shared == [("-ls", d + "/lib/libs.so")], (files, shared))
    # --- real objects
    pin = json.load(open(os.path.join(tp.REAL, tp.T + "verify/guest_compiler.json")))
    ext = pin["external"]
    red = None
    try:
        got = recipe.fetch_externals([ext], os.path.join(BASE, "ingest-store"), os.path.join(BASE, "extcache"), BASE)
    except recipe.BuildRed as x:
        red = x
    case("the pinned external is fetched", red is None, red.args if red else "")
    if red:
        return
    pre = got[ext["name"]]["host"] + "/bin/aarch64-linux-gnu-"
    gcc, gxx = pre + "gcc", pre + "g++"
    ex = {"c": P.expected(gcc, "c"), "cxx": P.expected(gxx, "cxx")}
    case("expected set: the semantic flags the compiler records, no -g/-O/-W/-D/-std", {"-fsigned-char", "-ffp-contract=off", "-mabi=ilp32", "-mno-outline-atomics", "-fPIC"} <= ex["c"]
          and not any(recipe.PER_ROOT.fullmatch(t) for t in ex["c"] | ex["cxx"]) and "-fno-sized-deallocation" in ex["cxx"] and "-fno-sized-deallocation" not in ex["c"], sorted(ex["c"]))
    sem = lambda k: recipe.semantics_for(gcc if k != "cxx" else gxx, k)  # noqa: E731
    w = BASE + "/pr"
    os.makedirs(w)
    cc(gcc, "c", w + "/g.c", w + "/good.o", sem("c") + ["-g", "-O2"])
    cc(gcc, "c", w + "/b.c", w + "/bad.o", [f for f in sem("c") if f != "-ffp-contract=off"] + ["-g"])
    cc(gcc, "c", w + "/n.c", w + "/nog.o", sem("c"))
    cc(gcc, "c", w + "/e.c", w + "/extra.o", sem("c") + ["-g", "-ffast-math"])
    cc(gxx, "c++", w + "/x.c", w + "/cxx.o", sem("cxx") + ["-g", "-O1", "-std=gnu++17"])
    cc(gcc, "assembler-with-cpp", w + "/s.S", w + "/asm.o", sem("asm") + ["-g"])
    subprocess.run(["aarch64-linux-gnu-ar", "rcs", w + "/l.a", w + "/good.o", w + "/bad.o"], check=True)
    cur = lambda p: P.cu_producers(p)  # noqa: E731
    case("cu_producers reads a plain object and an archive's members", list(cur(w + "/good.o")) == [""] and len(cur(w + "/good.o")[""]) == 1 and sorted(cur(w + "/l.a")) == ["bad.o", "good.o"]
          and cur(w + "/nog.o") == {"": []}, (cur(w + "/good.o"), cur(w + "/l.a"), cur(w + "/nog.o")))
    foreign = {n: cur(w + "/%s.o" % n)[""] for n in ("good", "bad", "nog", "extra")}
    items, finds = P.judge(foreign, ex)
    k = {i: kk for i, kk, _ in finds}
    case("real objects: the object built with the set is certified; a missing flag, a foreign flag and a no -g object are findings",
          k == {"bad": "flags-differ", "extra": "flags-differ", "nog": "no-producer"}, finds)
    case("the missing flag is named", any(i == "bad" and "-ffp-contract=off" in d for i, _, d in finds), finds)
    case("a C++ object is judged against the C++ set", P.judge({"cxx": cur(w + "/cxx.o")[""]}, ex)[1] == [], P.judge({"cxx": cur(w + "/cxx.o")[""]}, ex))
    case("a C++ object does not satisfy the C set's absence of C++-only flags", P.judge({"cxx": cur(w + "/cxx.o")[""]}, {"c": ex["c"], "cxx": ex["c"]})[1] != [])
    case("an assembler unit is unproven", [kk for _, kk, _ in P.judge({"asm": cur(w + "/asm.o")[""]}, ex)[1]] == ["asm-unproven"], cur(w + "/asm.o"))
    arch = {"l.a(%s)" % m: ps for m, ps in cur(w + "/l.a").items()}
    case("archive members are judged one by one", {i: kk for i, kk, _ in P.judge(arch, ex)[1]} == {"l.a(bad.o)": "flags-differ"})
    # the rule as the gate applies it: a link stage document that names the archive, an explicit object and a shared object under an external's mount
    open(w + "/lib.so", "w").write("")
    ldoc = {"argv": ["/ext/ld", "-T", "/ld/s.ld", "-o", "/out/e.elf", "/objs/u.o", "/e/l.a", "/e/bad.o", "/e/good.o", "/e/lib.so"]}
    items, finds = P.foreign_items("R", ldoc, {"/e": w}, ex)
    case("foreign_items: items are the archive members, the objects and the shared object", [i for i, _ in items] == ["R:/e/bad.o", "R:/e/good.o", "R:/e/l.a(bad.o)", "R:/e/l.a(good.o)",
                                                                                                                           "R:/e/lib.so"], items)
    case("foreign_items: findings are exactly the object with the missing flag (twice) and the shared object", sorted((i, k) for i, _, k, _ in finds) == [
        ("R:/e/bad.o", "flags-differ"), ("R:/e/l.a(bad.o)", "flags-differ"), ("R:/e/lib.so", "shared-input")], finds)


if __name__ == "__main__":
    main()
    print("FAILED: %s" % tp.FAILED if tp.FAILED else "all passed")
    sys.exit(1 if tp.FAILED else 0)
