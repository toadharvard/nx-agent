#!/usr/bin/env python3
"""productions_gate.py - handler coverage of the SM1-3 production table (PRODUCTIONS.md): productions minus handled = empty.
  python3 -I productions_gate.py --productions productions.json --mojoshader DIR --abi d3d9types.h --out productions_gate.json [--strict]
The handlers are read from the translator's own dispatch (the pinned MojoShader tree), nothing is listed here:
  op        the INSTRUCTION / INSTRUCTION_STATE list of mojoshader_internal.h (position = opcode token, shader-type mask) joined with the emit_GLSL_<op>
            definitions of mojoshader_profile_glsl.c; a RESERVED entry, a missing emitter or an emitter whose body only fails is unhandled
  regtype   the switch over REG_TYPE_ of the function the profile's get_varname field names, followed through its calls
  srcmod    the switch over `src_mod` (labels SRCMOD_*), dstmod the tests of `result_mod` against MOD_* in the destination builder
  swizzle / writemask / reladdr   the functions that read swizzle_x..w, the destination writemask, relative_regtype
  dcl / sampler   the profile's attribute / sampler emitter: switch labels MOJOSHADER_USAGE_* / TEXTURE_TYPE_*
A key is joined to its enumerator through the ABI header (d3d9types.h values) and the enumerator lists of the translator's headers (by value)."""
import argparse, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sm_xref

def rd(p): return open(p, encoding="utf8", errors="replace").read()
def strip_comments(s): return re.sub(r"//[^\n]*|/\*.*?\*/", "", s, flags=re.S)

def functions(src):
    """{name: body} of the function definitions (column-0 header, brace matched), comments stripped"""
    src = strip_comments(src); out = {}
    for m in re.finditer(r"^[A-Za-z_][\w \*]*?\b(\w+)\s*\([^;{}]*\)\s*\n\{", src, re.M):
        i = m.end() - 1; d = 0
        for j in range(i, len(src)):
            d += (src[j] == "{") - (src[j] == "}")
            if d == 0: break
        out[m.group(1)] = src[i:j + 1]
    return out

def enum_values(src, prefix):
    """{NAME: value} of `PREFIX_NAME = n` / sequential enumerators of the enum(s) whose enumerators start with PREFIX"""
    out = {}
    for m in re.finditer(r"enum\s*\w*\s*\{(.*?)\}", src, re.S):
        body = strip_comments(m.group(1) or "")
        if prefix not in body: continue
        v = -1
        for it in [x.strip() for x in body.split(",") if x.strip()]:
            k = re.match(r"(%s\w+)\s*(?:=\s*(.+))?$" % prefix, it)
            if not k: continue
            if k.group(2):
                try: v = int(k.group(2).strip(), 0)
                except ValueError: v = out.get(k.group(2).strip(), v + 1)
            else: v += 1
            out[k.group(1)] = v
    return out

def labels(body, prefix): return set(re.findall(r"case\s+(%s\w+)\s*:" % prefix, body))
def default_ok(body):
    m = re.search(r"default\s*:(.*?)(?:break\s*;|return\b|\}\s*//|\Z)", body, re.S)
    return bool(m) and "fail(" not in m.group(1) and "assert(0" not in m.group(1).replace(" ", "")

