"""bool_census.py - decodes EVERY shader of a set of shader packages (.sdp, render/shaders/SDP.md) with a D3D9 token-stream decoder and
counts every b# (CONSTBOOL) operand by instruction class: FLOW (if, callnz) versus ALU (every other instruction), vertex and pixel separately.
Instruction lengths: the length field for versions >= 2.0; for 1.x the operand counts of sm_table.json (checked: every shader must decode to its
end token 0xFFFF, else the census refuses).  Prints global counts only; --detail PATH writes the per-shader list (private data: never commit it).
usage: python3 -I bool_census.py --sdp DIR [--detail PATH]"""
import argparse, collections, glob, hashlib, json, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
T = json.load(open(os.path.join(HERE, "sm_table.json")))
REG_CONSTBOOL = 14
OP_COMMENT, OP_END, OP_DCL, OP_DEF, OP_DEFB, OP_DEFI = 0xFFFE, 0xFFFF, 0x1F, 0x51, 0x2F, 0x52
FLOW_OPS = {0x28: "if", 0x1A: "callnz"}                           # the only instructions MS lets read a b#: if bool, callnz bool
FLOWCLASS = {25, 26, 27, 28, 29, 30, 38, 39, 40, 41, 42, 43, 44, 45, 96}   # call callnz loop ret endloop label rep endrep if if_comp else endif break break_comp breakp
DEFS = {81, 82, 47}
def regtype(t): return ((t >> 28) & 7) | ((t >> 8) & 0x18)

def shape(stage, opnum, ver):
    """(has_dst, n_src) of opcode `opnum` for `stage` ('vs'/'ps') from the table; sgn uses its vs_3_0 one-source form"""
    for k, e in T["opcodes"].items():
        if k[:2] == stage and e.get("opnum") == opnum:
            s = e["s3"] if "s3" in e and ver >= (3, 0) else e["s"]
            return (1 if e["d"] else 0, len(s))
    return None

def decode(code, stage):
    """-> list of (opcode, [b# source indices]); raises ValueError when the token stream does not decode exactly to its end token"""
    w = struct.unpack("<%dI" % (len(code) // 4), code)
    ver = (w[0] >> 8 & 0xFF, w[0] & 0xFF)
    if w[0] >> 16 != (0xFFFE if stage == "vs" else 0xFFFF): raise ValueError("version token")
    i, out = 1, []
    while True:
        if i >= len(w): raise ValueError("no end token")
        t = w[i]; op = t & 0xFFFF
        if op == OP_END: break
        if op == OP_COMMENT: i += 1 + ((t >> 16) & 0x7FFF); continue
        if ver >= (2, 0): n = (t >> 24) & 0xF
        elif op in (OP_DEF, OP_DEFI): n = 5
        elif op == OP_DEFB: n = 2
        elif op == OP_DCL: n = 2
        else:
            sh = shape(stage, op, ver)
            if sh is None: raise ValueError("opcode %#x in %s_%d_%d" % (op, stage, *ver))
            n = sh[0] + sh[1]
        body = w[i + 1:i + 1 + n]
        if len(body) != n: raise ValueError("truncated")
        bs = []
        if op == OP_DEFB: pass                                       # dst b#, one data word
        elif op in (OP_DEF, OP_DEFI, OP_DCL): pass
        else:
            j = 0
            if ver >= (2, 0) and t & 0x10000000 and n: n_eff = n - 1  # trailing predicate token
            else: n_eff = n
            sh = shape(stage, op, ver)
            if sh is None: raise ValueError("opcode %#x" % op)
            if sh[0]:
                d = body[j]; j += 1
                if d & 0x2000 and ver >= (3, 0): j += 1               # relative dst: extra token (vs_3_0 outputs)
            while j < n_eff:
                s = body[j]; j += 1
                if regtype(s) == REG_CONSTBOOL: bs.append(s & 0x7FF)
                if s & 0x2000 and ver >= (2, 0): j += 1               # relative source: extra token
            if j != n_eff: raise ValueError("operand layout of %#x" % op)
        out.append((op, bs))
        i += 1 + n
    if i != len(w) - 1: raise ValueError("trailing tokens")
    return ver, out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--sdp", required=True); ap.add_argument("--detail")
    a = ap.parse_args()
    cnt = collections.Counter(); h = hashlib.sha256(); det = []
    for f in sorted(glob.glob(os.path.join(a.sdp, "*.sdp"))):
        d = open(f, "rb").read(); h.update(os.path.basename(f).encode() + b"\0" + hashlib.sha256(d).digest())
        magic, count, size = struct.unpack("<III", d[:12]); p = 12
        for _ in range(count):
            name = d[p:p + 256].split(b"\0")[0].decode(); s = struct.unpack("<I", d[p + 256:p + 260])[0]; code = d[p + 260:p + 260 + s]; p += 260 + s
            stage = "vs" if name.endswith(".vso") else "ps"
            ver, ins = decode(code, stage)
            cnt[stage + " shaders"] += 1; cnt["%s_%d_%d shaders" % (stage, *ver)] += 1; cnt[stage + " instructions"] += len(ins)
            has = False
            for op, bs in ins:
                if op in FLOWCLASS: cnt[stage + " flow-control instructions"] += 1
                if op in DEFS: cnt[stage + " def/defi/defb instructions"] += 1
            for op, bs in ins:
                for _b in bs:
                    cnt["%s b# source operands" % stage] += 1
                    k = "FLOW" if op in FLOW_OPS else "ALU"; cnt["%s b# %s" % (stage, k)] += 1
                    if k == "ALU": has = True; det.append("%s\t%s\t%#x" % (os.path.basename(f), name, op))
            if has: cnt[stage + " shaders with a b# ALU source"] += 1
        if p != len(d): raise SystemExit("%s: trailing bytes" % f)
    cnt["packages"] = len(glob.glob(os.path.join(a.sdp, "*.sdp")))
    print(json.dumps(dict(set_sha256=h.hexdigest(), counts=dict(sorted(cnt.items()))), indent=1))
    if a.detail: open(a.detail, "w").write("package\tentry\topcode\n" + "\n".join(det) + "\n")
if __name__ == "__main__": main()
