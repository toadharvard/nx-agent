#!/usr/bin/env python3
# reads: ports/fnv-nx/verify
"""Tests for verify/gate_flaglive.py (judge) on toy pack directories. Plain script: exit 0 on pass."""
import hashlib
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import gate_flaglive as G  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


def pack(n=3, rows="00401000\t00401000\t3f\n"):
    d = tempfile.mkdtemp(prefix="fl-")
    open(d + "/entries.tsv", "w").write("# header\n" + "".join("%08x\tL_%08x\tcode\n" % (i, i) for i in range(n)) + "# module_table sha256 00\n")
    open(d + "/flagdrop.tsv", "w").write(rows)
    return d


def doc(d, **o):
    return dict({"format": "fnvnx-flaglive/1", "entries": 3, "differences": 0, "pack_list_sha256": hashlib.sha256(open(d + "/flagdrop.tsv", "rb").read()).hexdigest()}, **o)


d = pack()
check("a clean check over every function of the pack is certified", G.judge(doc(d), d) == (3, 3, []))
u, c, why = G.judge(doc(d, differences=1), d)
check("a difference is red", c == 0 and why)
check("a check over fewer functions than the pack has is red", G.judge(doc(d, entries=2), d)[1] == 0)
check("a check over another list than the pack's is red", G.judge(doc(d, pack_list_sha256="0" * 64), d)[1] == 0)
check("a malformed record is red", G.judge({}, d)[1] == 0)
check("a pack without its list is red", G.judge(doc(d), tempfile.mkdtemp())[1] == 0)
sys.exit(1 if fails else 0)
