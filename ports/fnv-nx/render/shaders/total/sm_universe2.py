"""sm_universe2.py - declaration, sampler, flow-control, relative-addressing and ps_1_x families (see sm_universe.py)."""
from sm_emit import *
CT = {"1d": "sampler1d", "2d": "sampler2d", "cube": "samplercube", "volume": "sampler3d"}

def _units(m): return [u for u in T["usages"]]

def family_dcl(u, m):
    v = isvs(m)
    if v:
        for k, us in enumerate(T["usages"]):
            for ix in (0, 15):
                n = 1 if not (us == "position" and ix == 0) else 2         # v0 is the shell's position input
                u.add(m, "dcl", "in:%s%d" % (us, ix), shell(m, "mov r0, v%d" % n, decls=["dcl_%s%d v%d" % (us, ix, n)]))
        if m == "vs_3_0":
            for us in T["usages"]:
                for ix in (0, 15): u.add(m, "dcl", "out:%s%d" % (us, ix), shell(m, "mov o1, r0", decls=["dcl_%s%d o1" % (us, ix)]))
            for mk in ([".xy", ".zw"], [".x", ".y", ".z", ".w"], [".xyz", ".w"]):
                u.add(m, "dcl", "out-mask:" + "+".join(mk), shell(m, "mov o1, r0", decls=["dcl_texcoord%d o1%s" % (i, k) for i, k in enumerate(mk)]))
            u.add(m, "dcl", "in:v15", shell(m, "mov r0, v15", decls=["dcl_texcoord1 v15"]))
            u.add(m, "dcl", "out:o11", shell(m, "mov o11, r0", decls=["dcl_texcoord1 o11"]))
        return
    if m in ("ps_2_0", "ps_2_x"):
        for r in ("t0", "t7", "v0", "v1"):
            for md in ("", "_pp", "_centroid"):
                u.add(m, "dcl", "dcl%s %s" % (md, r), shell(m, "mov r0, " + r, decls=["dcl%s %s" % (md, r)]))
            u.add(m, "dcl", "dcl %s.xy" % r, shell(m, "mov r0.xy, %s" % r, decls=["dcl %s.xy" % r]))
    if m == "ps_3_0":
        for us in T["usages"]:
            for ix in (0, 15): u.add(m, "dcl", "in:%s%d" % (us, ix), shell(m, "mov r0, v1", decls=["dcl_%s%d v1" % (us, ix)]))
        for md in ("_pp", "_centroid"): u.add(m, "dcl", "in:texcoord0" + md, shell(m, "mov r0, v1", decls=["dcl_texcoord0%s v1" % md]))
        for mk in ([".xy", ".zw"], [".x", ".y", ".z", ".w"], [".xyz", ".w"]):
            u.add(m, "dcl", "in-mask:" + "+".join(mk), shell(m, "mov r0, v1", decls=["dcl_texcoord%d v1%s" % (i, k) for i, k in enumerate(mk)]))
        u.add(m, "dcl", "in:v9", shell(m, "mov r0, v9", decls=["dcl_texcoord9 v9"]))
        u.add(m, "dcl", "vPos.x", shell(m, "mov r0, vPos", decls=["dcl vPos.x"])); u.add(m, "dcl", "vPos.y", shell(m, "mov r0, vPos", decls=["dcl vPos.y"]))

def family_sampler(u, m):
    tys = T["sampler_types"].get(m)
    if ps1(m) or not tys: return
    last = MODELS[m]["regs"]["s"]["n"] - 1
    for e_name, e in [(k[3:], dict(e, key=k)) for k, e in sorted(OPS.items()) if k[:2] == m[:2] and m in e["models"] and e.get("tex") and e.get("d") == "Dr"]:
        for ty in tys:
            for sn in (0, last):
                b = Build(m); txt, b = op_text(m, e, o={1: ("s", sn), "stype": ty} if False else {"stype": ty}, b=b)
                txt = txt.replace("s0", "s%d" % sn); b.decl = [d.replace("s0", "s%d" % sn) for d in b.decl]
                u.add(m, "sampler", "%s:%s:s%d" % (ty, e_name, sn), finish(m, txt, b))

