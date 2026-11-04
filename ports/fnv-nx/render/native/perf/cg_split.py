#!/usr/bin/env python3
"""fnv-nx P6n: split a callgrind dump (one part between two marks) into the measured side and the excluded driver.

    python3 cg_split.py <callgrind file> [--top N]

Excluded ("driver") cost = the inclusive cost of every call edge from a measured function into a driver-internal
one. Driver-internal: the entry points - the pipe driver (softpipe_*, sp_*) and the host gate's present backend
(nx_gate_*, gate_*: on Switch this is nwindow queueing) - plus, to a fixed point, every function all of whose
callers are driver-internal (softpipe's draw module, tgsi_exec, tile caches). Functions both sides call (memcpy,
malloc, util_format_*) stay measured where Nine calls them and excluded where the driver does. Everything else - st/nine, cso_cache, u_upload_mgr, util, libc it calls -
is the measured side. Prints JSON: {"total", "driver", "measured", "top_self": [...], "driver_entries": [...]}.
"""
import json, re, sys

ENTRY = re.compile(r"^(softpipe_|sp_|nx_gate_|gate_)")


def parse(path):
    names, self_cost, edges = {}, {}, {}
    fn = cfn = None
    pending_call = False
    total = 0

    def name_of(tok):
        m = re.match(r"\((\d+)\)(?: (.*))?", tok)
        if not m:
            return tok
        if m.group(2) is not None:
            names[m.group(1)] = m.group(2)
        return names.get(m.group(1), tok)

    for line in open(path, errors="replace"):
        line = line.rstrip("\n")
        if not line:
            continue
        c = line[0]
        if c.isdigit() or c in "+-*":
            parts = line.split()
            cost = int(parts[1]) if len(parts) > 1 else 0
            if pending_call:
                edges[(fn, cfn)] = edges.get((fn, cfn), 0) + cost
                pending_call = False
            else:
                self_cost[fn] = self_cost.get(fn, 0) + cost
                total += cost
        elif line.startswith("fn="):
            fn = name_of(line[3:])
        elif line.startswith("cfn="):
            cfn = name_of(line[4:])
        elif line.startswith("calls="):
            pending_call = True
        elif line.startswith("summary:"):
            pass
    return total, self_cost, edges


def inside_set(edges):
    """driver-internal functions: the entry points plus every function all of whose callers are driver-internal"""
    callers = {}
    for (a, b) in edges:
        callers.setdefault(b, set()).add(a)
    inside = {f for f in callers if ENTRY.match(f or "")} | {a for (a, _) in edges if ENTRY.match(a or "")}
    changed = True
    while changed:
        changed = False
        for f, cs in callers.items():
            if f not in inside and cs and cs <= inside:
                inside.add(f); changed = True
    return inside


def split(path, top=15):
    total, self_cost, edges = parse(path)
    inside = inside_set(edges)
    driver, entries = 0, {}
    for (a, b), cost in edges.items():
        if b in inside and a not in inside:
            driver += cost
            entries[b] = entries.get(b, 0) + cost
    meas = {k: v for k, v in self_cost.items() if k not in inside}
    return {"total": total, "driver": driver, "measured": total - driver,
            "top_self": sorted(meas.items(), key=lambda kv: -kv[1])[:top],
            "driver_entries": sorted(entries.items(), key=lambda kv: -kv[1])[:top]}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 15
    print(json.dumps(split(sys.argv[1], top), indent=1))
