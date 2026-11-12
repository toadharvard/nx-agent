"""sm_reach.py - for every row of the SM1-3 universe (sm_universe.rows(), 7553 rows) the number of shipped shaders that use the form.
Rule: a form is a set of ATOMS, the orthogonal facts of one instruction (opcode+comparison, predication, co-issue, per-slot register type,
register index, source modifier, swizzle, write mask, result modifier, relative addressing, dcl word, def/defi/defb register, if/loop/rep/call
nesting depth), all keyed by the shader model.  The atoms of a row are those of its instructions that are not in the model's empty shell
(multiset difference of the decoded instructions); a shader USES the row when its model is the row's model and the pool of atoms of all its
instructions contains every atom of the row.  Pooling over the shader makes "used" an over-approximation (a row can be counted used when its
atoms occur in different instructions), never an under-approximation, so a count of 0 is sound (the form occurs in no shipped shader).
def/defi/defb values are not atoms (over-approximation again).  Rows of models no shipped shader has count 0 by model.
usage: python3 -I sm_reach.py --sdp DIR --mojoshader DIR [--out sm_reach.tsv] [--summary sm_reach.json] [--commit SHA]"""
import argparse, collections, glob, hashlib, json, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import bool_census as BC, sm_hand, sm_universe, sm_emit

def regtype(t): return ((t >> 28) & 7) | ((t >> 8) & 0x18)

