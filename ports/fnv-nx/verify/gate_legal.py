#!/usr/bin/env python3
"""Gate "legal" (fnvnx-gate/1): no public file pairs a guest address with a decomp-derived name (ARCHITECTURE.md, Legal).
    python3 -I gate_legal.py        cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/legal.json
Rule. A PAIR is a record of a public file that holds (a) a guest VA: a hexadecimal literal (`0x` + 5..8 hex digits, or a bare token of exactly 8 hex
  digits as the tables write them) whose value lies in the VA range [ImageBase, ImageBase + SizeOfImage) of the exe or of a guest image (the PE
  headers of contract/images.py's image set: the exe and every dll image reached from it, read from the pinned externals), and (b) a decomp-derived
  name: a token equal to a symbol that the decomp's own pristine MSVC objects (contract_inputs.json "msvc": the COFF objects of fnv-nx-build, a
  content-pinned external, read here at gate time and never copied) define as an external, or to that symbol with the C decoration cut (a leading `_`
  and a trailing `@N`) and that is not PUBLIC. A token is a maximal run of [A-Za-z0-9_?@$].
  PUBLIC (owner rule via L1): the name is defined by a pinned public artifact: source 1 = the exports of the declared system images (every dll of the
  redist external, the declared system32 set), source 2 = the dynamic symbols of the pinned toolchain libraries (the shared libraries of
  /usr/aarch64-linux-gnu/lib, env.json), source 3 = a pinned public third-party source tree the exe statically links (none is pinned yet: every
  such name stays decomp-derived and is red, which says to add the pin). Names are compared raw, with the C decoration cut, and as an MSVC
  name undecorated by llvm-undname (env.json clang-tools-18) against the c++filt of the library symbols. No allow-list.
Record: the line of a text file; for a .json file every JSON container's own scalar members (keys and values; an object, a row array), so a table
  row or a record is one unit whatever its layout. Every tracked file of the repository is read (bytes decoded with replacement), none is exempt.
Universe: every tracked file of the repository. Certified: files with no pair. uncertified: the first 50 `path:line` (a record of a .json file is
  named by its first line: the line of the file where the container opens is not known to a parsed document, so `path:record-index`). The result is
  public: it holds COUNTS only (files, records with a VA, pairs, per layer = the first two path components), never a pair, a name or an address.
Inputs: verify/contract_inputs.json (the same file and the same fetch as the contract gates: recipe.fetch_externals, tree_sha256 checked). A gate whose
  input is missing is RED with the reason, never skipped.
Stated limits (not hidden): a decimal address, an address split over tokens, an address that is an RVA, and a name that is only a part of a decorated
  C++ symbol (its class or function component) are not recognised; the cut C decoration is the only undecoration. Names are compared as whole tokens.
Exit 0 iff green."""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "contract"))
import gate_contract  # noqa: E402
import images  # noqa: E402
import modules  # noqa: E402
import recipe  # noqa: E402

TOKEN = re.compile(r"[A-Za-z0-9_?@$]+")
HEX = re.compile(r"(?<![A-Za-z0-9_])(?:0[xX]([0-9a-fA-F]{5,8})|([0-9a-fA-F]{8}))(?![A-Za-z0-9_])")


def coff_externals(data):
    """Names of the defined external symbols of a COFF object (storage class EXTERNAL, section number > 0 or absolute)."""
    if len(data) < 20:
        return set()
    nsec, ptr, nsym = struct.unpack_from("<HII", data, 2)[0], *struct.unpack_from("<II", data, 8)
    if not ptr or ptr + 18 * nsym > len(data):
        return set()
    strtab = ptr + 18 * nsym
    out, i = set(), 0
    while i < nsym:
        o = ptr + 18 * i
        nm, value, sec, typ, cls, aux = struct.unpack_from("<8sIhHBB", data, o)
        if cls == 2 and sec != 0:
            if nm[:4] == b"\0\0\0\0":
                off = struct.unpack_from("<I", nm, 4)[0]
                e = data.find(b"\0", strtab + off)
                name = data[strtab + off:e if e >= 0 else len(data)]
            else:
                name = nm.rstrip(b"\0")
            out.add(name.decode("latin-1"))
        i += 1 + aux
    return out


def with_decoration_cut(names):
    """The names and, for each, the same with a leading `_` and a trailing `@N` cut (the C decoration), as a set."""
    out = set(names)
    for n in names:
        m = re.sub(r"@\d+$", "", n[1:] if n.startswith("_") else n)
        if m:
            out.add(m)
    return {n for n in out if n}


