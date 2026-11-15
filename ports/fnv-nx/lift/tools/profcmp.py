#!/usr/bin/env python3
"""Compare two profiling variants of the pack over the same units: CC time, the scheduling passes, .text bytes. Profiling only.
  profcmp.py STORE KEYS.json SEL.json VARIANT_A VARIANT_B   (KEYS.json: {variant: {unit: cc stage key}}, read from STORE/stage-docs)"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import cas, profrep

def text_bytes(store, key):
    res = json.load(open("%s/keys/%s/result.json" % (store, key)))
    if res["rc"]:
        return None
    ent = cas.manifest(res["outputs"]["/out"], store)["entries"]["unit.o"]
    out = subprocess.run(["aarch64-linux-gnu-size", "-A", cas._obj(store, ent)], capture_output=True, text=True).stdout
    return sum(int(l.split()[1]) for l in out.splitlines() if l.startswith(".text"))   # .text and .text.* sections

def row(store, key):
    res = json.load(open("%s/keys/%s/result.json" % (store, key)))
    rows = profrep.parse(profrep.stage_stderr(store, key))
    t = rows.get("TOTAL") or {}
    return {"rc": res["rc"], "usr": t.get("usr"), "wall": t.get("wall"), "sched1": rows.get("scheduling", {}).get("usr", 0.0),
            "sched2": rows.get("scheduling 2", {}).get("usr", 0.0), "text": text_bytes(store, key)}

if __name__ == "__main__":
    store, keys, sel, A, B = sys.argv[1:6]
    keys, sel = json.load(open(keys)), json.load(open(sel))
    tab = {}
    for u in sorted(keys[A].keys() & keys[B].keys(), key=lambda u: -sel["all"][u]["max_insns"]):
        tab[u] = {A: row(store, keys[A][u]), B: row(store, keys[B][u]), **sel["all"][u]}
    json.dump(tab, open("cmp.json", "w"), indent=1)
    print("unit max_insns | %s usr | %s usr | sched2 share | .text %s | .text %s | text ratio" % (A, B, A, B))
    ta = tb = xa = xb = 0
    for u, t in tab.items():
        a, b = t[A], t[B]
        if a["rc"] or b["rc"] or not a["usr"] or not b["usr"]:
            print(u, t["max_insns"], "rc", a["rc"], b["rc"]); continue
        ta += a["usr"]; tb += b["usr"]; xa += a["text"]; xb += b["text"]
        print("%s %6d | %8.2f | %8.2f | %.3f | %d | %d | %.4f" % (u, t["max_insns"], a["usr"], b["usr"], b["sched2"] / b["usr"], a["text"], b["text"], b["text"] / a["text"]))
    print("TOTAL usr %.1f -> %.1f (x%.3f); .text %d -> %d (x%.4f)" % (ta, tb, tb / ta, xa, xb, xb / xa))
