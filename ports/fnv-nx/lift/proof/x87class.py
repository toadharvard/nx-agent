#!/usr/bin/env python3
"""The instruction class of an x87 shape key, read from the ENCODING (never from mnemonic text): the legacy prefixes are skipped, the opcode byte is D8..DF,
and the class is (opcode, form, reg) with form 'mem' (ModRM.mod != 3) or 'reg' (mod == 3) and reg = ModRM.reg (SDM Vol 2 Appendix A.2.1 and Tables A-6..A-9 index the x87
escape opcodes by exactly these fields).  For mod == 3 the opcodes whose reg row holds several instructions (D9 /4../7, DA /5, DB /4, DE /3, DF /4) are refined by
ModRM.rm (the same tables, the "ModR/M byte" columns), so a class is one instruction of the SDM opcode map and a builder per class is a builder per instruction page.
    python3 -I x87class.py KEYS.tsv...      rows `shape_key<TAB>count` (the pack's shapes.tsv layout); prints the keys per class, and the Form.build outcome counts
No argument other than files is read; the Form build is proof/forms.build on the decoder of proof/shape.py (the one decoder)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
from proof import shape  # noqa: E402

REFINE_RM = {(0xd9, 4), (0xd9, 5), (0xd9, 6), (0xd9, 7), (0xda, 5), (0xdb, 4), (0xde, 3), (0xdf, 4)}      # mod == 3 rows of the opcode map that hold several instructions (SDM Vol 2 A.2.1 Tables A-7..A-9)


def encoding(raw):
    """-> (opcode byte, ModRM byte) of the x87 instruction RAW (its prefixes skipped), or None when it is not an escape D8..DF"""
    i = 0
    while i < len(raw) and raw[i] in shape.PFX:
        i += 1
    if i + 1 >= len(raw) or not 0xd8 <= raw[i] <= 0xdf:
        return None
    return raw[i], raw[i + 1]


def x87_class(raw):
    """-> (opcode, 'mem' | 'reg', reg field, rm field or None)"""
    e = encoding(raw)
    if e is None:      # the family holds the non-escape x87 opcodes (FWAIT 9B): class = the opcode byte alone
        i = 0
        while i < len(raw) and raw[i] in shape.PFX:
            i += 1
        return raw[i], "op", None, None
    op, m = e
    mod, reg, rm = m >> 6, (m >> 3) & 7, m & 7
    if mod != 3:
        return op, "mem", reg, None
    return op, "reg", reg, rm if (op, reg) in REFINE_RM else None


def main(paths):
    from collections import Counter, defaultdict
    sys.path.insert(0, os.path.join(HERE, "tests"))
    import declared_cpufeat
    import declared_x86sys
    from fnvlift import inputs
    cpufeat, why = declared_cpufeat.path()
    if not cpufeat:
        raise SystemExit(why)
    declared_x86sys.configure()
    inputs.configure(cpufeat=cpufeat)
    from proof import forms
    dec = shape.decoder()
    cls, outcome, cres, proved, seen = defaultdict(set), Counter(), {}, {}, set()
    for p in paths:
        for ln in open(p):
            if ln[0] == "#" or not ln.strip():
                continue
            k = ln.split("\t")[0]
            if k in seen:      # the universe is the DISTINCT keys over the files
                continue
            seen.add(k)
            try:
                ins = next(dec.disasm(shape.rep_bytes(k), 0))
            except Exception:
                continue
            if shape.family(ins) != "x87":
                continue
            c = x87_class(bytes(ins.bytes))
            cls[c].add(k)
            try:
                F = forms.build(ins, 0)
                outcome["ok"] += 1
                if getattr(F, "x87", None):
                    kk = F.x87[:2]
                    if kk not in proved:
                        from proof import prove
                        proved[kk] = prove.prove_one(None, F, None, None, None)
                    if cres.get(c, "proved") == "proved":      # one result per class: proved only when every register index seen is
                        cres[c] = "proved" if proved[kk][0] == "proved" else "uncertified: %s %s" % (proved[kk][0], proved[kk][1])
            except KeyError as e:
                outcome["KeyError"] += 1
                cres[c] = "uncertified: %s" % (e.__cause__ or "no Form")
            except Exception as e:
                outcome[type(e).__name__] += 1
    for c in sorted(cls):
        print("%02x %s %s%s\t%d keys\t%s" % (c[0], c[1], "" if c[2] is None else "/%d" % c[2], "" if c[3] is None else " rm%d" % c[3], len(cls[c]), cres.get(c, "")))
    print("classes %d, keys %d, build outcome %s" % (len(cls), sum(len(v) for v in cls.values()), dict(outcome)))


if __name__ == "__main__":
    main(sys.argv[1:])
