#!/usr/bin/env python3
"""check_dynsites.py - what the platform answers to every dynamic module/name lookup the game and its modules make.

Input: the private, exe-derived dynsites.tsv (columns module, site, api, operand) - a PATH ARGUMENT; no row of it is embedded
here or written anywhere. The answers are not re-implemented: manifest/probe_dynsites (built by
`make -C tests/s4 probe`) calls the platform's own GetModuleHandleA / LoadLibraryA / GetProcAddress (module.cpp, exports.cpp).
Three things come from the repository's sources by rule:
  * ENV_TRUTH.md rows M2, M3, M4, M7: the modules that are absent by owner decision (every `<name>.dll` of those rows) and the
    function names the rows state for them (M7: DebugSetMute). LoadLibraryA / GetModuleHandleA of them must answer NULL +
    ERROR_MOD_NOT_FOUND (126); GetProcAddress on them has no module to ask.
  * the registered-resolver tables (render/nine/nine_register.cpp `kExports`, platform/input/register.cpp `X("mod","name",..)`):
    the names of d3d9 / dsound / dinput8 / xinput1_3, which the host probe does not load.
  * closure.tsv (next to dynsites.tsv, or --closure): the modules the loader translates (role static / dynamic / root); the
    loader answers their lookups, not the platform table.
Row classes (the operand column is a 48-byte window of string literals, so it is read by one rule, never guessed):
  static      every whitespace token of the operand is a clean token (letters, digits, _ . @ ? $, optional `L:` prefix). A token
              is a MODULE token iff it ends in `.dll` or names a known module (system table, ENV_TRUTH, closure); every other
              token is a NAME.
  non-static  `dynamic` operands, operands with any other character (sentences, GUIDs), rows with no module token for a
              module API, and every api that is not a name lookup (CoCreateInstance). Counted, never decided.
  unbound     GetProcAddress rows whose window holds names but no module token: the module is not statically known. Checked only
              by the necessary condition "some present module defines the name" (or the name is ENV_TRUTH's for an absent one).
Run time (platform/win32/module.cpp proc_address): GetProcAddress returns the import-key table's value for (dll, name), which
is generated from bridge.tsv, so a name resolves iff bridge.tsv has a row for it whose bind is not `none` - of ANY bind kind (sym, lookup,
l3, data): the platform's host tables are asked first only for the native address of host tests. With --bridge (default: bridge.tsv next
to dynsites.tsv) a (module, name) is therefore resolved iff that row exists; names the host probe or the resolver tables answer but that have
no row are reported as UNRESOLVED too (the product would fail them). Without --bridge only the probe and the tables decide.
For a static row:
  module token  -> translated | absent-by-ENV_TRUTH (the probe must answer 126 to both calls) | resolved (the probe answers a
                   module for both calls, in the letter-case and with/without `.dll` as the site passes it) | unresolved
  GetProcAddress (module token, name token) pairs -> translated | absent-by-ENV_TRUTH | resolved (probe yes / resolver table)
                   | unresolved (the module is present and defines no body for the name)
Output: counts, then one line per unresolved (module, name) and per unbound name defined nowhere. Exit 1 if anything is unresolved.
Usage: check_dynsites.py <dynsites.tsv> [--root ports/fnv-nx] [--closure closure.tsv] [--bridge bridge.tsv] [--probe path] [--selftest]
"""
import os
import re
import subprocess
import sys

CLEAN = re.compile(r"^(?:L:)?[A-Za-z0-9_.@?$]+$")
NAME_APIS = {"GetModuleHandleA", "GetModuleHandleW", "LoadLibraryA", "GetProcAddress"}
ERROR_MOD_NOT_FOUND = 126


def norm(m):
    m = m.lower()
    return m[:-4] if m.endswith(".dll") else m


def tokens(operand):
    """The clean tokens of an operand, or None when the operand is not static."""
    toks = operand.split()
    if not toks or operand.strip() == "dynamic" or not all(CLEAN.match(t) for t in toks):
        return None
    return [t[2:] if t.startswith("L:") else t for t in toks]


def env_truth(root):
    """(absent module names, function names ENV_TRUTH states for absent modules) from rows M2, M3, M4, M7."""
    mods, funcs = set(), set()
    with open(os.path.join(root, "manifest/ENV_TRUTH.md"), encoding="utf-8") as f:
        for line in f:
            if not re.match(r"\|\s*M[2347]\s*\|", line):
                continue
            for tick in re.findall(r"`([^`]+)`", line):
                if tick.lower().endswith(".dll"):
                    mods.add(norm(tick))
                elif re.match(r"^[A-Z][A-Za-z0-9_]*$", tick) and tick not in ("NULL",) and not tick.startswith("ERROR_") \
                        and not tick.startswith(("LoadLibrary", "GetModuleHandle", "GetProcAddress")):
                    funcs.add(tick)
    return mods, funcs