def family_ps1_tex(u, m):
    """ps_1_x: the sampler type is not in the token stream (no dcl_samplerType) - it comes from the bound texture, i.e. a translator INPUT.
    Every texture instruction x {no type info, each legal type}."""
    if not ps1(m): return
    for name, e in [(k[3:], dict(e, key=k)) for k, e in sorted(OPS.items()) if k[:2] == "ps" and m in e["models"] and e.get("tex") and e.get("d") in ("T", "Rr")]:
        st = e.get("st"); tys = ["1d", "2d", "cube", "volume"] if st is None else ["cube", "volume"] if st == "3d_or_cube" else [st]
        txt, b = op_text(m, e)
        stage = {"tex": 0, "texld14": 0}.get(name, 3 if "m3x3" in name else 2 if "m3x2" in name else 1)
        u.add(m, "ps1tex", name + "@ctab=none", finish(m, txt, b))
        for ty in tys:
            u.add(m, "ps1tex", name + "@ctab=" + ty, finish(m, txt, b), ctab="// ctab: s%d sampler %d 1 object %s 1 1" % (stage, stage, CT[ty]))
    for name, e in [(k[3:], dict(e, key=k)) for k, e in sorted(OPS.items()) if k[:2] == "ps" and m in e["models"] and not e.get("tex") and e.get("d") in ("T", "Rr") and not e.get("decl")]:
        txt, b = op_text(m, e); u.add(m, "ps1tex", name, finish(m, txt, b))
    if m == "ps_1_4":
        for nm, body in (("phase+texld-r", "texld r0, t0\nphase\ntexld r1, r0\nmov r0, r1"), ("phase+v", "texcrd r0, t0\nphase\nmov r0, v0"),
                         ("phase+texcrd2", "texld r0, t0\nphase\ntexcrd r1, t1\nmov r0, r1"), ("phase-only-tex", "texld r0, t0\nphase\nmov r0, c0")):
            u.add(m, "phase", nm, shell(m, body))
    if ps1(m):
        alpha = [k[3:] for k, e in sorted(OPS.items()) if k[:2] == "ps" and m in e["models"] and e.get("d") == "D" and k[3:] not in ("nop", "dp3")]
        for name in alpha:
            e = dict(OPS["ps:" + name]); txt, b = op_text(m, e, mask=".w")
            u.add(m, "coissue", name, finish(m, "mul r0.xyz, v0, c0\n+ " + txt, b))

def family_flow(u, m):
    lim = MODELS[m]["flow"]
    if not lim: return
    for name, e in [(k[3:], dict(e, key=k)) for k, e in sorted(OPS.items()) if k[:2] == m[:2] and m in e["models"] and not e.get("decl") and e.get("d") is None and e.get("tm") and "BODY" in e["tm"] or (k[:2] == m[:2] and m in e["models"] and k[3:] in ("call", "callnz_bool", "callnz_pred", "ret", "label"))]:
        if name == "label":
            for lb in (0, lim["labels"] - 1):
                txt, b = op_text(m, e); u.add(m, "flow", "label=l%d" % lb, finish(m, txt.replace("l0", "l%d" % lb), b))
        else:
            txt, b = op_text(m, e); u.add(m, "flow", name, finish(m, txt, b))
    b = Build(m)
    def nest(kind, d):
        if kind == "if": return "defb b0, true\n" + "if b0\n" * d + FILL + "\nendif" * d, 1
        if kind == "ifc": return "if_gt r1.x, r2.x\n" * d + FILL + "\nendif\n" * d, 1
        if kind == "rep": return "defi i0, 4, 0, 1, 0\n" + "rep i0\n" * d + FILL + "\nendrep" * d, 1
        if kind == "loop" and "loop" in [k[3:] for k in OPS if k[:2] == m[:2] and m in OPS[k]["models"]]:
            return "defi i0, 4, 0, 1, 0\n" + "loop aL, i0\n" * d + FILL + "\nendloop" * d, 1
        return None, 0
    for kind, key in (("if", "static"), ("ifc", "dynamic"), ("rep", "loop_rep"), ("loop", "loop_rep")):
        d = lim[key]
        if not d or (kind == "if" and not any(k[3:] == "if_bool" and m in OPS[k]["models"] for k in OPS if k[:2] == m[:2])): continue
        if kind == "ifc" and not any(k[3:] == "if_gt" and m in OPS[k]["models"] for k in OPS if k[:2] == m[:2]): continue
        for dd in sorted({1, d}):
            txt, _ = nest(kind, dd)
            if txt: u.add(m, "nest", "%s-depth-%d" % (kind, dd), shell(m, txt))
    if lim["call"] and any(k[3:] == "call" and m in OPS[k]["models"] for k in OPS if k[:2] == m[:2]):
        for dd in sorted({1, lim["call"]}):                    # call chain l0 -> l1 -> ... depth dd (forward calls only)
            body = "call l0\n@E@\nret\n" + "".join("label l%d\n%s\nret\n" % (i, ("call l%d" % (i + 1)) if i + 1 < dd else FILL) for i in range(dd))
            u.add(m, "nest", "call-depth-%d" % dd, shell(m, body.rstrip("\n")))
    if lim["labels"] > 16:
        u.add(m, "flow", "label-max-l%d" % (lim["labels"] - 1), shell(m, "call l%d\n@E@\nret\nlabel l%d\n%s\nret" % ((lim["labels"] - 1,) * 2 + (FILL,))))

