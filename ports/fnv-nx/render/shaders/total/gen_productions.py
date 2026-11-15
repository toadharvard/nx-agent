#!/usr/bin/env python3
"""gen_productions.py - the SM1-3 production table (PRODUCTIONS.md): one record per grammar element of every creatable model, read from sm_table.json only.
  python3 -I gen_productions.py TABLE OUT
No row is written here: every production is an element of a field of the table (opcodes[].models, models[].dst_regs / src_regs / dstmods / srcmods / regs[].rel /
swizzle, swizzle_forms, writemask, usages, sampler_types), with the table's own citation in `source`."""
import hashlib, json, sys

KINDS = ["op", "dstreg", "dstmod", "writemask", "srcreg", "reladdr", "srcmod", "swizzle", "dcl", "sampler"]

def render(table_path):
    raw = open(table_path, "rb").read(); T = json.loads(raw)
    P = []
    def add(m, kind, key, form, source):
        P.append(dict(id="%s:%s:%s" % (m, kind, key), model=m, stage=m[:2], kind=kind, key=key, form=form, source=source))
    ops = T["opcodes"]
    for m, M in sorted(T["models"].items()):
        rs = M["regs_source"]
        for oid, e in sorted(ops.items()):
            if m not in e["models"] or oid[:2] != m[:2]: continue
            form = {k: e[k] for k in ("opnum", "d", "s", "mk", "tex", "ctrl", "decl", "tm") if k in e}
            add(m, "op", oid, form, e["source"] + (" ; wine-9.0 union: %s" % ",".join(e["models_wine_added"]) if m in e.get("models_wine_added", []) else ""))
            if "s3" in e and m.endswith("3_0"):
                add(m, "op", oid + "/s3", dict(form, s=e["s3"], tm=e.get("tm3")), e["source"])
        for r in M["dst_regs"]: add(m, "dstreg", r, M["regs"].get(r, {}), rs)
        for r in M["src_regs"]: add(m, "srcreg", r, M["regs"].get(r, {}), rs)
        for x in M["dstmods"]: add(m, "dstmod", x, {}, "MS:instruction modifiers of " + m)
        for x in M["srcmods"]: add(m, "srcmod", x, {}, T["srcmod_source"] if isinstance(T.get("srcmod_source"), str) else json.dumps(T.get("srcmod_source"))[:200])
        for r, spec in sorted(M["regs"].items()):
            for via in spec.get("rel", []): add(m, "reladdr", "%s[%s]" % (r, via), {"reg": r, "via": via}, rs)
        for f in T["swizzle_forms"].get(M["swizzle"]) or ["any"]: add(m, "swizzle", f or "(none)", {"class": M["swizzle"]}, "MS swizzle rules, class " + M["swizzle"])
        wm = T["writemask"]
        cls = {"ps1": "ps1", "ps14": "ps14"}.get(M["swizzle"], "general")
        add(m, "writemask", cls, {"text": wm.get(cls if cls != "ps1" else "ps14", wm["general"])}, "MS write-mask rules (writemask." + cls + ")")
        if m[:2] == "vs":
            for r, forms in sorted(wm["vs_outputs"].items()):
                if r != "note" and r in M["dst_regs"]: add(m, "writemask", "out:" + r, {"forms": forms}, "MS vs output register masks")
        if any(oid.startswith(m[:2] + ":dcl") and m in e["models"] for oid, e in ops.items()):
            for u in T["usages"]: add(m, "dcl", u, {}, T["usage_source"] if isinstance(T.get("usage_source"), str) else "MS dcl usage pages")
        for s in T["sampler_types"].get(m, []): add(m, "sampler", s, {}, "MS dcl_samplertype pages")
    P.sort(key=lambda p: p["id"])
    assert len({p["id"] for p in P}) == len(P)
    return dict(format="fnvnx-sm-productions/1", table_sha256=hashlib.sha256(raw).hexdigest(), kinds=KINDS, productions=P)

if __name__ == "__main__":
    json.dump(render(sys.argv[1]), open(sys.argv[2], "w"), indent=1, sort_keys=True)
