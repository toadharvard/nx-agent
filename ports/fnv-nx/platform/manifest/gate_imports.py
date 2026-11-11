#!/usr/bin/env python3
"""Gate "imports" (fnvnx-gate/1): every import of the manifest has a body, proven by the build products.

    python3 -s -B gate_imports.py --manifest sys_names.tsv --specs <spec-dir> --archive libfnvnx-platform.a --nm <nm>
        --probe <probe-result.tsv> [--extra rows.tsv]... [--external rows.tsv]... [--provided symbols.txt]...
env GATE_OUT (result dir); writes GATE_OUT/imports.json and GATE_OUT/imports.ids. Exit 0 iff green.

Universe: every row of the bridge table that bridgetable.py derives from the manifest (sys_names.tsv: dll, name) and the
rows of the --extra files (same 8-column format; the platform's own COM rows and the other owners' rows). A row is
certified iff its body is proven by a build product:
  sym/data  the row's native symbol is, in the symbol table of the archive (`nm --format=sysv`: class and type columns),
            a defined GLOBAL (not weak, not local) symbol of type FUNC (sym) or OBJECT (data)
  lookup    (msvcrt names served by the name table of crt/msvcrt_exports.cpp) the host probe (probe_imports.cpp, which calls
            fnvnx_win32_lookup_import) answered yes: bridgetable.py reads the probe result file
  l3        provided by the lifter runtime; rows of an --external file belong to another owner's build. Both are certified
            only if the row's native symbol is in a --provided list (that owner's `defined` symbols), else class external.
Class of an uncertified row (imports.ids: `dll!name<TAB>class`, sorted): none (bridgetable found no symbol of that name
anywhere in the archive's table), unresolved-symbol (named, but only undefined or local), weak (defined weak only),
not-a-function (a defined global of the wrong type: not FUNC for sym, not OBJECT for data), lookup-miss (msvcrt name the
probe does not answer), external. The decision reads symbol tables and the probe's answers, never a source text or a name.
imports.json: {"format":"fnvnx-gate/1","name":"imports","status","universe","certified","uncertified": first 50 ids,"detail"}.
A bad input is red with the reason in detail."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridgetable  # noqa: E402


def read_symbols(nm, archive):
    """{name: [(class letter, type)]} over every symbol line of `nm --format=sysv` (class: T/D/B/R/W/V/U..., lower = local)."""
    out = subprocess.run([nm, "--format=sysv", archive], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if out.returncode != 0:
        raise ValueError("nm failed: " + out.stderr.decode("latin1")[:200])
    syms = {}
    for line in out.stdout.decode("latin1").split("\n"):
        f = [x.strip() for x in line.split("|")]
        if len(f) < 7 or not f[0] or f[0] == "Name" or not f[2]:
            continue
        syms.setdefault(f[0], []).append((f[2], f[3]))
    return syms


def elf_class(ents, want):
    """None if some entry is a defined global strong symbol of type `want`, else the class of the failure."""
    defined = [(c, t) for c, t in ents if c != "U" and c != "w" and c != "v"]
    glob = [(c, t) for c, t in defined if c.isupper() and c not in ("W", "V")]
    if any(t == want for _, t in glob):
        return None
    if glob:
        return "not-a-function"
    if any(c in ("W", "V") for c, _ in defined):
        return "weak"
    return "unresolved-symbol"


def judge(rows, syms, extern_of, provided):
    """-> [(id, class or None)] in row order."""
    out = []
    for dll, name, _, _, _, _, sym, bind in rows:
        rid = "%s!%s" % (dll, name)
        if bind == "l3" or (dll, name) in extern_of:
            cls = None if sym in provided else "external"
        elif bind == "lookup":
            cls = None
        elif bind == "none":
            cls = "lookup-miss" if dll == "msvcrt.dll" and sym == "-" else "none"
        elif bind in ("sym", "data"):
            cls = elf_class(syms.get(sym, []), "OBJECT" if bind == "data" else "FUNC")
        else:
            cls = "none"
        out.append((rid, cls))
    return out


def main(argv):
    opt = {}
    for i in range(0, len(argv) - 1, 2):
        opt.setdefault(argv[i], []).append(argv[i + 1])
    why, res, ids = "", [], []
    try:
        syms = read_symbols(opt["--nm"][0], opt["--archive"][0])
        provided = set()
        for p in opt.get("--provided", []):
            provided |= set(open(p).read().split())
        extra = opt.get("--extra", []) + opt.get("--external", [])
        rows, errors, extra_of = bridgetable.derive(opt["--manifest"][0], opt["--specs"][0], set(syms),
                                                    bridgetable.read_probe(opt["--probe"][0]), extra)
        if errors:
            raise ValueError("%d bridge errors, first: %s" % (len(errors), errors[0]))
        ext = {k for k, p in extra_of.items() if p in opt.get("--external", [])}
        res = judge(rows, syms, ext, provided)
    except (ValueError, KeyError, OSError) as e:
        why = "input error: %s" % (str(e)[:300] or type(e).__name__)
    bad = sorted((rid, c) for rid, c in res if c)
    classes = {}
    for _, c in bad:
        classes[c] = classes.get(c, 0) + 1
    if why:
        status, detail = "red", why
    elif bad:
        status, detail = "red", "%d of %d imports without a proven body: %s" % (
            len(bad), len(res), ", ".join("%s %d" % kv for kv in sorted(classes.items())))
    else:
        status, detail = "green", "%d imports, every body proven by the archive's symbol table, the probe or a provided list" % len(res)
    doc = {"format": "fnvnx-gate/1", "name": "imports", "status": status, "universe": len(res),
           "certified": len(res) - len(bad), "uncertified": [rid for rid, _ in bad[:50]], "detail": detail}
    gout = os.environ["GATE_OUT"]
    with open(os.path.join(gout, "imports.json"), "w") as fh:
        json.dump(doc, fh, sort_keys=True)
    with open(os.path.join(gout, "imports.ids"), "w") as fh:
        for rid, c in bad:
            fh.write("%s\t%s\n" % (rid, c))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
