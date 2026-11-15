#!/usr/bin/env python3
"""Selftest of gen_prefs_ini.py on synthetic input (no exe-derived file): the accepted case and every refusal."""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(HERE, "gen_prefs_ini.py")
REQ = "# key\tsection\tobject\tgate_function\nsD3DDevice\tDisplay\t0x1\t0x2\n"


def ident(desc, size=1100, nul=True):
    b = bytearray(size)
    b[0:7] = b"drv.dll"
    d = desc + (b"\0" if nul else b"")
    if nul:
        b[512:512 + len(d)] = d
    else:
        b[512:1024] = (desc * 600)[:512]
    return bytes(b)


def run(req, images):
    with tempfile.TemporaryDirectory() as d:
        rp = os.path.join(d, "req.tsv")
        open(rp, "w").write(req)
        args = [sys.executable, "-I", GEN, rp, os.path.join(d, "out.inc")]
        for i, e in enumerate(images):
            p = os.path.join(d, "ident%d.bin" % i)
            open(p, "wb").write(e)
            args += ["--ident", p]
        r = subprocess.run(args, capture_output=True, text=True)
        out = os.path.join(d, "out.inc")
        return r.returncode, (open(out).read() if os.path.exists(out) else None)


def main():
    bad = 0

    def expect(name, got, want_ok):
        nonlocal bad
        ok = (got[0] == 0) == want_ok and ((got[1] is not None) == want_ok)
        if not ok:
            bad += 1
            sys.stderr.write("FAIL %s: status %s\n" % (name, got[0]))

    rc, out = run(REQ, [ident(b"A B\\C")])
    expect("accept", (rc, out), True)
    if out is not None and "[Display]\\r\\nsD3DDevice=A B\\134C\\r\\n" not in out:
        bad += 1
        sys.stderr.write("FAIL accept: text %r\n" % out)
    expect("two images equal", run(REQ, [ident(b"X"), ident(b"X")]), True)
    expect("images differ", run(REQ, [ident(b"X"), ident(b"Y")]), False)
    expect("no --ident", run(REQ, []), False)
    expect("two required rows", run(REQ + "bFoo\tDisplay\t0x3\t0x2\n", [ident(b"X")]), False)
    expect("no required row", run("# key\n", [ident(b"X")]), False)
    expect("wrong size", run(REQ, [ident(b"X", size=1099)]), False)
    expect("empty Description", run(REQ, [ident(b"")]), False)
    expect("non printable", run(REQ, [ident(b"A\x07B")]), False)
    expect("no NUL", run(REQ, [ident(b"A", nul=False)]), False)
    expect("delimiter in key", run(REQ.replace("sD3DDevice", "s=D"), [ident(b"X")]), False)
    if bad:
        sys.exit(1)
    print("check_gen_prefs: ok")


main()
