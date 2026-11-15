#!/usr/bin/env python3
"""Static size of every unit of a lifted pack, and the profiling selection (profiling only, decides nothing).
Per unit: bytes, function count (definitions `... void L_xxxxxxxx(void)` followed by `{`), instruction count of the
largest function (one `L_xxxxxxxx: ;` label per lifted x86 instruction).
Rule: the K largest units by that count (ties: unit name) + the N units nearest the median (ties: unit name).
  profsel.py PACK_DIR [--top 20] [--median 10] -> JSON {"all": {unit: {...}}, "selected": [unit...], "median": m}"""
import json, os, re, sys

FN = re.compile(r'^extern "C"[^\n]*? (L_[0-9a-f]{8})\(void\)\s*$', re.M)
LAB = re.compile(r'^(L_[0-9a-f]{8}): ;$', re.M)

def measure(path):
    s = open(path, errors="replace").read()
    heads = [m.start() for m in re.finditer(r'^extern "C"[^\n]*\bvoid L_[0-9a-f]{8}\(void\)\s*$', s, re.M)]
    # definitions only (a declaration ends in ';' on the same line and is not matched by the `$` anchor)
    bounds = heads + [len(s)]
    sizes = [len(LAB.findall(s[a:b])) for a, b in zip(bounds, bounds[1:])]
    return {"bytes": len(s.encode()), "functions": len(heads), "max_insns": max(sizes or [0])}

def main(a):
    pack = a[0]
    opt = dict(zip(a[1::2], a[2::2]))
    top, nmed = int(opt.get("--top", 20)), int(opt.get("--median", 10))
    allu = {f"u/{f}": measure(os.path.join(pack, "u", f)) for f in sorted(os.listdir(os.path.join(pack, "u"))) if f.endswith(".cpp")}
    order = sorted(allu, key=lambda u: (-allu[u]["max_insns"], u))
    vals = sorted(v["max_insns"] for v in allu.values())
    med = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
    rest = [u for u in order if u not in order[:top]]
    near = sorted(rest, key=lambda u: (abs(allu[u]["max_insns"] - med), u))[:nmed]
    pct = lambda p: vals[min(len(vals) - 1, int(p * len(vals)))]
    print(json.dumps({"all": allu, "selected": sorted(order[:top] + near), "top": order[:top], "near_median": near, "median": med,
                      "p50": pct(.5), "p99": pct(.99), "max": vals[-1]}, indent=1))

if __name__ == "__main__":
    main(sys.argv[1:])
