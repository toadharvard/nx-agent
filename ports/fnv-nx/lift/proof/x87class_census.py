#!/usr/bin/env python3
"""The census of the x87 classes: the keys per class (x87class.x87_class) and the Form.build outcome counts.
    python3 -I x87class_census.py KEYS.tsv...      rows `shape_key<TAB>count` (the pack's shapes.tsv layout)
No argument other than files is read; the Form build is proof/forms.build on the decoder of proof/shape.py (the one decoder).  A tool of its own, apart from x87class.py, because it runs the prover
(proof/prove.py) and the Form builder imports x87class: the stage tools that import the builder do not carry the prover."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx)
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
from fnvnx.lift.proof import shape  # noqa: E402
from fnvnx.lift.proof.x87class import x87_class  # noqa: E402


def main(paths):
    from collections import Counter, defaultdict
    sys.path.insert(0, os.path.join(HERE, "tests"))
    import fnvnx.lift.proof.tests.declared_x86sys as declared_x86sys
    from fnvnx.lift.fnvlift import inputs
    declared_x86sys.configure()
    inputs.configure(cpumodel=os.path.join(HERE, "..", "..", "verify", "cpuid_model.json"))
    from fnvnx.lift.proof import forms
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
                        from fnvnx.lift.proof import prove
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