def registered_tables(root):
    """{module: set(names)} of the resolver-registered modules, from the two source tables."""
    tab = {}
    with open(os.path.join(root, "render/nine/nine_register.cpp"), encoding="utf-8") as f:
        s = f.read()
    m = re.search(r"kExports\[\]\s*=\s*\{(.*?)\n\};", s, re.S)
    tab["d3d9"] = set(re.findall(r'\{\s*"(\w+)"\s*,', m.group(1))) if m else set()
    with open(os.path.join(root, "platform/input/register.cpp"), encoding="utf-8") as f:
        for mod, name in re.findall(r'X\(\s*"(\w+)"\s*,\s*"(\w+)"', f.read()):
            tab.setdefault(mod, set()).add(name)
    return tab


def translated_modules(closure):
    out = set()
    if closure and os.path.exists(closure):
        with open(closure, encoding="utf-8") as f:
            for line in f.read().splitlines()[1:]:
                c = line.split("\t")
                if len(c) >= 2 and c[1] in ("static", "dynamic", "root"):
                    out.add(norm(c[0]))
    return out


class Probe:
    """The host probe, one process per batch of questions."""

    def __init__(self, path):
        self.path = path

    def ask(self, lines):
        r = subprocess.run([self.path], input="\n".join(lines) + "\n", capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("check_dynsites: probe failed: " + r.stderr.strip())
        return [l.split("\t") for l in r.stdout.splitlines()]


def hmod_modules(hmod):
    """The module literals of a dynsites hmod cell (comma list, `L:` = LoadLibrary result); [] when not known (`dynamic`, `-`)."""
    return [h[2:] if h.startswith("L:") else h for h in hmod.split(",") if h not in ("", "-", "dynamic", "hmod")]


def classify(rows, probe, absent_mods, absent_funcs, reg, translated, bridge=None):
    """rows: [(module, site, api, operand, hmod)]. Returns (counts, unresolved list, unbound-undefined list)."""
    rows = [(tuple(r) + ("",))[:5] if len(r) < 5 else tuple(r) for r in rows]
    mods = [c[1] for c in probe.ask(["MODS"]) if c[0] == "MODS"]
    known = {norm(m) for m in mods} | set(reg) | absent_mods | translated

    def is_mod(t):
        return t.lower().endswith(".dll") or norm(t) in known

    cnt = dict(rows=0, static=0, resolved=0, absent=0, translated=0, unresolved=0, nonstatic=0, other_api=0, unbound=0)
    unresolved, unbound_bad = [], []
    # --- pass 1: collect questions
    qs, plan = [], []
    for module, site, api, operand, hmod in rows:
        cnt["rows"] += 1
        if api not in NAME_APIS:
            cnt["other_api"] += 1
            cnt["nonstatic"] += 1
            continue
        toks = tokens(operand)
        if toks is None:
            cnt["nonstatic"] += 1
            continue
        mt = list(dict.fromkeys(t for t in toks if is_mod(t)))
        if api == "GetProcAddress":
            # the hmod column: the modules the module argument can be (pemanifest data flow); "dynamic"/"-" = not known
            mt = list(dict.fromkeys(mt + hmod_modules(hmod)))
        nt = list(dict.fromkeys(t for t in toks if not is_mod(t)))
        if api != "GetProcAddress":
            if not mt:
                cnt["nonstatic"] += 1
                continue
            cnt["static"] += 1
            plan.append(("mod", api, mt))
            for m in mt:
                qs += ["HANDLE\t" + m, "LOAD\t" + m]
        else:
            if not nt:
                cnt["nonstatic"] += 1
                continue
            if not mt:
                cnt["unbound"] += 1
                plan.append(("unbound", api, nt))
                continue
            cnt["static"] += 1
            plan.append(("gpa", api, [(m, n) for m in mt for n in nt]))
            for m in mt:
                if norm(m) not in translated and norm(m) not in absent_mods:
                    qs += ["GPA\t%s\t%s" % (m, n) for n in nt]
    # unbound names: asked of every present system module
    for kind, _, body in plan:
        if kind == "unbound":
            for n in body:
                qs += ["GPA\t%s\t%s" % (m, n) for m in mods]
    ans = {}
    for c in probe.ask(qs) if qs else []:
        ans[tuple(c[:-1] if c[0] == "GPA" else c[:2])] = c[-1] if c[0] == "GPA" else c[2:]
    seen_un = set()

    def note(m, n):
        if (m, n) not in seen_un:
            seen_un.add((m, n))
            unresolved.append((m, n))

    # --- pass 2: decide
    for kind, api, body in plan:
        if kind == "mod":
            for m in body:
                nm = norm(m)
                if nm in translated:
                    cnt["translated"] += 1
                    continue
                h, l = ans[("HANDLE", m)], ans[("LOAD", m)]
                present = h[0] == "present" and l[0] == "present"
                gone = h[0] == "absent" and l[0] == "absent" and h[1] == l[1] == str(ERROR_MOD_NOT_FOUND)
                if nm in absent_mods:
                    if gone:
                        cnt["absent"] += 1
                    else:
                        cnt["unresolved"] += 1
                        note(m, "<module must be absent: ENV_TRUTH>")
                elif present:
                    cnt["resolved"] += 1
                else:
                    cnt["unresolved"] += 1
                    note(m, "<module>")
        elif kind == "gpa":
            for m, n in body:
                nm = norm(m)
                if nm in translated:
                    cnt["translated"] += 1
                elif nm in absent_mods:
                    cnt["absent"] += 1
                elif (nm + ".dll", n) in bridge if bridge is not None else \
                        (n in reg.get(nm, ()) or ans.get(("GPA", m, n)) == "yes"):
                    cnt["resolved"] += 1
                else:
                    cnt["unresolved"] += 1
                    note(m, n)
        else:
            for n in body:
                if n in absent_funcs or (any(k[1] == n for k in bridge) if bridge is not None else
                                         (any(n in t for t in reg.values()) or
                                          any(ans.get(("GPA", m, n)) == "yes" for m in mods))):
                    continue
                if n not in unbound_bad:
                    unbound_bad.append(n)
    return cnt, unresolved, unbound_bad


def read_bridge(path):
    """{(dll, name)} of the rows whose bind is not `none`, dll lowercase; interface rows (`!Name`) are not modules."""
    out = set()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f.read().splitlines()[1:]:
                c = line.split("\t")
                if len(c) >= 8 and c[7] != "none" and not c[0].startswith("!"):
                    out.add((c[0].lower(), c[1]))
        return out
    return None


def run(tsv, root, closure, probe_path, bridge_path=None):
    with open(tsv, encoding="utf-8") as f:
        lines = f.read().splitlines()
    rows = [tuple((l.split("\t") + [""] * 5)[:5]) for l in lines[1:] if l.strip()]
    absent_mods, absent_funcs = env_truth(root)
    cnt, bad, unbound_bad = classify(rows, Probe(probe_path), absent_mods, absent_funcs, registered_tables(root),
                                     translated_modules(closure), read_bridge(bridge_path))
    print("dynsites: rows=%(rows)d static=%(static)d resolved=%(resolved)d absent-by-ENV_TRUTH=%(absent)d "
          "translated=%(translated)d unresolved=%(unresolved)d non-static=%(nonstatic)d (other-api=%(other_api)d) "
          "unbound-GetProcAddress=%(unbound)d" % cnt)
    if bridge_path and not os.path.exists(bridge_path):
        print("note: %s absent - decided by the host probe and resolver tables only" % bridge_path)
    for m, n in bad:
        print("UNRESOLVED %s!%s" % (m, n))
    for n in unbound_bad:
        print("UNBOUND-UNDEFINED %s (no module in the window; no present module defines it)" % n)
    return 1 if bad else 0


class FakeProbe:
    def __init__(self, mods, procs):
        self.mods, self.procs = mods, procs

    def ask(self, lines):
        out = []
        for l in lines:
            c = l.split("\t")
            if c[0] == "MODS":
                out += [["MODS", m] for m in self.mods] + [["END"]]
            elif c[0] in ("HANDLE", "LOAD"):
                ok = norm(c[1]) in self.mods
                out.append([c[0], c[1], "present"] if ok else [c[0], c[1], "absent", "126"])
            else:
                ok = norm(c[1]) in self.mods
                out.append(["GPA", c[1], c[2], ("yes" if (norm(c[1]), c[2]) in self.procs else "no") if ok else "nomodule"])
        return out


def selftest():
    fp = FakeProbe({"kernel32", "advapi32", "ws2_32"}, {("kernel32", "EncodePointer")})
    rows = [("x", "1", "GetModuleHandleA", "KERNEL32.DLL"),            # resolved
            ("x", "2", "GetModuleHandleA", "WS2_32"),                  # resolved, no .dll
            ("x", "3", "LoadLibraryA", "usp10.dll"),                   # absent by ENV_TRUTH
            ("x", "4", "LoadLibraryA", "nosuch.dll"),                  # unresolved module
            ("x", "5", "GetProcAddress", "KERNEL32.DLL EncodePointer"),   # resolved
            ("x", "6", "GetProcAddress", "KERNEL32 NoSuchFn"),         # unresolved name
            ("x", "7", "GetProcAddress", "GetFontUnicodeRanges usp10.dll"),   # name before module, absent
            ("x", "8", "GetProcAddress", "SystemFunction036"),         # unbound, defined nowhere
            ("x", "9", "GetProcAddress", "EncodePointer"),             # unbound, defined
            ("x", "10", "GetProcAddress", "dynamic"),                  # non-static
            ("x", "11", "GetProcAddress", "only vs_1_1, vs_2_0 are supported"),   # non-static
            ("x", "12", "CoCreateInstance", "{56A868A3-0AD4-11CE-B03A-0020AF0BA770}"),   # other api
            ("x", "13", "GetProcAddress", "d3d9.dll Direct3DCreate9"),   # registered table
            ("x", "14", "LoadLibraryA", "binkw32.dll"),                # translated
            ("x", "15", "GetModuleHandleA", "mscoree.dll")]            # absent (M4)
    c, bad, ub = classify(rows, FakeProbe({"kernel32", "advapi32", "ws2_32", "d3d9"}, {("kernel32", "EncodePointer")}),
                          {"usp10", "mscoree"}, set(), {"d3d9": {"Direct3DCreate9"}}, {"binkw32"})
    want = dict(rows=15, static=10, resolved=4, absent=3, translated=1, unresolved=2, nonstatic=3, other_api=1, unbound=2)
    ok = all(c[k] == v for k, v in want.items()) and bad == [("nosuch.dll", "<module>"), ("KERNEL32", "NoSuchFn")] \
        and ub == ["SystemFunction036"]
    if not ok:
        print("selftest FAILED:", c, bad, ub)
        return 1
    # the hmod column binds a name-only GetProcAddress: absent modules -> absent by ENV_TRUTH; present -> checked there
    rows = [("x", "1", "GetProcAddress", "SymInitialize", "dbghelp.dll"),        # absent by ENV_TRUTH
            ("x", "2", "GetProcAddress", "SymInitialize", "dynamic"),             # unbound, defined nowhere
            ("x", "3", "GetProcAddress", "EncodePointer", "L:kernel32.dll"),      # resolved through the hmod
            ("x", "4", "GetProcAddress", "NoSuchFn", "kernel32.dll,dbghelp.dll")]  # present member: unresolved
    c, bad, ub = classify(rows, FakeProbe({"kernel32"}, {("kernel32", "EncodePointer")}), {"dbghelp"}, set(), {}, set())
    if c["absent"] != 2 or c["resolved"] != 1 or c["unbound"] != 1 or bad != [("kernel32.dll", "NoSuchFn")] or ub != ["SymInitialize"]:
        print("selftest FAILED (hmod):", c, bad, ub)
        return 1
    # with a bridge: the row decides - a body-less name with a row resolves, a host-answered name without one does not
    br = {("kernel32.dll", "EncodePointer"), ("d3d9.dll", "DebugSetMute")}
    rows = [("x", "1", "GetProcAddress", "d3d9.dll DebugSetMute"), ("x", "2", "GetProcAddress", "kernel32 GetTickCount"),
            ("x", "3", "GetProcAddress", "EncodePointer"), ("x", "4", "GetProcAddress", "Other")]
    c, bad, ub = classify(rows, FakeProbe({"kernel32", "d3d9"}, {("kernel32", "GetTickCount")}), set(), set(), {}, set(), br)
    if c["resolved"] != 1 or bad != [("kernel32", "GetTickCount")] or ub != ["Other"]:
        print("selftest FAILED (bridge):", c, bad, ub)
        return 1
    print("selftest ok")
    return 0


def main(argv):
    root = "."
    closure = probe = tsv = bridge = None
    selftest_only = False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--root":
            root = argv[i + 1]; i += 1
        elif a == "--closure":
            closure = argv[i + 1]; i += 1
        elif a == "--bridge":
            bridge = argv[i + 1]; i += 1
        elif a == "--probe":
            probe = argv[i + 1]; i += 1
        elif a == "--selftest":
            selftest_only = True
        else:
            tsv = a
        i += 1
    if selftest_only:
        return selftest()
    if not tsv:
        print(__doc__)
        return 2
    if not os.path.exists(tsv):
        print("check_dynsites: private manifest %s absent - skipped" % tsv)
        return 0
    probe = probe or os.path.join(root, "tests/s4/build/probe_dynsites")
    closure = closure or os.path.join(os.path.dirname(tsv), "closure.tsv")
    if not os.path.exists(probe):
        sys.exit("check_dynsites: %s missing - build it: make -C tests/s4 probe" % probe)
    return run(tsv, root, closure, probe, bridge or os.path.join(os.path.dirname(tsv), "bridge.tsv"))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
