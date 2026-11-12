#!/usr/bin/env python3
"""Tests for verify/gate_tlsmodel.py: the relocation families, the readelf parser and the judgement. Pure: no build."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import gate_tlsmodel as G  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


P = "R_AARCH64_P32_"
for n, fam in (("TLSGD_ADR_PAGE21", G.DYNAMIC), ("TLSGD_ADD_LO12_NC", G.DYNAMIC), ("TLSLD_ADR_PAGE21", G.DYNAMIC), ("TLSDESC_ADR_PAGE21", G.DYNAMIC),
               ("TLSDESC_CALL", G.DYNAMIC), ("TLS_DTPMOD", G.DYNAMIC), ("TLS_TPREL32", G.DYNAMIC), ("TLSIE_ADR_GOTTPREL_PAGE21", G.IE),
               ("TLSIE_LD32_GOTTPREL_LO12_NC", G.IE), ("TLSLE_ADD_TPREL_HI12", G.LE), ("TLSLE_ADD_TPREL_LO12_NC", G.LE),
               ("TLSLE_MOVW_TPREL_G1", G.LE), ("TLS_FUTURE_KIND", G.OTHER)):
    check("%s is %s" % (n, fam), G.family(P + n) == fam, G.family(P + n))
check("LP64 names are classified alike", G.family("R_AARCH64_TLSIE_ADR_GOTTPREL_PAGE21") == G.IE and G.family("R_AARCH64_TLSGD_ADD_LO12_NC") == G.DYNAMIC, "")
check("a link-time TPREL name outside the families is unclassified", G.family("R_AARCH64_P32_FOO_TPREL_X") == G.OTHER, "")
for n in ("R_AARCH64_P32_ABS32", "R_AARCH64_P32_CALL26", "R_AARCH64_P32_ADR_PREL_PG_HI21", "R_AARCH64_P32_JUMP26"):
    check("%s is not a TLS relocation" % n, G.family(n) is None, G.family(n))

TXT = """
Relocation section '.rela.text' at offset 0x1f0 contains 3 entries:
 Offset     Info    Type                Sym. Value  Symbol's Name + Addend
00000000  00006e6a R_AARCH64_P32_TLSLE_ADD_TPREL_HI12 00000000   tv + 0
00000004  00006e6b R_AARCH64_P32_TLSIE_LD32_GOTTPREL_LO12_NC 00000000   host_tls + 0
00000008  0000030f R_AARCH64_P32_TLSLE_ADD_TPREL_LO12_NC 00000000   .tbss + 4
0000000c  00000102 R_AARCH64_P32_ABS32 00000000   .data + 8
"""
rl = G.parse_readelf(TXT)
check("the parser reads section, offset, type and symbol (a section symbol keeps its dot)", rl == [
    (".rela.text", 0, P + "TLSLE_ADD_TPREL_HI12", "tv"), (".rela.text", 4, P + "TLSIE_LD32_GOTTPREL_LO12_NC", "host_tls"),
    (".rela.text", 8, P + "TLSLE_ADD_TPREL_LO12_NC", ".tbss"), (".rela.text", 12, P + "ABS32", ".data")], rl)


def j(objs, linked=None):
    its, fs, cnt = G.judge({"g": objs}, linked or {})
    return its, {(o, k) for o, n, k, d in fs}, cnt, fs


def r(rt, sym, off=0):
    return (".rela.text", off, P + rt, sym)


OK = {"a.o": ({"tv"}, [r("TLSLE_ADD_TPREL_HI12", "tv"), r("TLSIE_LD32_GOTTPREL_LO12_NC", "host_tls", 4)]), "b.o": (set(), [r("ABS32", "x")])}
its, k, cnt, fs = j(OK)
check("LE of an own symbol and IE of a host symbol: green, objects and relocations are items", not k and len(its) == 4 and cnt == {G.LE: 1, G.IE: 1}, (its, k, cnt))
its, k, cnt, fs = j({"a.o": (set(), [r("TLSLE_ADD_TPREL_HI12", "host_tls")])})
check("LE of a symbol the group does not define: le-outside, names object symbol and relocation", ("a.o", "le-outside") in k and
      "host_tls" in fs[0][3] and "TLSLE_ADD_TPREL_HI12" in fs[0][3], fs)
its, k, cnt, fs = j({"a.o": (set(), [r("TLSLE_ADD_TPREL_HI12", "x")]), "b.o": ({"x"}, [])})
check("LE of a symbol another object of the group defines is allowed", not k, k)
its, k, cnt, fs = j({"a.o": ({"t"}, [r("TLSGD_ADR_PAGE21", "t")])})
check("general-dynamic TLS is red", ("a.o", "dynamic-tls") in k, k)
its, k, cnt, fs = j({"a.o": ({"t"}, [r("TLSDESC_CALL", "t")])})
check("TLS descriptors are red", ("a.o", "dynamic-tls") in k, k)
its, k, cnt, fs = j({"a.o": (set(), [r("TLS_FUTURE_KIND", "t")])})
check("an unclassified TLS relocation is red", ("a.o", "unclassified") in k, k)
its, k, cnt, fs = j({"a.o": ({"t"}, [r("TLSIE_ADR_GOTTPREL_PAGE21", "t")]), "b.o": (set(), [r("TLSLE_ADD_TPREL_HI12", "t")])})
check("one symbol with initial-exec and local-exec references: mixed-model on both", {("a.o", "mixed-model"), ("b.o", "mixed-model")} <= k, k)
its, k, cnt, fs = j({"a.o": ({"t"}, [r("TLSIE_ADR_GOTTPREL_PAGE21", "t"), r("TLSIE_LD32_GOTTPREL_LO12_NC", "t", 4)])})
check("one symbol, one model: green", not k, k)
its, k, cnt, fs = j({"a.o": (set(), [])}, {"x.elf": [(".rela.dyn", 0, P + "TLS_TPREL32", "tv")]})
check("a TLS relocation left in a linked output is red", ("x.elf", "dynamic-tls") in k, k)
its, k, cnt, fs = j({"a.o": (set(), [])}, {"x.elf": [(".rela.dyn", 0, P + "RELATIVE", "")]})
check("a linked output with no TLS relocation: its item is certified", not k and ("x.elf", "<elf>") in its, (its, k))
its, fs, cnt = G.judge({}, {})
check("no guest root: no item", its == [] and fs == [], its)
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