def main():
    ap = argparse.ArgumentParser()
    for a in ("productions", "mojoshader", "abi", "out"): ap.add_argument("--" + a, required=True)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    P = json.load(open(a.productions)); prods = P["productions"]
    mj = a.mojoshader
    internal = rd(os.path.join(mj, "mojoshader_internal.h")); mjh = rd(os.path.join(mj, "mojoshader.h"))
    glsl = rd(os.path.join(mj, "profiles", "mojoshader_profile_glsl.c")); common = rd(os.path.join(mj, "profiles", "mojoshader_profile_common.c"))
    fn = {}; [fn.update(functions(s)) for s in (common, glsl)]
    abi = rd(a.abi)
    # ---- op: instruction list ----
    tbl = internal[internal.index("#if MOJOSHADER_DO_INSTRUCTION_TABLE"):]; tbl = tbl[:tbl.index("#undef MOJOSHADER_DO_INSTRUCTION_TABLE")]
    ent = [(m.group(1), m.group(2)) for m in re.finditer(r"^INSTRUCTION(?:_STATE)?\((\w+)\s*,[^\n]*?(MOJOSHADER_TYPE_\w+)\s*,\s*\w+\)", tbl, re.M)]
    tbits = {"MOJOSHADER_TYPE_PIXEL": 1, "MOJOSHADER_TYPE_VERTEX": 2, "MOJOSHADER_TYPE_ANY": 3, "MOJOSHADER_TYPE_UNKNOWN": 0}
    tv = enum_values(mjh, "MOJOSHADER_TYPE_"); tbits.update({k: v for k, v in tv.items() if k in ("MOJOSHADER_TYPE_PIXEL", "MOJOSHADER_TYPE_VERTEX", "MOJOSHADER_TYPE_ANY")}) if tv else None
    emit_def = {m: fn.get("emit_GLSL_" + m) for m in {e[0] for e in ent}}
    def emitter_state(name):
        b = emit_def.get(name)
        if name == "RESERVED": return "reserved"
        if b is None: return "no_emitter"
        body = re.sub(r"\s+", "", b[1:-1])
        if body == "": return "empty_emitter"
        if re.fullmatch(r"fail\([^;]*\);(return;)?", body): return "fail_only"
        return None
    ALIAS = {"TEXCOORD": "TEXCRD", "TEX": "TEXLD"}
    # comparison control tables (fnvnx_sm_check.h): ctl value -> name
    chk = rd(os.path.join(mj, "fnvnx_sm_check.h")); cmpt = {m.group(1): int(m.group(2)) for m in re.finditer(r"X\((\w+),\s*(\d+),", chk)}
    # ---- ABI joins ----
    spr = {k: v for k, v in enum_values(abi, "D3DSPR_").items()}
    spsm = {k: v for k, v in enum_values(re.sub(r"\s*<<\s*D3DSP_SRCMOD_SHIFT", "", abi), "D3DSPSM_").items()}
    rt = enum_values(internal, "REG_TYPE_"); sm = enum_values(internal, "SRCMOD_")
    inv = {}
    for n, l in sm_xref.REGNAME.items(): inv.setdefault(l, []).append(n)
    def reg_enum(key):
        base = {"oPos": "rastout", "oFog": "rastout", "oPts": "rastout", "vPos": "misc", "vFace": "misc"}.get(key, key)
        names = inv.get(base) or []
        vals = {spr["D3DSPR_" + n] for n in names if "D3DSPR_" + n in spr}
        return vals
    # ---- class dispatch functions ----
    srcmod_fn = [n for n, b in fn.items() if re.search(r"switch\s*\([^)]*src_mod\s*\)", b)]
    getvar = fn.get("get_GLSL_varname_in_buf", "")
    def reach(root, depth=3):     # the functions called from root, to `depth` calls
        seen, front = set(), [root]
        for _ in range(depth):
            nxt = []
            for f in front:
                for n in re.findall(r"\b(\w+)\s*\(", fn.get(f, "")):
                    if n in fn and n not in seen: seen.add(n); nxt.append(n)
            front = nxt
        return seen
    var_fns = sorted(n for n in reach("get_GLSL_varname_in_buf") if labels(fn[n], "REG_TYPE_"))
    dst_fn = [n for n, b in fn.items() if "result_mod" in b and "MOD_SATURATE" in b]
    swz_fn = [n for n, b in fn.items() if not n.startswith("emit_") and all(("swizzle_" + c) in b for c in "xyzw")]
    wm_fn = [n for n, b in fn.items() if not n.startswith("emit_") and "writemask0" in b and "writemask3" in b]
    rel_fn = [n for n, b in fn.items() if "relative_regtype" in b]
    attr = fn.get("emit_GLSL_attribute", ""); samp = fn.get("emit_GLSL_sampler", "")
    usage = enum_values(mjh, "MOJOSHADER_USAGE_"); ttype = enum_values(internal, "TEXTURE_TYPE_")
    unhandled = []; bykind = {}
    def res(p, cause, info=None):
        k = bykind.setdefault(p["kind"], dict(productions=0, handled=0)); k["productions"] += 1
        if cause is None: k["handled"] += 1
        else: unhandled.append(dict(id=p["id"], cause=cause, **({"detail": info} if info else {})))
    for p in prods:
        k, key, st = p["kind"], p["key"], p["stage"]
        if k == "op":
            f = p["form"]; v = f["opnum"]; base = key.split(":")[1].split("/")[0]
            if v >= 0xFFF0: res(p, None if "FNVNX_SM_TOKEN_" + f.get("tm", base).upper() in chk or re.search(r"FNVNX_SM_TOKEN_" + base.upper(), rd(os.path.join(mj, "mojoshader.c"))) else "no_entry"); continue
            if v >= len(ent): res(p, "no_entry"); continue
            name, tm = ent[v]
            want = ALIAS.get(f["tm"].split()[0].upper(), f["tm"].split()[0].upper()) if f.get("tm") else None
            # the entry at the opcode value must be the op's own entry (comparison forms: the *C entry), see fnvnx_sm_rows.h rule
            n_tab = ALIAS.get(p["form"].get("num", ""), None)
            if name == "RESERVED": res(p, "reserved", name); continue
            if not (tbits.get(tm, 0) & (1 if st == "ps" else 2)): res(p, "stage_mask", "%s %s" % (name, tm)); continue
            es = emitter_state(name)
            if es == "empty_emitter" and (f.get("decl") or (f.get("d") is None and not f.get("s"))):
                es = None      # an instruction with no operand and no effect (nop) or a declaration (consumed by the parser; its output is the attribute / sampler emitter)
            res(p, es, name if es else None)
        elif k in ("dstreg", "srcreg"):
            vals = reg_enum(key)
            if not vals: res(p, "unmapped_key", key); continue
            names = {n for n, v in rt.items() if v in vals}
            ok = any(names & labels(fn[x], "REG_TYPE_") for x in var_fns)
            res(p, None if ok else "no_case", ",".join(sorted(names)))
        elif k == "srcmod":
            v = spsm.get("D3DSPSM_" + key.upper())
            if v is None: res(p, "unmapped_key", key); continue
            nm = {n for n, x in sm.items() if x == v and n not in ("SRCMOD_TOTAL",)}
            ok = any(nm & labels(fn[x], "SRCMOD_") for x in srcmod_fn)
            res(p, None if ok else "no_case", ",".join(sorted(nm)))
        elif k == "dstmod":
            if key in ("x2", "x4", "x8", "d2", "d4", "d8"):      # result shift (D3DSPDM shift field): one generic path reads result_shift
                res(p, None if any("result_shift" in fn[x] for x in dst_fn) else "no_case", "result_shift"); continue
            tok = {"sat": "MOD_SATURATE", "pp": "MOD_PP", "centroid": "MOD_CENTROID"}.get(key)
            if tok is None: res(p, "unmapped_key", key); continue
            ok = any(re.search(r"result_mod\s*&\s*" + tok + r"\b", fn[x]) for x in dst_fn)
            res(p, None if ok else "no_case", tok)
        elif k == "swizzle": res(p, None if swz_fn else "no_case", ",".join(swz_fn))
        elif k == "writemask": res(p, None if wm_fn else "no_case", ",".join(wm_fn))
        elif k == "reladdr":
            via = p["form"]["via"]; tok = {"a0": "REG_TYPE_ADDRESS", "aL": "REG_TYPE_LOOP"}.get(via)
            ok = bool(tok) and bool(rel_fn) and any(tok in labels(fn[x], "REG_TYPE_") for x in var_fns)
            res(p, None if ok else "no_case", tok)
        elif k == "dcl":
            tok = "MOJOSHADER_USAGE_" + {"psize": "POINTSIZE"}.get(key, key.upper())
            if tok not in usage: res(p, "unmapped_key", key); continue
            res(p, None if (tok in labels(attr, "MOJOSHADER_USAGE_") or default_ok(attr)) else "no_case", tok)
        elif k == "sampler":
            tok = "TEXTURE_TYPE_" + {"2d": "2D"}.get(key, key.upper())
            res(p, None if (tok in labels(samp, "TEXTURE_TYPE_")) else "no_case", tok)
    by = {}
    for u in unhandled: by[u["cause"]] = by.get(u["cause"], 0) + 1
    out = dict(format="fnvnx-sm-coverage/1", table_sha256=P["table_sha256"], productions=len(prods), handled=len(prods) - len(unhandled),
               unhandled=unhandled, by_cause=dict(sorted(by.items())), by_kind=dict(sorted(bykind.items())), status="red" if unhandled else "green",
               dispatch=dict(srcmod_fn=srcmod_fn, var_fns=var_fns, dst_fn=dst_fn, swz_fn=swz_fn, wm_fn=wm_fn, rel_fn=rel_fn))
    json.dump(out, open(a.out, "w"), indent=1, sort_keys=True)
    print("productions %d handled %d unhandled %d %s %s" % (out["productions"], out["handled"], len(unhandled), out["by_cause"], out["status"]))
    return 1 if (unhandled and a.strict) else 0

if __name__ == "__main__":
    sys.exit(main())
