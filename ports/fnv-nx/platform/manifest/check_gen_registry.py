#!/usr/bin/env python3
"""Selftest of gen_registry_seed.py on synthetic input (no exe-derived file): the accepted case and every refusal.

  check_gen_registry.py            (run with python3 -I)
"""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(HERE, "gen_registry_seed.py")
SITES_H = "module\tsite\tapi\troot\tsubkey\tvalue\tstatus\treason\n"
DECL_H = "root\tkey\tname\ttype\tdata\tsource\n"
SITES = SITES_H + "m.exe\t0x1000\tRegQueryValueExA\tHKEY_LOCAL_MACHINE\tSoftware\\Co\\App\tPath\tstatic\t\n"
SITES += "m.exe\t0x1010\tRegQueryValueExA\tHKEY_CURRENT_USER\tSoftware\\Co\\Other\tFlag\tstatic\t\n"
SITES += "m.exe\t0x1020\tRegOpenKeyExA\tHKEY_LOCAL_MACHINE\tSoftware\\Co\\Opened\t-\tstatic\t\n"
SITES += "m.exe\t0x1030\tRegQueryValueExA\tdynamic\tdynamic\tdynamic\tdynamic\tobject field\n"
GOOD = DECL_H + "HKEY_LOCAL_MACHINE\tSoftware\\Co\\App\tPath\tREG_SZ\t{game_dir}\tdeclared\n"


def run(td, sites, decl, extra=()):
    s, d, o = (os.path.join(td, n) for n in ("s.tsv", "d.tsv", "o.inc"))
    if sites is not None:
        open(s, "w").write(sites)
    if decl is not None:
        open(d, "w").write(decl)
    if os.path.exists(o):
        os.remove(o)
    r = subprocess.run([sys.executable, "-I", GEN, s, d, o, *extra], capture_output=True, text=True)
    return r.returncode, r.stderr, (open(o).read() if os.path.exists(o) else None)


def row(root, key, name, typ, data, src="declared"):
    return DECL_H + "\t".join((root, key, name, typ, data, src)) + "\n"


def main():
    bad = 0

    def expect(label, got, rc, frag=None):
        nonlocal bad
        ok = got[0] == rc and (frag is None or frag in got[1]) and (rc == 0 or got[2] is None)
        if not ok:
            bad += 1
            print("FAIL", label, got[:2])

    with tempfile.TemporaryDirectory() as td:
        g = run(td, SITES, GOOD)
        expect("good", g, 0)
        if "\\001" not in (g[2] or "") or '"Software\\\\Co\\\\App"' not in (g[2] or "") or "{ 0, 0, 0, 0, 0, 0 }" not in g[2]:
            bad += 1
            print("FAIL good include content", g[2])
        u = os.path.join(td, "u.tsv")
        expect("uncovered", run(td, SITES, GOOD, ("--uncovered", u)), 0)
        rep = open(u).read()
        if "Flag" not in rep or "Path" in rep or "Opened" in rep:
            bad += 1
            print("FAIL uncovered report", rep)
        expect("no read site", run(td, SITES, row("HKEY_LOCAL_MACHINE", "Software\\Co\\Gone", "Path", "REG_SZ", "x")), 2, "no static read site")
        expect("open is not a read", run(td, SITES, row("HKEY_LOCAL_MACHINE", "Software\\Co\\Opened", "", "REG_SZ", "x")), 2, "no static read site")
        expect("wrong root", run(td, SITES, row("HKEY_CURRENT_USER", "Software\\Co\\App", "Path", "REG_SZ", "x")), 2, "no static read site")
        nf = os.path.join(td, "absent.tsv")
        for label, args in (("missing sites", (nf, os.path.join(td, "d.tsv"))), ("missing decl", (os.path.join(td, "s.tsv"), nf))):
            r = subprocess.run([sys.executable, "-I", GEN, *args, os.path.join(td, "o2.inc")], capture_output=True, text=True)
            expect(label, (r.returncode, r.stderr, None), 2, "cannot read")
        expect("bad sites header", run(td, "x\ty\n", GOOD), 2, "header")
        expect("unknown root", run(td, SITES, row("HKEY_X", "K", "n", "REG_SZ", "v")), 2, "unknown root")
        expect("unknown type", run(td, SITES, row("HKEY_LOCAL_MACHINE", "Software\\Co\\App", "Path", "REG_FOO", "v")), 2, "unknown type")
        expect("bad token", run(td, SITES, row("HKEY_LOCAL_MACHINE", "Software\\Co\\App", "Path", "REG_SZ", "{pf}\\x")), 2, "only as")
        expect("bad number", run(td, SITES, row("HKEY_LOCAL_MACHINE", "Software\\Co\\App", "Path", "REG_DWORD", "4294967296")), 2, "does not fit")
        expect("bad hex", run(td, SITES, row("HKEY_LOCAL_MACHINE", "Software\\Co\\App", "Path", "REG_BINARY", "0g")), 2, "hex")
        expect("empty source", run(td, SITES, row("HKEY_LOCAL_MACHINE", "Software\\Co\\App", "Path", "REG_SZ", "x", " ")), 2, "source")
        expect("duplicate", run(td, SITES, GOOD + "HKEY_LOCAL_MACHINE\tsoftware\\co\\app\tPATH\tREG_SZ\ty\tdeclared\n"), 2, "duplicate")
        expect("short row", run(td, SITES, DECL_H + "HKEY_LOCAL_MACHINE\tK\tn\tREG_SZ\tv\n"), 2, "fields")
        expect("dword ok", run(td, SITES, row("HKEY_LOCAL_MACHINE", "Software\\Co\\App", "Path", "REG_DWORD", "0x10")), 0)
    print("check_gen_registry: %s" % ("FAIL" if bad else "ok"))
    sys.exit(1 if bad else 0)


main()
