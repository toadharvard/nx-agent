#!/usr/bin/env python3
"""Fit CC time of the profiled units (profrep.py's inputs). Profiling only.
  proffit.py STORE SEL.json PP.json OUT_DIR -> OUT_DIR/table.json, table.tsv, fit.txt
CC time = TOTAL usr of the CC stage's -ftime-report. Units whose CC stage failed (rc != 0) have no optimizer time: listed, left out of the fit.
Fit: log-log least squares t = a * x^k (k = 1 linear, k > 1 superlinear), x = the largest function's x86 instructions, and x = unit bytes;
also a two-variable fit log t = log a + k1 log(max_insns) + k2 log(bytes)."""
import json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import profrep

def fit(xs, ys):
    lx, ly = [math.log(x) for x in xs], [math.log(y) for y in ys]
    n = len(lx); mx, my = sum(lx) / n, sum(ly) / n
    k = sum((a - mx) * (b - my) for a, b in zip(lx, ly)) / sum((a - mx) ** 2 for a in lx)
    c = my - k * mx
    ss = sum((b - (c + k * a)) ** 2 for a, b in zip(lx, ly)); st = sum((b - my) ** 2 for b in ly)
    return k, math.exp(c), 1 - ss / st

def main(a):
    store, sel, pp, out = a
    sel, pp = json.load(open(sel)), json.load(open(pp))
    os.makedirs(out, exist_ok=True)
    table = {}
    for k in sorted(os.listdir(store + "/keys")):
        try:
            r = json.load(open("%s/keys/%s/result.json" % (store, k)))
        except (OSError, ValueError):
            continue
        st = r["stage"]
        if not (st.startswith("cc:u/") and st[3:] in sel["selected"]):
            continue
        u = st[3:]
        txt = profrep.stage_stderr(store, k)
        open(os.path.join(out, u.replace("/", "_") + ".timereport.txt"), "w").write(txt)
        rows = profrep.parse(txt)
        tot = rows.get("TOTAL")
        passes = {n: v for n, v in rows.items() if not n.startswith(("phase ", "sub:", "callgraph ", "TOTAL"))}
        top = sorted(passes.items(), key=lambda kv: -kv[1]["usr"])[:5]
        table[u] = dict(sel["all"][u], rc=r["rc"], pp_wall=pp.get(u, {}).get("pp_seconds"), ii_bytes=pp.get(u, {}).get("ii_bytes"),
                        total=tot, selected_as="top" if u in sel["top"] else "median",
                        top5=[{"name": n, **v, "share": round(v["usr"] / tot["usr"], 3) if tot else None} for n, v in top],
                        sched_usr=rows.get("scheduling", {}).get("usr", 0.0), plugin_usr=rows.get("sub:plugin execution", {}).get("usr"), plugin_wall=rows.get("sub:plugin execution", {}).get("wall"),
                        phases={n: v for n, v in rows.items() if n.startswith("phase ")})
    json.dump(table, open(out + "/table.json", "w"), indent=1)
    ok = {u: t for u, t in table.items() if t["rc"] == 0 and t["total"]}
    with open(out + "/table.tsv", "w") as f:
        f.write("unit\tset\trc\tbytes\tfuncs\tmax_insns\tpp_wall\tcc_usr\tcc_wall\ttop5 (usr s, share)\tplugin_usr\n")
        for u, t in sorted(table.items(), key=lambda kv: -kv[1]["max_insns"]):
            tt = t["total"] or {}
            f.write("%s\t%s\t%d\t%d\t%d\t%d\t%s\t%s\t%s\t%s\t%s\n" % (u, t["selected_as"], t["rc"], t["bytes"], t["functions"], t["max_insns"], t["pp_wall"], tt.get("usr"), tt.get("wall"),
                    "; ".join("%s %.2f (%.0f%%)" % (x["name"], x["usr"], 100 * (x["share"] or 0)) for x in t["top5"]), t["plugin_usr"]))
    xs = lambda key: [t[key] for t in ok.values()]
    ys = [t["total"]["usr"] for t in ok.values()]
    ss = [max(t["sched_usr"], 0.01) for t in ok.values()]
    lines = ["units with CC time: %d, failed in CC: %d (%s)" % (len(ok), len(table) - len(ok), ", ".join(u for u, t in table.items() if t["rc"]))]
    for nm, y in (("CC usr total", ys), ("scheduling usr", ss)):
        for key in ("max_insns", "bytes"):
            k, c, r2 = fit(xs(key), y)
            lines.append("%-14s ~ %.3g * %s^%.2f   (R2 %.3f)" % (nm, c, key, k, r2))
    # 2-variable
    import itertools
    L = [[1.0, math.log(t["max_insns"]), math.log(t["bytes"])] for t in ok.values()]; Y = [math.log(y) for y in ys]
    A = [[sum(r[i] * r[j] for r in L) for j in range(3)] for i in range(3)]; B = [sum(r[i] * y for r, y in zip(L, Y)) for i in range(3)]
    for i in range(3):   # gauss
        p = A[i][i]
        for j in range(i, 3): A[i][j] /= p
        B[i] /= p
        for k2 in range(3):
            if k2 != i:
                f2 = A[k2][i]
                for j in range(i, 3): A[k2][j] -= f2 * A[i][j]
                B[k2] -= f2 * B[i]
    lines.append("CC usr ~ %.3g * max_insns^%.2f * bytes^%.2f" % (math.exp(B[0]), B[1], B[2]))
    lines.append("points (max_insns, bytes, cc_usr, scheduling_usr):")
    for u, t in sorted(ok.items(), key=lambda kv: kv[1]["max_insns"]):
        lines.append("  %s %6d %8d %8.2f %8.2f" % (u, t["max_insns"], t["bytes"], t["total"]["usr"], t["sched_usr"]))
    open(out + "/fit.txt", "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))

if __name__ == "__main__":
    main(sys.argv[1:])