def qualified(name):
    """A demangled C++ name without its parameter list and trailing qualifiers: the last top-level balanced `(...)` group is cut."""
    n = name.strip()
    while n.endswith((" const", " volatile")):
        n = n.rsplit(" ", 1)[0]
    if not n.endswith(")"):
        return n
    depth = 0
    for i in range(len(n) - 1, -1, -1):
        depth += (n[i] == ")") - (n[i] == "(")
        if depth == 0:
            return n[:i].strip()
    return n


def public_sources(ext, doc):
    """-> ({source id: set of names}) : 1 = exports of the redist dlls, 2 = dynamic symbols of the pinned toolchain libraries."""
    s1, s2 = set(), set()
    d = ext["redist"]["host"]
    for root, _, fs in os.walk(d):
        for f in sorted(fs):
            if f.lower().endswith(".dll"):
                try:
                    pe = modules.PE(open(os.path.join(root, f), "rb").read(), f)
                    s1 |= {k for k, *_ in pe.exports() if not k.startswith("#")}
                except (modules.StageError, struct.error, ValueError):
                    pass
    files = []
    for lib in ("/usr/aarch64-linux-gnu/lib", "/usr/lib/gcc-cross/aarch64-linux-gnu/13"):
        for f in sorted(os.listdir(lib)) if os.path.isdir(lib) else []:
            fp = os.path.join(lib, f)
            if os.path.isfile(fp) and not os.path.islink(fp) and (".so" in f or f.endswith((".a", ".o"))):
                files.append(fp)
    raw = set()
    for fp in files:          # every symbol the pinned toolchain objects define or reference (archives, crt*.o, shared libraries)
        dyn = ["-D"] if ".so" in os.path.basename(fp) else []
        r = subprocess.run(["aarch64-linux-gnu-nm"] + dyn + [fp], capture_output=True, text=True)
        for ln in r.stdout.splitlines():
            w = ln.split()
            if w and not ln.rstrip().endswith(":") and len(w) >= 2:
                raw.add(w[-1].split("@")[0])
    s2 |= raw
    dem = subprocess.run(["c++filt"], input="\n".join(sorted(raw)), capture_output=True, text=True).stdout.splitlines()
    s2 |= {qualified(x) for x in dem}
    return {1: s1, 2: s2}


UNDNAME = ["llvm-undname", "--no-calling-convention", "--no-return-type", "--no-access-specifier", "--no-member-type"]


def source_of(tok, pub):
    """The PUBLIC source id (1, 2) of the decomp token TOK, or 0: decomp-derived (and 3 is not pinned yet)."""
    cands = [tok, re.sub(r"@\d+$", "", tok[1:] if tok.startswith("_") else tok)]
    if tok.startswith("?"):
        r = subprocess.run(UNDNAME + [tok], capture_output=True, text=True)
        cands += [qualified(ln) for ln in r.stdout.splitlines() if ln.strip() and ln.strip() != tok]
    for sid in (1, 2):
        if any(c in pub[sid] for c in cands):
            return sid
    return 0


def in_ranges(v, ranges):
    return any(lo <= v < hi for lo, hi in ranges)


def pair_in(text, names, ranges):
    """True iff TEXT (one record) holds a VA of RANGES and a token of NAMES."""
    if not any(in_ranges(int(a or b, 16), ranges) for a, b in HEX.findall(text)):
        return False
    return any(t in names for t in TOKEN.findall(text))


def has_va(text, ranges):
    return any(in_ranges(int(a or b, 16), ranges) for a, b in HEX.findall(text))


def json_records(doc):
    """Every container's own scalar members (keys and values) as one string, in document order."""
    out = []

    def walk(x):
        if isinstance(x, dict):
            out.append(" ".join([str(k) for k in x] + [str(v) for v in x.values() if not isinstance(v, (dict, list))]))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            out.append(" ".join(str(v) for v in x if not isinstance(v, (dict, list))))
            for v in x:
                walk(v)
    walk(doc)
    return out


def scan_file(path, data, names, ranges, judge=None, tally=None):
    """-> (records with a VA, [pair ids]) of one file's bytes. JUDGE(token) -> 0 decomp-derived, 1/2 public source (default: every name in NAMES is
    decomp-derived); TALLY, if given, counts every (class) of a name token that meets a VA in its record."""
    judge = judge or (lambda t: 0)
    text = data.decode("utf-8", "replace")
    nva, pairs = 0, []
    recs = None
    if path.endswith(".json"):
        try:
            recs = [(str(i), r) for i, r in enumerate(json_records(json.loads(text)))]
        except ValueError:
            recs = None
    if recs is None:
        recs = [(str(i + 1), ln) for i, ln in enumerate(text.splitlines())]
    for rid, r in recs:
        if has_va(r, ranges):
            nva += 1
            cl = [judge(t) for t in TOKEN.findall(r) if t in names]
            if tally is not None:
                for c in cl:
                    tally[c] = tally.get(c, 0) + 1
            if 0 in cl:
                pairs.append("%s:%s" % (path, rid))
    return nva, pairs


