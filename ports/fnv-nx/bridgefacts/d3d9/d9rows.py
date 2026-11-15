#!/usr/bin/env python3
"""d9rows.py OUT.tsv [SPEC.json]: the bridge rows of our d3d9.dll, from the interface table d3d9_spec.json (nothing per method): D9_ROWS of the tool root bridgefacts.
One row per spec method (dll `!<Interface>`, name, ordinal = vtable slot, conv stdcall, ret, args = x86 stack slots with `this` first, native_symbol `nxb_<Interface>_<Method>`,
bind sym) and one per documented export with a signature (dll d3d9.dll, native_symbol `nxb_<Export>`), the columns of the platform's bridge.tsv.
The slot and return rules below are the ones of the thunks of render/d3d9/gen/gen_bridge.py, which imports them from here (one source):
  value / enum / flags / ohandle by value   one slot `i` (size must be 4);   float by value   one slot `f`
  anything by reference, struct, buffer, locked   one slot `i` (a guest address; guest and native code share the address space)
  ret: void `v`, float `f` (x87 ST0 on the guest side), everything else one 32-bit `i` (HRESULT, counts, enums, opaque handles).
An item this file has no rule for stops the generator with exit 2."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))


def die(msg): sys.stderr.write("d9rows: " + msg + "\n"); sys.exit(2)


def slot_of(what, p):
    k = p["kind"]
    if p["byref"] or k in ("struct", "buffer", "locked"):
        if k == "struct" and not p["byref"]: die(f"{what}: struct {p['name']} by value has no rule")
        return "i"
    if k == "float": return "f"
    if k in ("value", "enum", "flags", "ohandle", "handle"):
        if p.get("size", 4) != 4: die(f"{what}: {p['name']} size {p.get('size')} has no rule")
        return "i"
    die(f"{what}: kind {k} has no rule")


def ret_of(what, m):
    rk = m.get("ret_kind")
    if rk == "void": return "v"
    if rk == "float": return "f"
    if rk in ("hresult", "value", "enum", "ohandle"):
        if m.get("ret_size", 4) != 4: die(f"{what}: return size has no rule")
        return "i"
    if rk == "handle": return "i"
    die(f"{what}: return kind {rk} has no rule")


def rows(spec):
    out, by = [], {}
    for m in spec["methods"]: by.setdefault(m["iface"], []).append(m)
    for i, info in spec["interfaces"].items():
        ms = sorted(by.get(i, []), key=lambda m: m["slot"])
        if [m["slot"] for m in ms] != list(range(info["slots"])): die(f"{i}: methods do not cover slots 0..{info['slots'] - 1}")
        for m in ms:
            what = f"{i}::{m['name']}"
            out.append((f"!{i}", m["name"], str(m["slot"]), "stdcall", ret_of(what, m), "i" + "".join(slot_of(what, p) for p in m["params"]), f"nxb_{i}_{m['name']}", "sym"))
    for e in spec["exports"]:
        if e.get("undocumented"): continue
        what = e["name"]
        out.append(("d3d9.dll", what, "", "stdcall", ret_of(what, e), "".join(slot_of(what, p) for p in e["params"]), f"nxb_{what}", "sym"))
    return out


if __name__ == "__main__":
    spec = json.load(open(sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "d3d9_spec.json")))
    open(sys.argv[1], "w").write("".join("\t".join(r) + "\n" for r in rows(spec)))
