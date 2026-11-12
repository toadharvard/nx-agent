#!/usr/bin/env python3
"""gen_bridge.py OUT [spec.json]: the guest bridge of our d3d9.dll, generated from the interface table (nothing per method).
  OUT/d9bridge_rows.tsv   rows for the platform's bridge.tsv (same columns): one per spec method (dll `!<Interface>`, name, ordinal = vtable slot,
                          conv stdcall, ret, args = x86 stack slots with `this` first, native_symbol `nxb_<Interface>_<Method>`, bind sym) and one per
                          export with a signature (dll d3d9.dll, native_symbol `nxb_<Export>`).
  OUT/d9bridge.gen.cpp    the thunks (extern "C", arguments = the stack slots in order), the key tables and nx_bridge_keytab (dev/face.h).
Rules (total over the spec's item kinds; an item this file has no rule for stops the generator with exit 2):
  value / enum / flags / ohandle by value   one slot `i` (size must be 4);   float by value   one slot `f`
  anything by reference, struct, buffer, locked   one slot `i` (a guest address; guest and native code share the address space)
  handle by value (in)      the guest passes a face: nx_unface
  handle by reference (out) the method stores our object; the thunk replaces it by its face (null stays null)
  buffer with canon `handle` (an array of interface pointers, in)   converted element by element into a native array of the same length
  buffer with canon `ohandle` (private data D3DSPD_IUNKNOWN)        the bytes are a GUEST object pointer: passed through unchanged. The calls our
                            object model makes on it (AddRef / Release) go through nxd3d_app_* (dev/app_unk_guest.cpp -> fnvnx_guest_invoke of L3a)
  ret: void `v`, float `f` (x87 ST0 on the guest side), everything else one 32-bit `i` (HRESULT, counts, enums, opaque handles)."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); SPEC = os.path.join(HERE, "..", "spec", "d3d9_spec.json")
sys.path.insert(0, HERE)
from gen_impl import cname  # noqa: E402
from gen_expr import Ctx  # noqa: E402
REFS = ("REFIID", "REFGUID")


def die(msg): sys.stderr.write("gen_bridge: " + msg + "\n"); sys.exit(2)


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


def thunk(spec, what, sym, m, callee, this_t):
    """one extern "C" function; callee = 'o->Name' or the export's native function"""
    P = m.get("params", []); decl, args, pre, post, allocd = [], [], [], [], []
    if this_t: decl.append("void* nxb_this"); pre.append(f"{this_t}* o = ({this_t}*)nx_unface_this(nxb_this);")
    for p in P:
        n, k, ct = p["name"], p["kind"], p["ctype"]
        slot_of(what, p)
        if ct in REFS: decl.append(f"const GUID* {n}"); args.append(f"*{n}"); continue
        if k == "handle" and not p["byref"]:
            if p["dir"] != "in": die(f"{what}: handle {n} by value with dir {p['dir']} has no rule")
            decl.append(f"void* {n}"); args.append(f"({ct})nx_unface({n})"); continue
        if k == "handle":
            if p["dir"] != "out": die(f"{what}: handle {n} by reference with dir {p['dir']} has no rule")
            decl.append(f"{ct} {n}"); args.append(n); post.append(f"if ({n} && *{n}) *(void**){n} = nx_face((IUnknown*)*(void**){n});"); continue
        if k == "buffer" and "canon" in p:
            as_ = p["canon"]["as"]
            if as_ == "handle":
                if p["dir"] != "in" or p.get("elem") != 4 or p["canon"].get("when") != "1": die(f"{what}: canon handle buffer {n} shape has no rule")
                ln = Ctx(m, spec, None, False, what).c(p["len"])
                et = ct.replace('**', '*').strip()
                decl.append(f"void** {n}"); pre.append(f"uint64_t nxb_n_{n} = {n} ? ({ln}) / 4 : 0; {et}* nxb_a_{n} = nullptr;")
                undo = "".join(f"nxd3d_free(nxb_a_{u}); " for u in allocd)   # allocation failure: what the earlier buffers of this thunk allocated goes back before the E_OUTOFMEMORY (Wine d3d9: allocation failure = E_OUTOFMEMORY)
                pre.append(f"if (nxb_n_{n}) {{ if (nxb_n_{n} > 0x3fffffffu || !(nxb_a_{n} = ({ct})nxd3d_alloc((size_t)nxb_n_{n} * sizeof(void*)))) {{ {undo}return E_OUTOFMEMORY; }} }}")
                allocd.append(n)
                pre.append(f"for (uint64_t i = 0; i < nxb_n_{n}; i++) nxb_a_{n}[i] = ({et})nx_unface({n}[i]);")
                post.append(f"nxd3d_free(nxb_a_{n});")
                if m.get("ret_kind") != "hresult": die(f"{what}: canon handle buffer in a method without HRESULT has no rule")
                args.append(f"nxb_a_{n}"); continue
            if as_ != "ohandle": die(f"{what}: canon {as_} of {n} has no rule")
        decl.append(f"{ct} {n}"); args.append(n)
    call = f"{callee}({', '.join(args)})"; ret = m["ret"]; L = [f"{ret} {sym}({', '.join(decl) or 'void'}) {{"] + ["    " + x for x in pre]
    if m.get("ret_kind") == "handle": L.append(f"    return ({ret})nx_face((IUnknown*){call});") if not post else die(f"{what}: handle return with out handles has no rule")
    elif ret == "void": L += [f"    {call};"] + ["    " + x for x in post]
    elif post: L += [f"    {ret} r = {call};"] + ["    " + x for x in post] + ["    return r;"]
    else: L.append(f"    return {call};")
    return "\n".join(L + ["}"])


