#!/usr/bin/env python3
"""sm_linkform.py - link_failed(shader): the static form predicate of the (d) design (inbox l6-render-w3b.md, "(d) rewritten").
link_failed(code) = the bytecode contains a form of table FORMS (UNDEFINED by the D3D9 docs, class closed by sm_linkclass.py:
the wined3d emitter's text is ill-typed / unbalanced for every compile-arg value and partner).  create_refused(code) = forms Wine's
CreateShader refuses (hr 8876086c).  Usage: sm_linkform.py --sdp DIR   (census over the shipped packages; counts only)"""
import argparse, collections, glob, hashlib, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.dont_write_bytecode = True
import sm_reach
OP = dict(MOV=1, LIT=0x10, LOOP=0x1B, ENDLOOP=0x1D, IF=0x28, CALLNZ=0x26, SINCOS=0x25, SGN=0x22)
RT_ADDR, RT_B, RT_AL, RT_PRED = 3, 14, 15, 19
def rt(t): return ((t >> 28) & 7) | ((t >> 8) & 0x18)
# FORMS: (id, predicate(model, stage, major, minor, instr)) ; instr from sm_reach.decode_full
def f_aL_alu(m, st, ins):  return ins["op"] != OP["LOOP"] and any(rt(s) == RT_AL for s, r in ins["srcs"])
def f_b_alu(m, st, ins):   return ins["op"] not in (OP["IF"], OP["CALLNZ"]) and any(rt(s) == RT_B for s, r in ins["srcs"])
def f_a0_mov(m, st, ins):  return st == "vs" and int(m[3]) >= 2 and ins["op"] == OP["MOV"] and ins["dst"] is not None and rt(ins["dst"]) == RT_ADDR
def f_sincos(m, st, ins):  return ins["op"] == OP["SINCOS"] and ((ins["dst"] >> 16) & 0xF) not in (1, 2, 3)
FORMS = [("aL_alu", f_aL_alu), ("b_alu", f_b_alu), ("a0_mov", f_a0_mov), ("sincos_xyz", f_sincos)]
def f_ps_lit_sgn(m, st, ins): return st == "ps" and ins["op"] in (OP["LIT"], OP["SGN"])
def f_ps2x_loop(m, st, ins):  return st == "ps" and m == "ps_2_x" and ins["op"] in (OP["LOOP"], OP["ENDLOOP"])
def f_ps2x_aL(m, st, ins):    return st == "ps" and m == "ps_2_x" and any(rt(s) == RT_AL or (r is not None and rt(r) == RT_AL) for s, r in ins["srcs"])
def f_ps3_dclpos(m, st, ins): return st == "ps" and m == "ps_3_0" and ins["dcl"] is not None and (ins["dcl"] & 0x1F) == 0 and rt(ins["dst"]) == 1
REFUSED = [("ps_lit_sgn", f_ps_lit_sgn), ("ps2x_loop", f_ps2x_loop), ("ps2x_aL", f_ps2x_aL), ("ps3_dcl_position", f_ps3_dclpos)]
def forms(code, stage, table):
    m, ins = sm_reach.decode_full(code, stage)
    return sorted({n for x in ins for n, f in table if f(m, stage, x)})
def link_failed(code, stage): return forms(code, stage, FORMS)
def create_refused(code, stage): return forms(code, stage, REFUSED)
if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--sdp", required=True); a = ap.parse_args()
    seen, tot, hit = set(), collections.Counter(), collections.Counter()
    for f in sorted(glob.glob(os.path.join(a.sdp, "*.sdp"))):
        d = open(f, "rb").read(); magic, count, size = struct.unpack("<III", d[:12]); p = 12
        for _ in range(count):
            sz = struct.unpack("<I", d[p + 256:p + 260])[0]; code = d[p + 260:p + 260 + sz]; p += 260 + sz
            st = "vs" if (struct.unpack("<I", code[:4])[0] >> 16) == 0xFFFE else "ps"
            h = hashlib.sha256(code).hexdigest(); tot["shaders"] += 1; first = h not in seen; seen.add(h); tot["distinct"] += first
            for n in link_failed(code, st) + create_refused(code, st): hit[n] += 1; hit[n + "/distinct"] += first
    print(dict(tot)); print(dict(hit) if hit else "no shipped shader contains a form of FORMS or REFUSED")