def family_const(u, m):
    regs = MODELS[m]["regs"]
    dn = [k[3:] for k in OPS if k[:2] == m[:2] and m in OPS[k]["models"]]
    if "def" in dn:
        for idx in sorted({0, T["env_default"]["vs_const"] - 1 if m[0] == "v" else T["env_default"]["ps_const"][m] - 1}):
            u.add(m, "def", "def:c%d" % idx, shell(m, "def c%d, 1.0, -2.0, 3.5e10, 0.0\nmov r0, c%d" % (idx, idx)))
    if "defi" in dn:
        for nm, v in (("max", "255, 255, 127, 0"), ("min", "0, 0, -128, 0"), ("zero", "0, 0, 0, 0")):
            for idx in (0, 15): u.add(m, "defi", "defi:i%d:%s" % (idx, nm), shell(m, "defi i%d, %s\nloop aL, i%d\nadd r0, r0, c0\nendloop" % (idx, v, idx) if "loop" in dn else "defi i%d, %s\nrep i%d\nadd r0, r0, c0\nendrep" % (idx, v, idx)))
    if "defb" in dn:
        for idx in (0, 15):
            for val in ("true", "false"): u.add(m, "defb", "defb:b%d:%s" % (idx, val), shell(m, "defb b%d, %s\nif b%d\nmov r0, c0\nendif" % (idx, val, idx)))

def family_rel(u, m):
    regs = MODELS[m]["regs"]; v = isvs(m); top = T["env_default"]["vs_const"] if v else 0
    lines = lambda sel, body: shell(m, sel + body)
    if v:
        comps = ["x"] if m == "vs_1_1" else ["x", "y", "z", "w"]
        setup = "mov a0.x, c0.x\n" if m == "vs_1_1" else "mova a0, c0\n"
        for c in comps:
            for off in (0, 1, top - 1):
                u.add(m, "rel", "c[a0.%s+%d]" % (c, off), shell(m, setup + "mov r0, c[a0.%s + %d]" % (c, off)))
        if m != "vs_1_1":
            for off in (0, top - 1): u.add(m, "rel", "c[aL+%d]" % off, shell(m, "defi i0, 4, 0, 1, 0\nloop aL, i0\nmov r0, c[aL + %d]\nendloop" % off))
            u.add(m, "rel", "mad.c[a0.x]@s0,s1,s2", shell(m, "mova a0, c0\nmad r0, c[a0.x + 1], c[a0.x + 2], c[a0.x + 3]"))
            u.add(m, "rel", "c[a0.x]+c[aL]", shell(m, "mova a0, c0\ndefi i0, 4, 0, 1, 0\nloop aL, i0\nadd r0, c[a0.x + 1], c[aL + 2]\nendloop"))
    if m == "vs_3_0":
        for off in (0, 1, 14): u.add(m, "rel", "v[aL+%d]" % off, shell(m, "defi i0, 4, 0, 1, 0\nloop aL, i0\nmov r0, v[aL + %d]\nendloop" % off, decls=["dcl_texcoord%d v%d" % (i, i) for i in range(1, 16)]))
        for off in (0, 1, 10): u.add(m, "rel", "o[aL+%d]" % off, shell(m, "defi i0, 4, 0, 1, 0\nloop aL, i0\nmov o[aL + %d], r0\nendloop" % off, decls=["dcl_texcoord%d o%d" % (i, i) for i in range(1, 12)]))
    if m == "ps_3_0":
        for off in (0, 1, 8): u.add(m, "rel", "v[aL+%d]" % off, shell(m, "defi i0, 4, 0, 1, 0\nloop aL, i0\nmov r0, v[aL + %d]\nendloop" % off, decls=["dcl_texcoord%d v%d" % (i, i) for i in range(0, 10)]))

def family_vs_closed(u, m):
    """R4 rows (table.resolved vs_a0_src / vs_bi_alu / mov_a0_vs2): forms no source states illegal"""
    if m[0] != "v": return
    v11 = m == "vs_1_1"
    seta = "mov a0.x, c0.x\n" if v11 else "mova a0, c0\n"
    u.add(m, "srcreg", "a0@mov.s0", shell(m, seta + "mov r0, a0.x"), tg="closed-world")
    u.add(m, "srcreg", "a0@add.s1", shell(m, seta + "add r0, c0, a0.x"), tg="closed-world")
    if v11: return
    u.add(m, "srcreg", "b0@mov.s0", shell(m, "defb b0, true\nmov r0, b0"), tg="closed-world")
    u.add(m, "srcreg", "i0@mov.s0", shell(m, "defi i0, 4, 0, 1, 0\nmov r0, i0"), tg="closed-world")
    u.add(m, "dstreg", "a0@mov.dst", shell(m, "mov a0, c0\nmov r0, c[a0.x]"), tg="closed-world")

def families(u, m):
    for f in (family_dcl, family_sampler, family_ps1_tex, family_flow, family_const, family_rel, family_vs_closed): f(u, m)