def decode_full(code, stage):
    """-> (model, [instr]) ; instr = dict(op, cmp, pred, coi, dst, srcs, raw, extra).  Layout rules as bool_census.decode (checked there)."""
    w = struct.unpack("<%dI" % (len(code) // 4), code)
    ver = (w[0] >> 8 & 0xFF, w[0] & 0xFF)
    model = "%s_%d_%s" % (stage, ver[0], "x" if ver[1] == 1 and ver[0] == 2 else ver[1])
    i, out = 1, []
    while True:
        t = w[i]; op = t & 0xFFFF
        if op == BC.OP_END: break
        if op == BC.OP_COMMENT: i += 1 + ((t >> 16) & 0x7FFF); continue
        if ver >= (2, 0): n = (t >> 24) & 0xF
        elif op in (BC.OP_DEF, BC.OP_DEFI): n = 5
        elif op in (BC.OP_DEFB, BC.OP_DCL): n = 2
        else:
            sh = BC.shape(stage, op, ver); n = sh[0] + sh[1]
        body = w[i + 1:i + 1 + n]; ins = dict(op=op, cmp=(t >> 16) & 0xFF if op in (0x29, 0x2D, 0x5E, 0x42) else 0, coi=bool(t & 0x40000000),
                                              pred=None, dst=None, srcs=[], raw=(t,) + tuple(body), dcl=None)
        if op == BC.OP_DCL: ins["dcl"] = body[0]; ins["dst"] = body[1]
        elif op in (BC.OP_DEF, BC.OP_DEFI, BC.OP_DEFB): ins["dst"] = body[0]
        else:
            j, n_eff = 0, n
            if ver >= (2, 0) and t & 0x10000000 and n: n_eff = n - 1; ins["pred"] = body[n - 1]
            sh = BC.shape(stage, op, ver)
            if sh[0]:
                ins["dst"] = body[j]; j += 1
                if ins["dst"] & 0x2000 and ver >= (3, 0): ins["dstrel"] = body[j]; j += 1
            while j < n_eff:
                s = body[j]; j += 1; rel = None
                if s & 0x2000 and ver >= (2, 0): rel = body[j]; j += 1
                elif s & 0x2000: rel = 0xB0000000          # vs_1_1: implicit a0.x
                ins["srcs"].append((s, rel))
        out.append(ins); i += 1 + n
    return model, out

def atoms(model, ins_list):
    A = set(); depth = collections.Counter(); kinds = {0x28: "if", 0x29: "ifc", 0x1B: "loop", 0x26: "rep", 0x19: "call", 0x1A: "call"}; ends = {0x2B: "if", 0x1D: "loop", 0x27: "rep", 0x1C: "call"}
    ifstack = []
    stack = []
    for x in ins_list:
        op = x["op"]
        A.add(("op", model, op, x["cmp"]))
        if x["coi"]: A.add(("coissue", model, op))
        if x["pred"] is not None: A.add(("pred", model, op, x["pred"] >> 16 & 0xFF, x["pred"] >> 24 & 0xF))
        if x["dcl"] is not None: A.add(("dcl", model, x["dcl"]))
        d = x["dst"]
        if d is not None:
            rt = regtype(d); A.add(("slot", model, op, "d", rt)); A.add(("idx", model, "d", rt, d & 0x7FF))
            if op not in (BC.OP_DEF, BC.OP_DEFI, BC.OP_DEFB, BC.OP_DCL) or op == BC.OP_DCL:
                A.add(("mask", model, op, d >> 16 & 0xF)); A.add(("dstmod", model, op, d >> 20 & 0xF, d >> 24 & 0xF))
            if d & 0x2000 and "dstrel" in x: dr = x["dstrel"]; A.add(("rel", model, "d", rt, regtype(dr), dr >> 16 & 0xFF, d & 0x7FF))
        for k, (s, rel) in enumerate(x["srcs"]):
            rt = regtype(s); A.add(("slot", model, op, k, rt)); A.add(("idx", model, "s", rt, s & 0x7FF))
            if s >> 24 & 0xF: A.add(("srcmod", model, op, k, s >> 24 & 0xF))
            if s >> 16 & 0xFF != 0xE4: A.add(("swz", model, op, k, s >> 16 & 0xFF)); A.add(("swz", model, op, "*", s >> 16 & 0xFF))
            if rel is not None: A.add(("rel", model, rt, regtype(rel), rel >> 16 & 0xFF, s & 0x7FF))
        if op in kinds: stack.append(kinds[op]); depth[kinds[op]] += 1; A.add(("nest", model, kinds[op], depth[kinds[op]]))
        if op in ends and stack: depth[stack.pop()] -= 1
    return A


RT = {"r": 0, "v": 1, "c": 2, "a": 3, "t": 3, "oPos": 4, "oFog": 4, "oPts": 4, "oD": 5, "oT": 6, "o": 6, "i": 7, "oC": 8, "oDepth": 9, "s": 10, "b": 14, "aL": 15, "vPos": 17, "vFace": 17, "p": 19, "l": 18}
def rtnum(name):
    import re
    m = re.match(r"([A-Za-z]+?)(\d*)$", name)
    n = m.group(1)
    for k in sorted(RT, key=len, reverse=True):
        if name.startswith(k) and (name[len(k):].isdigit() or name == k): return RT[k], (int(name[len(k):]) if name[len(k):] else None), k
    raise KeyError(name)
SRCMOD = sm_hand.SRCMODS; DSTM = {"sat": (1, 0), "pp": (2, 0), "centroid": (4, 0)}; SH = {k: (0, v) for k, v in sm_hand.SHIFT.items()}
def maskbits(s): return sum({"x": 1, "y": 2, "z": 4, "w": 8}[c] for c in s) if s not in ("", "all") else 15
def swzbyte(s):
    c = {"x": 0, "y": 1, "z": 2, "w": 3}; s = s + s[-1] * (4 - len(s)); return sum(c[ch] << (2 * i) for i, ch in enumerate(s))

def row_atoms(r, rest, T):
    """the atoms the row's FEATURE names (not the incidental operands of its probe program).  Unparsed -> the opcode atom only (weaker = safe)."""
    import re
    m, k, f = r["model"], r["kind"], r["feature"]; st = m[:2]
    def opnum(name):
        e = T["opcodes"].get("%s:%s" % (st, name)) or T["opcodes"].get("%s:%s" % (st, name.split("=")[0]))
        return None if e is None else e.get("opnum")
    head = f.split("@")[0]; tail = f.split("@", 1)[1] if "@" in f else ""
    probe = lambda op: [x for x in rest if x["op"] == op] or rest
    A = set()
    def opatom(op):
        for x in probe(op)[-1:]: A.add(("op", m, x["op"], x["cmp"]))
    if k == "opcode": opatom(opnum(f))
    elif k == "flow": opatom(opnum(f))
    elif k == "coissue":
        op = opnum(f); opatom(op); A.add(("coissue", m, op))
    elif k == "operand":
        op = opnum(head); mm = re.match(r"(src(\d)|dst)=(\w+)", tail); rt, _, _ = rtnum(mm.group(3)) if mm.group(3) not in ("c", "v", "r", "t", "s") else (RT[mm.group(3)], 0, 0)
        A.add(("op", m, op, 0) if False else ("slot", m, op, int(mm.group(2)) if mm.group(2) else "d", rt))
    elif k == "regidx":
        role, name = f.split(":"); rt, idx, base = rtnum(name)
        A.add(("idx", m, "s" if role == "src" else "d", rt, idx) if base in ("r", "c", "v", "t", "oD", "oT", "oC", "o") else ("slot_any", m, "s" if role == "src" else "d", rt))
    elif k == "srcmod":
        mm = re.match(r"(\w+)@(\w+)\.s(\d)", f); op = opnum(mm.group(2)); A.add(("srcmod", m, op, int(mm.group(3)), SRCMOD[mm.group(1)]))
    elif k == "dstmod":
        mod = head; op = opnum(tail)
        if mod in DSTM: A.add(("dstmod", m, op, DSTM[mod][0], 0))
        else: A.add(("dstmod", m, op, 0, SH[mod][1]))
    elif k == "writemask":
        mm = re.match(r"(\w+)@(?:(\w+?)\d*)?\.(\w+)", f); op = opnum(mm.group(1)); A.add(("mask", m, op, maskbits(mm.group(3)) if mm.group(3) != "all" else 15))
        if mm.group(2): A.add(("slot", m, op, "d", RT[mm.group(2)]))
    elif k == "swizzle":
        op = opnum(head); who, _, sw = tail.partition(".")
        kk = int(who[1:]) if who.startswith("s") and who[1:].isdigit() else (0 if who == "coord" else "*")
        sw = sw.translate(str.maketrans("rgba", "xyzw"))
        if sw == "none" or sw == "": opatom(op)
        else: A.add(("swz", m, op, kk, swzbyte(sw)))
    elif k == "pred":
        op = opnum(head); opatom(op)
        for x in probe(op)[-1:]:
            if x["pred"] is not None: A.add(("pred", m, x["op"], x["pred"] >> 16 & 0xFF, x["pred"] >> 24 & 0xF))
    elif k in ("dcl", "sampler"):
        for x in rest:
            if x["dcl"] is not None: A.add(("dcl", m, x["dcl"])); A.add(("idx", m, "d", regtype(x["dst"]), x["dst"] & 0x7FF))
        if k == "sampler": opatom(opnum(f.split(":")[1]))
    elif k in ("def", "defi", "defb"):
        op = {"def": 0x51, "defi": 0x52, "defb": 0x2F}[k]; nm = f.split(":")[1]; rt, idx, _ = rtnum(nm) if nm[0] != "c" else (2, int(nm[1:]), "c")
        A.add(("op", m, op, 0)); A.add(("idx", m, "d", rt, idx))
    elif k == "nest":
        kd, depth = f.split("-depth-"); A.add(("nest", m, kd, int(depth)))
    elif k == "rel":
        for x in rest:
            for (s, rel) in x["srcs"]:
                if rel is not None: A.add(("rel", m, regtype(s), regtype(rel), rel >> 16 & 0xFF, s & 0x7FF))
            if "dstrel" in x: dr = x["dstrel"]; A.add(("rel", m, "d", regtype(x["dst"]), regtype(dr), dr >> 16 & 0xFF, x["dst"] & 0x7FF))
    elif k in ("srcreg", "dstreg"):
        mm = re.match(r"(\w+?)(\d)@(\w+)\.(s(\d)|dst)", f); op = opnum(mm.group(3))
        A.add(("slot", m, op, "d" if mm.group(4) == "dst" else int(mm.group(5)), RT[mm.group(1)]))
    else: raise KeyError(k)
    return A

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--sdp", required=True); ap.add_argument("--mojoshader", required=True)
    ap.add_argument("--out", default=os.path.join(HERE, "sm_reach.tsv")); ap.add_argument("--summary", default=os.path.join(HERE, "sm_reach.json")); ap.add_argument("--commit", default="?")
    a = ap.parse_args()
    ops = sm_hand.load_opcodes(os.path.join(a.mojoshader, "mojoshader_internal.h"))
    pool = collections.defaultdict(lambda: collections.defaultdict(int))     # model -> atom -> bitmask of shaders
    nsh = collections.Counter(); h = hashlib.sha256(); total = 0
    for f in sorted(glob.glob(os.path.join(a.sdp, "*.sdp"))):
        d = open(f, "rb").read(); h.update(os.path.basename(f).encode() + b"\0" + hashlib.sha256(d).digest())
        count = struct.unpack("<I", d[4:8])[0]; p = 12
        for _ in range(count):
            name = d[p:p + 256].split(b"\0")[0].decode(); s = struct.unpack("<I", d[p + 256:p + 260])[0]; code = d[p + 260:p + 260 + s]; p += 260 + s
            model, ins = decode_full(code, "vs" if name.endswith(".vso") else "ps")
            bit = 1 << nsh[model]; nsh[model] += 1; total += 1
            for at in atoms(model, ins): pool[model][at] |= bit
    rows = sm_universe.rows(); shell = {}
    def shell_ins(m):
        if m not in shell:
            code = sm_hand.assemble(sm_emit.shell(m, ""), ops); shell[m] = collections.Counter(x["raw"] for x in decode_full(code, m[:2])[1])
        return shell[m]
    res = []; unenc = 0
    for r in rows:
        m = r["model"]
        if m not in nsh: res.append((r, 0, 0, "model not in shipped set")); continue
        if r.get("ctab"): raise SystemExit("ctab row of a shipped model: %s" % r["feature"])
        code = sm_hand.assemble(r["asm"], ops); mm, ins = decode_full(code, m[:2])
        rest, sc = [], collections.Counter(shell_ins(m))
        for x in ins:
            if sc[x["raw"]] > 0: sc[x["raw"]] -= 1
            else: rest.append(x)
        A = row_atoms(r, rest, sm_emit.T); mask = (1 << nsh[m]) - 1
        for at in A: mask &= pool[m].get(at, 0)
        res.append((r, bin(mask).count("1"), nsh[m], "" if A else "no probe atoms"))
    with open(a.out, "w") as o:
        o.write("model\tkind\tfeature\tshipped_shaders_using\tshaders_of_model\tnote\n")
        for r, u, n, note in res: o.write("%s\t%s\t%s\t%d\t%d\t%s\n" % (r["model"], r["kind"], r["feature"], u, n, note))
    used = sum(1 for _, u, _, _ in res if u > 0); notes = collections.Counter(n for *_, n in res if n)
    S = dict(commit=a.commit, set_sha256=h.hexdigest(), shaders=total, shaders_per_model=dict(sorted(nsh.items())), rows=len(res), rows_used_by_ge1=used,
             rows_used_by_0=len(res) - used, rows_by_note=dict(notes),
             rows_used_by_0_in_shipped_models=sum(1 for r, u, n, note in res if u == 0 and not note.startswith("model")))
    json.dump(S, open(a.summary, "w"), indent=1, sort_keys=True); print(json.dumps(S, indent=1, sort_keys=True))
if __name__ == "__main__": main()
