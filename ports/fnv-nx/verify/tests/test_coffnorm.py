#!/usr/bin/env python3
"""Tests for verify/coffnorm.py. Plain script: exit 0 on pass, one PASS/FAIL line per case.
Inputs are synthetic (struct.pack); a real object/archive is added when a COFF-capable compiler is installed."""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "coffnorm.py")
ROOT = tempfile.mkdtemp(prefix="coffnorm-test-")
sys.path.insert(0, os.path.dirname(HERE))
import testcase  # noqa: E402
from testcase import case  # noqa: E402
FAILED = testcase.FAILED
_n = [0]
DATE0 = b"0" + b" " * 11


def sh(*args):
    p = subprocess.run([sys.executable, "-I", TOOL] + list(args), capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def put(data, name="in.bin"):
    _n[0] += 1
    d = os.path.join(ROOT, "t%d" % _n[0])
    os.makedirs(d)
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


def rd(p):
    with open(p, "rb") as f:
        return f.read()


def norm(data):
    src = put(data)
    out = src + ".norm"
    rc, so, se = sh("normalize", src, "-o", out)
    return rc, (rd(out) if rc == 0 else None), se


def field_table(data):
    """(offset, width, name, value) rows from `fields`."""
    rc, so, se = sh("fields", put(data))
    assert rc == 0, se
    rows = []
    for ln in so.splitlines():
        t = ln.split()
        rows.append((int(t[0], 16), int(t[1]), t[2], t[3] if len(t) > 3 else ""))
    return rows


def diff_offsets(a, b):
    assert len(a) == len(b)
    return [i for i in range(len(a)) if a[i] != b[i]]


# ---- synthetic builders ----
def cv_ts2(name=b"x.pdb"):
    body = struct.pack("<H", 0x1515) + bytes(range(1, 17)) + struct.pack("<I", 1) + name + b"\0"
    while (len(body) + 2) % 4:
        body += b"\xf1"
    return struct.pack("<I", 4) + struct.pack("<H", len(body)) + body


def cv_ts1(sig=0x4a3b2c1d, name=b"x.pdb"):
    body = struct.pack("<HII", 0x1301, sig, 1) + name + b"\0"
    while (len(body) + 2) % 4:
        body += b"\xf1"
    return struct.pack("<I", 4) + struct.pack("<H", len(body)) + body


def cv_plain():
    body = struct.pack("<HIHH", 0x1002, 0x1000, 0, 0)
    return struct.pack("<I", 4) + struct.pack("<H", len(body)) + body


def sections_blob(secs, hdr, first_raw):
    heads, raw, pos = b"", b"", first_raw
    for name, data in secs:
        heads += struct.pack("<8sIIIIIIHHI", name, 0, 0, len(data), pos if data else 0, 0, 0, 0, 0, 0x40000040)
        raw += data
        pos += len(data)
    return heads, raw


def mkobj(tds=0x5f5e1000, secs=((b".text", b"\xc3"),), mach=0x8664):
    heads, raw = sections_blob(secs, 20, 20 + 40 * len(secs))
    pst = 20 + len(heads) + len(raw)
    sym = struct.pack("<8sIhHBB", b"_main", 0, 1, 0x20, 2, 0) + struct.pack("<I", 4)
    return struct.pack("<HHIIIHH", mach, len(secs), tds, pst, 1, 0, 0) + heads + raw + sym


BIG_CLSID = bytes.fromhex("c7a1bad1eebaa94baf20faf66aa4dcb8")


def mkbig(tds=0x5f5e1000, secs=((b".text", b"\xc3"),)):
    heads, raw = sections_blob(secs, 56, 56 + 40 * len(secs))
    pst = 56 + len(heads) + len(raw)
    sym = struct.pack("<8sIiHBB", b"_main", 0, 1, 0x20, 2, 0) + struct.pack("<I", 4)
    hdr = struct.pack("<HHHHI", 0, 0xFFFF, 2, 0x8664, tds) + BIG_CLSID + struct.pack("<IIIIIII", 0, 0, 0, 0, len(secs), pst, 1)
    return hdr + heads + raw + sym


def mkimp(tds=0x5f5e1000):
    tail = b"_sym\0kernel32.dll\0"
    return struct.pack("<HHHHIIHH", 0, 0xFFFF, 0, 0x14c, tds, len(tail), 7, 0) + tail


def member(name, data, date=b"1600000000  ", uid=b"1000  ", gid=b"1000  ", mode=b"100666  "):
    h = name.ljust(16) + date + uid + gid + mode + str(len(data)).encode().ljust(10) + b"`\n"
    assert len(h) == 60
    return h + data + (b"\n" if len(data) & 1 else b"")


def mkar(objs):
    return (b"!<arch>\n" + member(b"/", b"\0\0\0\0") + member(b"/", b"\0\0\0\0\0\0\0\0")
            + member(b"//", b"long_member_name.obj/\n\0") + b"".join(member(n, d) for n, d in objs))




def only_fields_change(data):
    rc, out, se = norm(data)
    if rc != 0:
        return False, "rc=%d %s" % (rc, se)
    allowed = set()
    for off, w, _, _ in field_table(data):
        allowed.update(range(off, off + w))
    d = diff_offsets(data, out)
    return (bool(d) and set(d) <= allowed), "changed %s allowed %s" % (d[:8], sorted(allowed)[:8])


def idem(data):
    rc, once, _ = norm(data)
    rc2, twice, _ = norm(once)
    return rc == 0 and rc2 == 0 and once == twice, ""


SAMPLES = {"obj": mkobj(), "bigobj": mkbig(), "import": mkimp(),
           "archive": mkar([(b"a.obj/", mkobj(1)), (b"/0", mkbig(2)), (b"i.obj/", mkimp(3))])}
for _k, _v in SAMPLES.items():
    case("only-fields-change", lambda v=_v: only_fields_change(v), key=_k)
    case("idempotent", lambda v=_v: idem(v), key=_k)


def t_obj_value():
    rc, out, _ = norm(mkobj(0x12345678))
    return rc == 0 and struct.unpack_from("<I", out, 4)[0] == 0 and out[:4] == mkobj()[:4] and out[8:] == mkobj()[8:], ""


def t_big_import_offsets():
    return [r[0] for r in field_table(mkbig())] == [8] and [r[0] for r in field_table(mkimp())] == [8], ""


def t_ar_header_bytes():
    rc, out, _ = norm(SAMPLES["archive"])
    pos, n = 8, 0
    while pos < len(out):
        h = out[pos:pos + 60]
        if h[16:48] != DATE0 + b"0     " + b"0     " + b"644     ":
            return False, h[16:48]
        sz = int(h[48:58])
        pos += 60 + sz + (sz & 1)
        n += 1
    return n == 6, n


def t_ar_matches_gnu():
    src = put(b"x", "m.txt")
    lib = src + ".a"
    subprocess.run(["ar", "rcD", lib, src], check=True)
    return rd(lib)[8 + 16:8 + 48] == DATE0 + b"0     " + b"0     " + b"644     ", rd(lib)[24:56]


def t_ar_members_kept():
    rc, out, _ = norm(SAMPLES["archive"])
    a, b = SAMPLES["archive"], out
    return rc == 0 and a[8 + 60:8 + 60 + 4] == b[8 + 60:8 + 60 + 4] and b"long_member_name.obj/\n" in b and len(a) == len(b), ""


def t_check():
    raw = put(SAMPLES["obj"])
    rc, out, se = sh("check", raw)
    ok1 = rc == 2 and "TimeDateStamp" in (out + se) and raw in (out + se) and "0x5f5e1000" in (out + se)
    n = raw + ".n"
    sh("normalize", raw, "-o", n)
    rc2, _, _ = sh("check", n)
    ar = put(SAMPLES["archive"])
    rc3, _, _ = sh("check", ar)
    an = ar + ".n"
    sh("normalize", ar, "-o", an)
    rc4, _, _ = sh("check", an, n)
    return ok1 and rc2 == 0 and rc3 == 2 and rc4 == 0, (rc, rc2, rc3, rc4)


def t_rejects():
    cases = {"empty": b"", "text": b"hello world, not a COFF object\n" * 4, "pe": b"MZ" + b"\0" * 200,
             "thin": b"!<thin>\n" + b"\0" * 80, "unknown-machine": mkobj(mach=0x1234),
             "anon-v1": struct.pack("<HHHHI", 0, 0xFFFF, 1, 0x14c, 0) + BIG_CLSID + b"\0" * 40}
    bad = []
    for k, v in cases.items():
        src = put(v)
        rc, _, _ = sh("normalize", src, "-o", src + ".o")
        rc2, _, _ = sh("check", src)
        rc3, _, _ = sh("fields", src)
        if rc != 1 or rc2 != 1 or rc3 != 1 or os.path.exists(src + ".o"):
            bad.append(k)
    return not bad, bad


def t_truncated():
    bad = []
    for k in ("obj", "bigobj", "import", "archive"):
        full = SAMPLES[k]
        for cut in (1, 7, 19, 25, 60, len(full) - 1):
            if cut >= len(full):
                continue
            src = put(full[:cut])
            if sh("normalize", src, "-o", src + ".o")[0] != 1:
                bad.append((k, cut))
    corrupt = mkar([(b"a.obj/", mkobj())])
    corrupt = corrupt[:8 + 58] + b"XX" + corrupt[8 + 60:]
    src = put(corrupt)
    if sh("check", src)[0] != 1:
        bad.append("fmag")
    sz = bytearray(mkar([(b"a.obj/", mkobj())]))
    sz[8 + 48:8 + 58] = b"99999     "
    if sh("check", put(bytes(sz)))[0] != 1:
        bad.append("size")
    return not bad, bad


def t_not_in_place():
    src = put(mkobj())
    before = rd(src)
    rc, _, _ = sh("normalize", src, "-o", src)
    return rc == 1 and rd(src) == before, rc


def t_typeserver():
    bad = []
    for kind, blob in (("TS2", cv_ts2()), ("TS", cv_ts1())):
        data = mkobj(secs=((b".text", b"\xc3"), (b".debug$T", blob)))
        src = put(data)
        rc, _, se = sh("normalize", src, "-o", src + ".o")
        if rc != 1 or "object references a PDB (compile with /Z7 or without debug info)" not in se or os.path.exists(src + ".o"):
            bad.append(kind + ":noflag")
        rc, _, se = sh("normalize", src, "-o", src + ".o", "--allow-typeserver")
        out = rd(src + ".o") if rc == 0 else b""
        if rc != 0 or "LF_TYPESERVER" not in se or blob not in out or len(out) != len(data):
            bad.append(kind + ":flag")
        rc, so, _ = sh("fields", src)
        if rc != 0 or "LF_TYPESERVER" not in so:
            bad.append(kind + ":fields")
    arch = mkar([(b"a.obj/", mkobj(secs=((b".debug$T", cv_ts2()),)))])
    src = put(arch)
    if sh("normalize", src, "-o", src + ".o")[0] != 1 or sh("normalize", src, "-o", src + ".o", "--allow-typeserver")[0] != 0:
        bad.append("archive")
    return not bad, bad


def t_debug_ok_and_corrupt():
    bad = []
    ok = mkobj(secs=((b".text", b"\xc3"), (b".debug$T", cv_plain()), (b".debug$S", struct.pack("<I", 4))))
    if norm(ok)[0] != 0:
        bad.append("plain")
    broken = cv_plain()[:-3]
    if norm(mkobj(secs=((b".debug$T", broken),)))[0] != 1:
        bad.append("trunc-record")
    if norm(mkobj(secs=((b".debug$T", struct.pack("<I", 9) + b"\0" * 8),)))[0] != 1:
        bad.append("bad-sig")
    if norm(mkobj(secs=((b".debug$S", struct.pack("<III", 4, 0xF1, 0x1000)),)))[0] != 1:
        bad.append("bad-subsection")
    return not bad, bad


def real_compiler():
    """The declared COFF-capable compiler (verify/env.json programs: clang); its absence is a failure, never a skip."""
    cc, extra = "clang", ["--target=x86_64-pc-windows-msvc", "-mincremental-linker-compatible"]
    src = put(b"int f(int x){return x+1;}\n", "q.c")
    r = subprocess.run([cc] + extra + ["-c", src, "-o", src + ".t.o"], capture_output=True)
    if r.returncode:
        raise RuntimeError("declared compiler %s cannot produce COFF: %s" % (cc, r.stderr[-200:]))
    return cc, extra


def t_real():
    cc, extra = real_compiler()
    src = put(b"int f(int x){return x+1;}\n", "q.c")
    outs, libs = [], []
    for i, epoch in enumerate(("1000000000", "1700000000")):
        o = src + ".%d.obj" % i
        env = dict(os.environ, SOURCE_DATE_EPOCH=epoch)
        subprocess.run([cc] + extra + ["-c", src, "-o", o], check=True, env=env)
        os.makedirs(src + ".d%d" % i)
        same = os.path.join(src + ".d%d" % i, "q.obj")
        shutil.copy(o, same)
        lib = src + ".%d.lib" % i
        subprocess.run(["ar", "rc", os.path.abspath(lib), "q.obj"], check=True, cwd=os.path.dirname(same))
        outs.append(o)
        libs.append(lib)
        time.sleep(1.2)
    res = []
    for a, b in ((outs[0], outs[1]), (libs[0], libs[1])):
        na, nb = a + ".n", b + ".n"
        r1 = sh("normalize", a, "-o", na)
        r2 = sh("normalize", b, "-o", nb)
        if r1[0] != 0 or r2[0] != 0:
            return False, (r1[2], r2[2])
        res.append(rd(na) == rd(nb) and sh("check", na)[0] == 0)
    differ = rd(outs[0]) != rd(outs[1])
    print("NOTE real compiler %s; raw objects differ before normalization: %s" % (cc, differ))
    return all(res), res


case("obj-value-and-bytes", t_obj_value)
case("bigobj-import-offsets", t_big_import_offsets)
case("archive-header-bytes", t_ar_header_bytes)
case("archive-matches-gnu-ar-D", t_ar_matches_gnu)
case("archive-members-kept", t_ar_members_kept)
case("check-verdicts", t_check)
case("unknown-and-PE-rejected", t_rejects)
case("truncated-corrupt", t_truncated)
case("not-in-place", t_not_in_place)
case("typeserver", t_typeserver)
case("debug-sections", t_debug_ok_and_corrupt)
case("real-object-and-archive", t_real)

shutil.rmtree(ROOT, ignore_errors=True)
print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
