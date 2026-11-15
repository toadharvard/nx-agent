#!/usr/bin/env python3
"""Per-unit compile-time table from a profiling build (recipe.py build --root lift/pack@prof --units F --prof-out P).
  profrep.py STORE PROF.json SEL.json OUT_DIR   (SEL.json: profsel.py output)
Reads the CC stage's stderr (-ftime-report -ftime-report-details) from the STORE and the PP/CC seconds of P. Profiling only."""
import json, os, re, sys, glob
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import cas

ROW = re.compile(r"^\s*(.+?)\s*:\s*([\d.]+)\s*\(\s*\d+%\)\s*([\d.]+)\s*\(\s*\d+%\)\s*([\d.]+)\s*\(\s*\d+%\)\s+(\d+)\s*(\w?)\s*\(")

def stage_stderr(store, key):
    res = json.load(open(os.path.join(store, "keys", key, "result.json")))
    return open(cas._obj(store, cas.manifest(res["stderr"], store)), errors="replace").read()

TOT = re.compile(r"^\s*TOTAL\s*:\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)")

def parse(text):
    """-> {row name: {usr, sys, wall}} for top-level rows; sub-rows (` `-`/`|`) are summed by name under "sub:<name>"."""
    rows, sub = {}, {}
    for l in text.splitlines():
        t = TOT.match(l)
        if t:
            rows["TOTAL"] = {"usr": float(t.group(1)), "sys": float(t.group(2)), "wall": float(t.group(3))}
            continue
        m = ROW.match(l)
        if not m:
            continue
        name = m.group(1).strip()
        v = {"usr": float(m.group(2)), "sys": float(m.group(3)), "wall": float(m.group(4))}
        if name[0] in "`|":
            n = name.lstrip("`-| ").strip()
            d = sub.setdefault("sub:" + n, {"usr": 0.0, "sys": 0.0, "wall": 0.0})
            for k in d:
                d[k] += v[k]
        else:
            rows[name] = v
    rows.update(sub)
    return rows

if __name__ == "__main__":
    store, prof, sel, out = sys.argv[1:5]
    prof, sel = json.load(open(prof)), json.load(open(sel))
    os.makedirs(out, exist_ok=True)
    table = {}
    for u, p in sorted(prof.items()):
        if not p.get("cc_key"):
            continue
        txt = stage_stderr(store, p["cc_key"])
        open(os.path.join(out, u.replace("/", "_") + ".timereport.txt"), "w").write(txt)
        rows = parse(txt)
        tot = rows.get("TOTAL", {})
        passes = {k: v for k, v in rows.items() if k != "TOTAL" and not k.startswith(("phase ", "sub:", "callgraph "))}   # phase/callgraph rows are containers (inclusive of the passes below)
        top = sorted(passes.items(), key=lambda kv: -kv[1]["usr"])[:5]
        table[u] = dict(sel["all"][u], pp_wall=p["pp_seconds"], cc_wall=p["cc_seconds"], ii_bytes=p["ii_bytes"], files=p["files"], total=tot,
                        top5=[{"name": k, **v, "share": round(v["usr"] / tot["usr"], 3) if tot.get("usr") else None} for k, v in top],
                        phases={k: v for k, v in rows.items() if k.startswith("phase ")}, rows=rows)
    json.dump(table, open(os.path.join(out, "table.json"), "w"), indent=1)
    print(len(table), "units")
