#!/usr/bin/env python3
"""The RTL-pass table of toolchain/order/README.md, derived from the PINNED GCC 13.3.0 source tarball.

usage: rtlpasses.py <gcc-13.3.0.tar.xz> [--write | --check]    (default: print the table)

The tarball must have the sha256 of the pin of toolchain/msvcmodel/build.sh (the same pin as gen_foldset.py).
Derived exactly from the tarball:
  universe   every NEXT_PASS of gcc/passes.def from `pass_expand` to the end of the file (the RTL passes: pass_expand turns the last
             GIMPLE form into RTL), nested groups included, in file order; the name of a pass that occurs more than once is one pass
  file       the source file and line of its `make_pass_<name> (gcc::context *ctxt)` definition
  candidates every line of that file that holds one of CANDIDATE_IDENTS, the tests an RTL pass uses to refuse or to keep the order
             of a volatile / atomic access
Read by a reviewer, recorded in rtlpasses.json (keyed by the pass and the sha256 of every cited file of the tarball):
  class      "refuses"  : a cited test decides that a MEM with MEM_VOLATILE_P (or an insn with UNSPEC_VOLATILE) is not created,
                          merged, split or moved by the pass
             "no-mem"   : the cited lines are where the pass creates or changes insns and none of them creates, merges, splits or
                          moves a MEM (the reason says why)
             "expand"   : pass_expand, the pass that creates the MEMs
Errors (exit 1): a pass of the universe without a row, a row without a pass of the universe, a pass whose make_pass cannot be found, a
file whose sha256 differs from the row's key, a cited line that does not hold the cited identifier."""
import hashlib, io, json, os, re, sys, tarfile

PIN = "0845e9621c9543a13f484e94584a49ffc0129970e9914624235fc1d061a0c083"
CANDIDATE_IDENTS = ("MEM_VOLATILE_P", "UNSPEC_VOLATILE", "volatile_insn_p", "volatile_refs_p", "side_effects_p", "MEM_READONLY_P")
HERE = os.path.dirname(os.path.abspath(__file__))
ROWS = os.path.join(HERE, "rtlpasses.json")
OUT = os.path.join(HERE, "rtlpasses.md")

def load(tarball):
    raw = open(tarball, "rb").read()
    if hashlib.sha256(raw).hexdigest() != PIN:
        raise SystemExit("tarball sha256 differs from the pin " + PIN)
    tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:xz")
    files = {}
    for m in tf.getmembers():
        n = m.name
        if m.isfile() and n.startswith("gcc-13.3.0/gcc/") and n.count("/") == 2 and n.endswith((".cc", ".def", ".c", ".h")):
            files[n[len("gcc-13.3.0/gcc/"):]] = tf.extractfile(m).read()
    return files

def universe(passes_def):
    names, started = [], False
    for l in passes_def.split("\n"):
        m = re.match(r"\s*NEXT_PASS \((pass_\w+)", l)
        if not m:
            continue
        if m.group(1) == "pass_expand":
            started = True
        if started and m.group(1) not in names:
            names.append(m.group(1))
    return names

def make_pass_sites(files):
    sites = {}
    for fn, raw in files.items():
        if not fn.endswith(".cc"):
            continue
        for i, l in enumerate(raw.decode("utf-8", "replace").split("\n")):
            m = re.match(r"^make_pass_(\w+) \(gcc::context", l)
            if m:
                sites.setdefault("pass_" + m.group(1), []).append((fn, i + 1))
    return sites

def candidates(files, fn):
    out = []
    for i, l in enumerate(files[fn].decode("utf-8", "replace").split("\n")):
        for ident in CANDIDATE_IDENTS:
            if re.search(r"\b%s\b" % ident, l):
                out.append((i + 1, ident))
    return out

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = [a for a in sys.argv[1:] if a.startswith("--")]
    files = load(args[0])
    uni = universe(files["passes.def"].decode())
    sites = make_pass_sites(files)
    rows = json.load(open(ROWS))["rows"] if os.path.exists(ROWS) else []
    byname = {}
    errors = []
    for r in rows:
        if r["pass"] in byname:
            errors.append("duplicate row for %s" % r["pass"])
        byname[r["pass"]] = r
    for p in byname:
        if p not in uni:
            errors.append("row for %s, which is not an RTL pass of passes.def" % p)
    table = []
    for p in uni:
        s = sites.get(p)
        if not s or len(s) != 1:
            errors.append("%s: make_pass_%s found %d times (need exactly 1)" % (p, p[5:], len(s or [])))
            continue
        fn, ln = s[0]
        cand = candidates(files, fn)
        r = byname.get(p)
        if not r:
            errors.append("%s (%s:%d): no classification row" % (p, fn, ln))
            table.append((p, fn, ln, len(cand), "UNCLASSIFIED", ""))
            continue
        if r["class"] not in ("refuses", "no-mem", "expand"):
            errors.append("%s: unknown class %s" % (p, r["class"]))
        for f, sha in r["files"].items():
            if f not in files:
                errors.append("%s: cited file %s is not in the tarball" % (p, f))
            elif hashlib.sha256(files[f]).hexdigest() != sha:
                errors.append("%s: file %s has sha256 %s, the row is keyed by %s" % (p, f, hashlib.sha256(files[f]).hexdigest(), sha))
        if not r["cites"]:
            errors.append("%s: a row without a cited line" % p)
        cites = []
        for c in r["cites"]:
            f, line, ident = c["file"], c["line"], c["ident"]
            if f not in r["files"]:
                errors.append("%s: cited file %s has no sha256 key" % (p, f))
                continue
            ls = files[f].decode("utf-8", "replace").split("\n") if f in files else []
            if not (0 < line <= len(ls)) or not re.search(r"\b%s\b" % re.escape(ident), ls[line - 1]):
                errors.append("%s: %s:%d does not hold %s" % (p, f, line, ident))
            cites.append("%s:%d `%s`" % (f, line, ident))
        table.append((p, fn, ln, len(cand), r["class"], "; ".join(cites) + " — " + r["reason"]))
    out = ["# RTL passes of GCC 13.3.0 and what each does to a volatile / atomic MEM", "",
           "Generated by `rtlpasses.py` from the pinned tarball (sha256 %s) and `rtlpasses.json`. Do not edit." % PIN, "",
           "| Pass | make_pass | candidate tests in that file | class | deciding lines |", "|---|---|---|---|---|"]
    for p, fn, ln, n, cl, why in table:
        out.append("| %s | %s:%d | %d | %s | %s |" % (p, fn, ln, n, cl, why))
    text = "\n".join(out) + "\n"
    if errors:
        sys.stderr.write("\n".join(errors) + "\n%d error(s)\n" % len(errors))
    if "--write" in mode:
        open(OUT, "w").write(text)
    elif "--check" in mode:
        if not os.path.exists(OUT) or open(OUT).read() != text:
            errors.append("rtlpasses.md is not what the generator emits")
            sys.stderr.write("rtlpasses.md is not what the generator emits\n")
    else:
        sys.stdout.write(text)
    sys.exit(1 if errors else 0)

main()
