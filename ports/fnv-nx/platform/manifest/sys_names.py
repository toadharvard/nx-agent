#!/usr/bin/env python3
"""sys_names.py - the (dll, name) set the platform must implement, derived by rule from the exe-derived manifest.

Rule (the name set of bridgetable.py's input):
    sys_names = I u D u U, restricted to modules the loader does NOT translate (closure.tsv roles static/dynamic/root),
    minus modules that are absent by owner decision (ENV_TRUTH M2/M3/M4/M7, never loaded: no name of them is requested at run time)
  I = every (dll, name | #ordinal) of imports.tsv (the load contract: every slot is resolved before the entry point).
  D = every static dynsites.tsv GetProcAddress (module token, name token) pair (check_dynsites.py row classes).
  U = every module-less GetProcAddress name that EXACTLY ONE present system module defines (asked of the platform
      through probe_dynsites; a name defined by none or by several modules is left out and stays reported by check_dynsites.py).
No hand list; output is sorted, one `dll<TAB>name` per line.

Usage: sys_names.py <manifest-dir> [--root ports/fnv-nx] [--probe path] [--out file] [--selftest]
  <manifest-dir> holds imports.tsv, dynsites.tsv, closure.tsv. Everything read from it stays private; nothing is embedded here.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_dynsites as cd  # noqa: E402


def derive(imports, dynsites, translated, absent_mods, reg, probe):
    """imports: [(dll, name)]; dynsites: [(module, site, api, operand[, hmod])]. Returns the sorted list of lines."""
    out = set()
    for dll, name in imports:
        if cd.norm(dll) not in translated:
            out.add((cd.norm(dll) + ".dll", name))
    dynsites = [(tuple(r) + ("",))[:5] if len(r) < 5 else tuple(r) for r in dynsites]
    mods = [c[1] for c in probe.ask(["MODS"]) if c[0] == "MODS"]
    known = {cd.norm(m) for m in mods} | set(reg) | absent_mods | translated
    unbound = []
    for _, _, api, operand, hmod in dynsites:
        if api != "GetProcAddress":
            continue
        toks = cd.tokens(operand)
        if toks is None:
            continue
        mt = list(dict.fromkeys(t for t in toks if t.lower().endswith(".dll") or cd.norm(t) in known))
        mt = list(dict.fromkeys(mt + cd.hmod_modules(hmod)))
        nt = list(dict.fromkeys(t for t in toks if not (t.lower().endswith(".dll") or cd.norm(t) in known)))
        if not nt:
            continue
        if mt:
            for m in mt:
                if cd.norm(m) not in translated and cd.norm(m) not in absent_mods:
                    out.update((cd.norm(m) + ".dll", n) for n in nt)
        else:
            unbound += nt
    qs = ["GPA\t%s\t%s" % (m, n) for n in dict.fromkeys(unbound) for m in mods]
    yes = {}
    for c in probe.ask(qs) if qs else []:
        if c[0] == "GPA" and c[-1] == "yes":
            yes.setdefault(c[2], []).append(c[1])
    for n in dict.fromkeys(unbound):
        owners = set(yes.get(n, ())) | {m for m, t in reg.items() if n in t}
        if len(owners) == 1:
            out.add((cd.norm(next(iter(owners))) + ".dll", n))
    return ["%s\t%s" % p for p in sorted(out)]


def selftest():
    fp = cd.FakeProbe({"kernel32", "advapi32"}, {("advapi32", "SystemFunction036"), ("kernel32", "Both"), ("advapi32", "Both")})
    imps = [("KERNEL32.dll", "GetTickCount"), ("binkw32.dll", "_BinkOpen@8"), ("xinput1_3.dll", "#2")]
    dyn = [("x", "1", "GetProcAddress", "KERNEL32 EncodePointer"), ("x", "2", "GetProcAddress", "SystemFunction036"),
           ("x", "3", "GetProcAddress", "Both"), ("x", "4", "GetProcAddress", "Nowhere"),
           ("x", "5", "GetProcAddress", "usp10.dll Foo"), ("x", "6", "GetProcAddress", "dynamic"),
           ("x", "7", "GetProcAddress", "EncodePointer", "dynamic"), ("x", "8", "GetProcAddress", "OnlyViaHmod", "L:advapi32.dll"),
           ("x", "9", "GetProcAddress", "Foo2", "usp10.dll")]
    got = derive(imps, dyn, {"binkw32"}, {"usp10"}, {}, fp)
    want = ["advapi32.dll\tOnlyViaHmod", "advapi32.dll\tSystemFunction036", "kernel32.dll\tEncodePointer", "kernel32.dll\tGetTickCount", "xinput1_3.dll\t#2"]
    if got != want:
        print("selftest FAILED:", got)
        return 1
    print("selftest ok")
    return 0


def main(argv):
    if "--selftest" in argv:
        return selftest()
    args, opt, i = [], {}, 1
    while i < len(argv):
        if argv[i] in ("--root", "--probe", "--out"):
            opt[argv[i]] = argv[i + 1]
            i += 1
        else:
            args.append(argv[i])
        i += 1
    if not args:
        print(__doc__)
        return 2
    m = args[0]
    root, probe, out = opt.get("--root", "."), opt.get("--probe"), opt.get("--out")
    probe = probe or os.path.join(root, "tests/s4/build/probe_dynsites")
    with open(os.path.join(m, "imports.tsv"), encoding="utf-8") as f:
        imports = [(c[1], c[2]) for c in (l.split("\t") for l in f.read().splitlines()[1:]) if len(c) >= 3]
    with open(os.path.join(m, "dynsites.tsv"), encoding="utf-8") as f:
        dyn = [tuple((l.split("\t") + [""] * 5)[:5]) for l in f.read().splitlines()[1:] if l.strip()]
    absent, _ = cd.env_truth(root)
    lines = derive(imports, dyn, cd.translated_modules(os.path.join(m, "closure.tsv")), absent, cd.registered_tables(root),
                   cd.Probe(probe))
    text = "\n".join(lines) + "\n"
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