def main():
    out = sys.argv[1]; spec = json.load(open(sys.argv[2] if len(sys.argv) > 2 else SPEC)); os.makedirs(out, exist_ok=True)
    rows, cpp, tabs = [], ['// generated by gen_bridge.py; do not edit', '#include <string.h>', '#include "d9impl.gen.h"', '#include "../dev/face.h"', "#ifndef E_OUTOFMEMORY\n#define E_OUTOFMEMORY ((HRESULT)0x8007000EL)\n#endif",
                           'extern "C" uint32_t fnvnx_import_key(const char* dll, const char* name, uint32_t ordinal);   // loader/include/fnvnx_importkey.h', 'extern "C" {'], []
    by = {}
    for m in spec["methods"]: by.setdefault(m["iface"], []).append(m)
    for i, info in spec["interfaces"].items():
        ms = sorted(by.get(i, []), key=lambda m: m["slot"])
        if [m["slot"] for m in ms] != list(range(info["slots"])): die(f"{i}: methods do not cover slots 0..{info['slots'] - 1}")
        for m in ms:
            what = f"{i}::{m['name']}"; sym = f"nxb_{i}_{m['name']}"
            rows.append((f"!{i}", m["name"], str(m["slot"]), "stdcall", ret_of(what, m), "i" + "".join(slot_of(what, p) for p in m["params"]), sym, "sym"))
            cpp.append(thunk(spec, what, sym, m, f"o->{m['name']}", i))
        tabs.append((i, cname(i), [m["name"] for m in ms]))
    for e in spec["exports"]:
        if e.get("undocumented"): continue
        what = e["name"]; sym = f"nxb_{what}"
        rows.append(("d3d9.dll", what, "", "stdcall", ret_of(what, e), "".join(slot_of(what, p) for p in e["params"]), sym, "sym"))
        cpp.append(f"{e['ret']} WINAPI {what}({', '.join(p['ctype'] + ' ' + p['name'] for p in e['params']) or 'void'});")
        cpp.append(thunk(spec, what, sym, e, what, None))
    cpp.append('}')
    cpp.append("struct NxKeyTab { const NxClassInfo* ci; const char* dll; const char* const* names; uint32_t n; uint32_t* keys; };")
    for i, c, names in tabs:
        cpp.append(f"static const char* const nxb_names_{c}[] = {{ " + ", ".join(f'"{n}"' for n in names) + " };")
        cpp.append(f"static uint32_t nxb_keys_{c}[{len(names)}];")
    cpp.append("static const NxKeyTab nxb_tabs[] = {\n" + ",\n".join(f'    {{&nx_ci_{c}, "!{i}", nxb_names_{c}, {len(n)}, nxb_keys_{c}}}' for i, c, n in tabs) + "\n};")
    cpp.append("""// Fills every key table from the build's import table (one value per (dll, name) everywhere: pointer equality as on Windows). Returns the number of
// rows the import table lacks; the bridge gate (rows of this generator == rows of bridge.tsv) makes that 0 in every build that links.
extern "C" uint32_t nx_bridge_init(void) {
    static uint32_t done, missing;
    if (done) return missing;
    for (const NxKeyTab& t : nxb_tabs) for (uint32_t s = 0; s < t.n; s++) { t.keys[s] = fnvnx_import_key(t.dll, t.names[s], s); if (!t.keys[s]) missing++; }
    done = 1; return missing;
}
uint32_t nx_bridge_keytab(const NxClassInfo* ci) {
    nx_bridge_init();
    for (const NxKeyTab& t : nxb_tabs) if (t.ci == ci) return (uint32_t)(uintptr_t)t.keys;
    return 0;
}""")
    open(os.path.join(out, "d9bridge.gen.cpp"), "w").write("\n".join(cpp) + "\n")
    open(os.path.join(out, "d9bridge_rows.tsv"), "w").write("".join("\t".join(r) + "\n" for r in rows))
    print(f"gen_bridge: {len(rows)} rows ({sum(1 for r in rows if r[0][0] == '!')} methods of {len(tabs)} interfaces, {sum(1 for r in rows if r[0][0] != '!')} exports)")


if __name__ == "__main__":
    main()