def layer(path):
    p = path.split("/")
    return "/".join(p[:2]) if len(p) > 2 else p[0]


def externals_inputs(repo, cache):
    """-> (names set, ranges [(lo, hi)], workdir to remove): the decomp's external symbols and the image VA ranges, from the pinned externals."""
    doc = gate_contract.load_inputs(os.path.join(repo, gate_contract.INPUTS))
    ws = tempfile.mkdtemp(prefix="legal-ext-")
    ext = recipe.fetch_externals(doc["external"], os.path.join(cache, "store"), os.path.join(cache, "external"), ws)
    bridge = set()
    if doc.get("bridge"):
        bridge = {r["dll"] for r in gate_contract.iat.bridge_rows(gate_contract.P2(ext, doc["bridge"]))[0]}
    ranges = []
    for name, path in images.image_set(gate_contract.P2(ext, doc["exe"]), [ext[n]["host"] for n in ("exe", "redist")], bridge):
        pe = modules.PE(open(path, "rb").read(), name)
        ranges.append((pe.base, pe.base + pe.size_of_image))
    names, d = set(), gate_contract.P2(ext, doc["msvc"])
    for f in sorted(os.listdir(d)):
        if f.endswith(".obj"):
            names |= coff_externals(open(os.path.join(d, f), "rb").read())
    return with_decoration_cut(names), ranges, ws, public_sources(ext, doc)


def main():
    repo = "."
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-legal-")
    cache = os.environ.get("GATE_CACHE") or tmp
    ws = None
    try:
        files = [p for p in subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout.decode("utf-8", "surrogateescape").split("\0") if p]
        try:
            names, ranges, ws, pub = externals_inputs(repo, cache)
        except (gate_contract.Red, recipe.BuildRed, OSError, ValueError, KeyError, modules.StageError) as e:
            res = {"format": "fnvnx-gate/1", "name": "legal", "status": "red", "universe": len(files), "certified": 0, "uncertified": ["input"],
                   "detail": ("input: %s" % (e.args[0] if e.args else type(e).__name__))[:300]}
            return finish(res)
        bad, census, nva, tally, memo = [], {}, 0, {}, {}

        def judge(t):
            if t not in memo:
                memo[t] = source_of(t, pub)
            return memo[t]
        for p in files:
            fp = os.path.join(repo, p)
            if not os.path.isfile(fp) or os.path.islink(fp):
                continue
            n, pairs = scan_file(p, open(fp, "rb").read(), names, ranges, judge, tally)
            nva += n
            c = census.setdefault(layer(p), {"files": 0, "records_with_va": 0, "pairs": 0, "files_with_pairs": 0})
            c["files"] += 1
            c["records_with_va"] += n
            c["pairs"] += len(pairs)
            c["files_with_pairs"] += bool(pairs)
            bad += pairs
        badfiles = sorted({b.rsplit(":", 1)[0] for b in bad})
        detail = "%d files, %d decomp names, %d image ranges, %d records with a guest VA, %d pairs in %d files" % (
            len(files), len(names), len(ranges), nva, len(bad), len(badfiles))
        res = {"format": "fnvnx-gate/1", "name": "legal", "status": "red" if bad else "green", "universe": len(files),
               "certified": len(files) - len(badfiles), "uncertified": sorted(bad)[:50], "detail": detail,
               "public_sources": {"1": len(pub[1]), "2": len(pub[2]), "3": 0}, "name_hits": {"decomp": tally.get(0, 0), "source1": tally.get(1, 0), "source2": tally.get(2, 0)},
               "pairs": len(bad), "files_with_pairs": len(badfiles), "records_with_va": nva, "census": dict(sorted(census.items()))}
        return finish(res)
    finally:
        for d in (tmp, ws):
            if d and not os.environ.get("GATE_KEEP"):
                shutil.rmtree(d, ignore_errors=True)


def finish(res):
    with open(os.path.join(os.environ["GATE_OUT"], "legal.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    print("legal: %s %d/%d %s" % (res["status"], res["certified"], res["universe"], res["detail"][:200]))
    return 0 if res["status"] == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
