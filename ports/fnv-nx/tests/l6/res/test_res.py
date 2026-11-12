#!/usr/bin/env python3
"""test_res.py: tests of render/d3d9/dev/res_* (D3: buffers, declarations + FVF, shaders, queries).  python3 -I tests/l6/res/test_res.py [--no-mutations]
 a creation rules   b locks / dirty range / DISCARD zero-fill   c FVF + declarations (python oracle fvf.txt)   d shader validator + interface (python-encoded cases)   e queries
 f gate G-METHOD before -> after (tests/l6/dev/test_dev.py's gate())   g mutation checks.   Oracles are independent of the C++: fvf_oracle() from the MS D3DFVF page + Wine's Windows-validated
 fvf_to_decl_tests, shader bytecode from render/total/census/sm_hand.py (read-only import) over sm_universe.py rows and the sm_cases rows behind sm_illegal.tsv."""
import concurrent.futures as cf, csv, importlib.util, json, os, random, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(); import mutsrc
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEV = os.path.join(ROOT, "render", "d3d9", "dev"); GEN = os.path.join(ROOT, "render", "d3d9", "gen"); SPEC = os.path.join(ROOT, "render", "d3d9", "spec", "d3d9_spec.json")
TOT = os.path.join(ROOT, "render", "shaders", "total"); CENSUS = os.path.join(ROOT, "render", "total", "census"); DEVT = os.path.join(ROOT, "tests", "l6", "dev")
INC = os.path.join(ROOT, "shim", "include"); FRONT = os.path.join(ROOT, "render", "d3d9", "front"); BACKEND = os.path.join(ROOT, "render", "gl4"); NXB = os.path.normpath(os.path.join(BACKEND, "..", "backend")); FRONT_C = sorted(f for f in os.listdir(FRONT) if f.endswith(".c"))
LIB = ["obj.cpp", "app_unk_native.cpp", "state_rt.cpp", "state_ext.cpp"]; GENS = ["d9impl_unk.gen.cpp", "state.gen.cpp"]; RES = ["res_buf.cpp", "res_fvf.cpp", "res_decl.cpp", "res_shader.cpp", "res_smcheck.cpp", "res_query.cpp"]; NATIVE = os.path.join(ROOT, "tests", "l6", "native")
TESTS = ["t_env.cpp", "t_buf.cpp", "t_decl.cpp", "t_fvf.cpp", "t_shader.cpp", "t_query.cpp"]
fails = []


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def build(dev, tmp, jobs=8):
    os.makedirs(tmp, exist_ok=True)
    for cmd in ([sys.executable, "-I", os.path.join(GEN, "gen_impl.py"), tmp, SPEC], [sys.executable, "-I", os.path.join(GEN, "gen_wine_ids.py"), tmp], [sys.executable, "-I", os.path.join(dev, "state_model.py"), tmp, SPEC],
                [sys.executable, "-I", os.path.join(dev, "res_smgen.py"), tmp, os.path.join(TOT, "sm_table.json"), os.path.join(os.path.dirname(SPEC), "d3d9_tables.py")]):
        r = sh(cmd)
        if r.returncode: return None, None, r.stderr[-800:]
    fl = ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O0", "-w", "-I", INC, "-I", tmp, "-I", HERE, "-I", dev]
    jobs_ = [(os.path.join(NATIVE if s == "app_unk_native.cpp" else dev, s), s) for s in LIB + RES] + [(os.path.join(tmp, s), s) for s in GENS] + [(os.path.join(HERE, s), s) for s in TESTS]
    cmds = [fl + ["-c", src, "-o", os.path.join(tmp, n + ".o")] for src, n in jobs_]
    cmds += [["gcc", "-m32", "-std=c11", "-w", "-I", BACKEND, "-I", NXB, "-I", FRONT, "-c", os.path.join(FRONT, c), "-o", os.path.join(tmp, "nxfront_" + c[:-2] + ".o")] for c in FRONT_C]
    with cf.ThreadPoolExecutor(jobs) as ex: rs = list(ex.map(sh, cmds))
    for c, r in zip(cmds, rs):
        if r.returncode: return None, None, " ".join(c[-3:]) + "\n" + r.stderr[-1500:]
    prod = [os.path.join(tmp, n + ".o") for _, n in jobs_ if n in LIB + GENS + RES]
    allo = prod + [os.path.join(tmp, n + ".o") for n in TESTS] + [os.path.join(tmp, "nxfront_" + c[:-2] + ".o") for c in FRONT_C]
    r = sh(["g++", "-m32", "-no-pie", "-o", os.path.join(tmp, "test_res")] + allo + ["-Wl,--unresolved-symbols=ignore-all"])
    return (os.path.join(tmp, "test_res"), prod, "") if r.returncode == 0 else (None, None, r.stderr[-800:])


# ---------------------------------------------------------------- oracles
def fvf_oracle(fvf):
    """MS D3DFVF page + Wine d3d9 tests fvf_to_decl_tests (Windows-validated). Types: 0..3 FLOAT1-4, 4 D3DCOLOR, 5 UBYTE4. Usages: 0 POSITION 1 BLENDWEIGHT 2 BLENDINDICES 3 NORMAL 4 PSIZE 5 TEXCOORD 10 COLOR 9 POSITIONT."""
    if fvf & 0x1 or fvf & 0x6000 or (fvf >> 8) & 15 > 8: return None
    p = fvf & 0x400E; els = []
    if p not in (0, 2, 4, 6, 8, 10, 12, 14, 0x4002): return None
    if p == 2: els.append((2, 0, 0))
    elif p == 4: els.append((3, 9, 0))
    elif p == 0x4002: els.append((3, 0, 0))
    elif p >= 6:
        els.append((2, 0, 0)); betas = (p - 4) // 2; ub, col = bool(fvf & 0x1000), bool(fvf & 0x8000); idx = betas == 5 or ub or col; w = betas - (1 if idx else 0)
        quirk = betas == 2 and col                                                        # Wine test: XYZB2 | LASTBETA_D3DCOLOR = D3DCOLOR weight + UBYTE4 index
        if w: els.append((4 if quirk else w - 1, 1, 0))
        if idx: els.append((5 if ub or quirk else 4 if col else 0, 2, 0))
    if fvf & 0x10: els.append((2, 3, 0))
    if fvf & 0x20: els.append((0, 4, 0))
    if fvf & 0x40: els.append((4, 10, 0))
    if fvf & 0x80: els.append((4, 10, 1))
    for i in range((fvf >> 8) & 15): els.append(({0: 1, 1: 2, 2: 3, 3: 0}[(fvf >> (16 + 2 * i)) & 3], 5, i))
    sz = {0: 4, 1: 8, 2: 12, 3: 16, 4: 4, 5: 4}; off = 0; out = []
    for t, u, x in els: out.append((off, t, u, x)); off += sz[t]
    return off, out


def write_fvf(path):
    rnd = random.Random(7); vals = []
    pats = [lambda c: 0, lambda c: 0x5555, lambda c: 0xAAAA, lambda c: 0xFFFF, lambda c: 0xE4E4 & ((1 << 2 * c) - 1) if c else 0, lambda c: rnd.getrandbits(16), lambda c: rnd.getrandbits(16)]
    for p in (0, 2, 4, 6, 8, 10, 12, 14, 0x4002):
        for lb in (0, 0x1000, 0x8000, 0x9000):
            for flags in range(16):
                for nt in range(9):
                    for pt in pats:
                        sizes = pt(nt); sizes &= (1 << 2 * nt) - 1
                        extra = rnd.getrandbits(16) << 16 if False else 0
                        vals.append(p | lb | (flags << 4) | (nt << 8) | (sizes << 16))
    for nt in range(9):                                                                     # texture size bits of textures >= count are ignored
        for _ in range(40): vals.append(2 | (nt << 8) | (rnd.getrandbits(16) << 16))
    for _ in range(20000): vals.append(rnd.getrandbits(32))
    for i in range(32): vals.append(1 << i)
    n = 0
    with open(path, "w") as f:
        for v in vals:
            o = fvf_oracle(v)
            f.write("%08x 0 0 0\n" % v if o is None else "%08x 1 %d %d %s\n" % (v, len(o[1]), o[0], " ".join("%d,%d,%d,%d" % e for e in o[1]))); n += 1
    return n


def load_sm():
    sys.path.insert(0, CENSUS); sys.path.insert(0, TOT)
    import sm_hand, sm_cases, sm_universe
    T = json.load(open(os.path.join(TOT, "sm_table.json"))); ops = {e["num"]: e["opnum"] for e in T["opcodes"].values()}; ops["PHASE"] = 0xFFFD; ops["TEXCRD"] = ops["TEXCOORD"]
    return sm_hand, sm_cases, sm_universe, ops


def hexw(b): return " ".join("%08x" % w for w in __import__("struct").unpack("<%dI" % (len(b) // 4), b))


IFACE = [  # (stage, id, asm, in_mask, cf_lo, cf_hi, sampler_mask, ci_mask, cb_mask, def_f, def_i, def_b, n_instr, usages {reg: (usage, index)}, sampler types {reg: type})
    (0, "vs2_dcl_def", "vs_2_0\ndcl_position v0\ndcl_texcoord1 v3\ndcl_color v5\ndef c7, 1.0, 2.0, 3.0, 4.0\ndefi i2, 1, 2, 3, 4\ndefb b5, true\nmov r0, c2\nmad r1, v0, c7, v3\nmov oPos, r1\n", 0x29, 2, 8, 0, 4, 0x20, 1, 1, 1, 3, {0: (0, 0), 3: (5, 1), 5: (10, 0)}, {}),
    (1, "ps2_samplers", "ps_2_0\ndcl_2d s0\ndcl_cube s3\ndcl t0\ntexld r0, t0, s0\ntexld r1, t0, s3\nmov oC0, r0\n", 0, 0, 0, 0x9, 0, 0, 0, 0, 0, 3, {}, {0: 2, 3: 3}),
    (1, "ps14_tex", "ps_1_4\ntexld r0, t0\ntexld r1, t3\nmov r0, r1\n", 0, 0, 0, 0x9, 0, 0, 0, 0, 0, 3, {}, {}),
    (1, "ps11_tex", "ps_1_1\ntex t0\ntexcoord t1\ntex t2\nmov r0, t0\n", 0, 0, 0, 0x5, 0, 0, 0, 0, 0, 4, {}, {}),
    (0, "vs11_noDcl", "vs_1_1\nmov oPos, v7\nmov oD0, v15\n", 0x8080, 0, 0, 0, 0, 0, 0, 0, 0, 2, {}, {}),
    (0, "vs3_vtex", "vs_3_0\ndcl_position v0\ndcl_position o0\ndcl_2d s1\ntexldl r0, v0, s1\nmov o0, r0\n", 1, 0, 0, 0x2, 0, 0, 0, 0, 0, 2, {0: (0, 0)}, {1: 2}),
    (1, "ps3_flow", "ps_3_0\ndcl_color v0\ndcl_volume s2\ndefb b3, true\nif b3\nmov r0, c0\nendif\nloop aL, i1\nadd r0, r0, c4\nendloop\nmov oC0, r0\n", 0, 0, 5, 0x4, 0x2, 0x8, 0, 0, 1, 7, {}, {2: 4}),
    (0, "vs2_rel", "vs_2_0\ndcl_position v0\nmova a0.x, v0\nmov r0, c[a0.x + 4]\nmov oPos, r0\n", 1, 0, 256, 0, 0, 0, 0, 0, 0, 3, {0: (0, 0)}, {}),
]
ERRMAP = {"opcode": "OP_MODEL,OPCODE", "srcmod": "SRCMOD,TEXLD_OPERAND", "dstmod": "DSTMOD", "addr": "RELADDR"}
# sm_illegal.tsv rows that are NOT illegal bytecode (data defects found by this test, reported to the owner of render/shaders/total):
#  not@if.p0 (sm_legacy.py: the generic `not` branch shadows the specific legal `if !p0.x` branch), swizzle@texld.sampler-src (the generated asm `s0.xyzw` is the identity swizzle:
#  its token stream is the legal texld). They are fed to the checker as LEGAL rows. ps_2_x c[aL+n]: its asm uses `loop`, which ps_2_x does not have (rejected by the opcode rule first).
TSV_LEGAL = ("not@if.p0", "swizzle@texld.sampler-src"); TSV_OTHER_RULE = {("ps_2_x", "c[aL+n]"): "OP_MODEL,RELADDR"}
PS = "%s\ndcl_2d s0\nmov r1, c1\n%s\nmov oC0, r0\n"; VS = "%s\ndcl_position v0\nmov r1, c1\n%s\nmov oPos, r0\n"
EXTRA_ILLEGAL = [("ps_2_0", PS % ("ps_2_0", "texld r0, r1, s0.wzyx"), "TEXLD_OPERAND"), ("ps_2_x", PS % ("ps_2_x", "texld r0, r1, s0.yxzw"), "TEXLD_OPERAND"), ("ps_2_0", PS % ("ps_2_0", "texld r0, r1.yxzw, s0"), "TEXLD_OPERAND"),
                  ("ps_2_x", PS % ("ps_2_x", "texld r0, -r1, s0"), "TEXLD_OPERAND"), ("vs_2_0", VS % ("vs_2_0", "(p0) mov r0, c0"), "PRED"), ("ps_2_0", PS % ("ps_2_0", "(p0) mov r0, c0"), "PRED")]


def write_sm(d):
    hand, cases, uni, ops = load_sm(); st = lambda m: 1 if m[0] == "p" else 0
    n_ok = n_skip = 0
    with open(os.path.join(d, "sm_legal.txt"), "w") as f:
        for r in uni.rows():
            if r.get("ctab"): n_skip += 1; continue
            try: b = hand.assemble(r["asm"], ops)
            except Exception: n_skip += 1; continue
            f.write("%d %s %s\n" % (st(r["model"]), (r["model"] + ":" + r["kind"] + ":" + r["feature"]).replace(" ", "_"), hexw(b))); n_ok += 1
    cs = {(m, k, ft): t for m, k, ft, t, lg in cases.cases()}; rows = list(csv.DictReader(open(os.path.join(TOT, "sm_illegal.tsv")), delimiter="\t")); kinds = {}
    with open(os.path.join(d, "sm_illegal.txt"), "w") as f, open(os.path.join(d, "sm_legal.txt"), "a") as fl:
        for r in rows:
            b = hand.assemble(cs[(r["model"], r["kind"], r["feature"])], ops); kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1; rid = (r["model"] + ":" + r["kind"] + ":" + r["feature"]).replace(" ", "_")
            if r["feature"].endswith(TSV_LEGAL): fl.write("%d tsvdefect:%s %s\n" % (st(r["model"]), rid, hexw(b))); kinds["tsv_defect"] = kinds.get("tsv_defect", 0) + 1; continue
            f.write("%d %s %s %s\n" % (st(r["model"]), rid, TSV_OTHER_RULE.get((r["model"], r["feature"]), ERRMAP[r["kind"]]), hexw(b)))
        for m, text, want in EXTRA_ILLEGAL:
            b = hand.assemble(text, ops); f.write("%d extra:%s:%s %s %s\n" % (st(m), m, text.split("\n")[-3].replace(" ", "_"), want, hexw(b)))
    with open(os.path.join(d, "iface.txt"), "w") as f:
        for stg, i, asm, im, lo, hi, sm, ci, cb, df, di, db, nin, us, sty in IFACE:
            f.write("%d %s %x %d %d %x %x %x %d %d %d %d U %s S %s H %s\n" % (stg, i, im, lo, hi, sm, ci, cb, df, di, db, nin, " ".join("%d:%d:%d" % (r, u, x) for r, (u, x) in us.items()), " ".join("%d=%d" % kv for kv in sty.items()), hexw(hand.assemble(asm, ops))))
    spec = json.load(open(SPEC))["tables"]["query_type"]
    with open(os.path.join(d, "qsizes.txt"), "w") as f:
        for k, v in spec.items(): f.write("%s %d %d\n" % (k, v["value"], v["size"]))
    return n_ok, n_skip, kinds, len(rows)


def run(exe, data):
    r = sh([exe, data]); parts = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}
    return r, parts


def main():
    tmp = tempfile.mkdtemp(prefix="d9res_"); data = os.path.join(tmp, "data"); os.makedirs(data)
    nf = write_fvf(os.path.join(data, "fvf.txt")); ok, skip, kinds, nill = write_sm(data)
    print(f"oracle files: fvf {nf} lines; shader legal rows {ok} (skipped {skip}: ctab / encode), illegal rows {nill} {kinds}")
    exe, prod, err = build(DEV, tmp)
    if not exe: print("BUILD FAILED\n" + err); return 1
    r, parts = run(exe, data); print(r.stdout[-6000:])
    for p in "abcde":
        if p not in parts or parts[p][1] or not parts[p][0]: fails.append(f"{p}: {parts.get(p)}")
    print("parts: " + ", ".join(f"{p} {parts[p][0]} checks" for p in sorted(parts)))
    # ---- f: gate G-METHOD before -> after (the gate function of tests/l6/dev/test_dev.py, read-only import)
    sp = importlib.util.spec_from_file_location("test_dev", os.path.join(DEVT, "test_dev.py")); td = importlib.util.module_from_spec(sp); sp.loader.exec_module(td)
    want, miss, unres = td.gate(tmp, prod); before = td.gate(tmp, [o for o in prod if not os.path.basename(o).startswith("res_")])
    N = len(want); print(f"G-METHOD before (no res_*): methods in spec {N}, defined {N - len(before[1])}, missing {len(before[1])}")
    print(f"G-METHOD after  (with res_*): methods in spec {N}, defined {N - len(miss)}, missing {len(miss)}")
    newly = sorted(set(before[1]) - set(miss)); print(f"newly defined {len(newly)}; still missing in the classes of D3: " + ", ".join(sorted(m for m in miss if m.split("::")[0] in ("NxVertexBuffer9", "NxIndexBuffer9", "NxVertexDeclaration9", "NxVertexShader9", "NxPixelShader9", "NxQuery9")) ) )
    print("still missing device methods of D3 scope: " + ", ".join(m for m in miss if m.split("::")[1] in ("ProcessVertices", "CreateVertexBuffer", "CreateIndexBuffer", "CreateVertexDeclaration", "CreateVertexShader", "CreatePixelShader", "CreateQuery")))
    print(f"unresolved product references (hooks): {unres}")
    if not len(miss) < len(before[1]): fails.append("f: gate did not improve")
    if "--no-mutations" not in sys.argv:
        muts = [("b", "res_buf.cpp", "if (off >= length || end > length)", "if (off > length || end > length)"), ("b", "res_buf.cpp", "if (discard) memset(mem, 0, length);", ""),
                ("b", "res_buf.cpp", "&& gpu && dyn && !(flags", "&& gpu && !(flags"), ("b", "res_buf.cpp", "if (managed()) { lo = off;", "if (false) { lo = off;"),
                ("a", "res_buf.cpp", "(uint32_t)pool >= (uint32_t)D3DPOOL_SCRATCH", "(uint32_t)pool > (uint32_t)D3DPOOL_SCRATCH"), ("a", "res_buf.cpp", "if (fmt != D3DFMT_INDEX16 && fmt != D3DFMT_INDEX32) return D3DERR_INVALIDCALL;", ""),
                ("c", "res_fvf.cpp", "bool b2c = pos == D3DFVF_XYZB2 && (fvf & D3DFVF_LASTBETA_D3DCOLOR);", "bool b2c = false;"), ("c", "res_fvf.cpp", "{D3DDECLTYPE_FLOAT2, D3DDECLTYPE_FLOAT3, D3DDECLTYPE_FLOAT4, D3DDECLTYPE_FLOAT1}", "{D3DDECLTYPE_FLOAT2, D3DDECLTYPE_FLOAT3, D3DDECLTYPE_FLOAT1, D3DDECLTYPE_FLOAT4}"),
                ("c", "res_fvf.cpp", "|| (fvf & D3DFVF_RESERVED2)", ""), ("c", "res_fvf.cpp", "if (FvfEnt* f = c->m.find(fvf)) return f->d;", ""), ("c", "res_fvf.cpp", "|| seen[e->Usage][e->UsageIndex]", ""),
                ("a", "res_decl.cpp", "(e[i].Offset & 3)", "(e[i].Offset & 1)"), ("a", "res_decl.cpp", "k < 127", "k < 128"), ("c", "res_decl.cpp", "d->fvf = nx_decl_to_fvf(e);", "d->fvf = 0;"),
                ("d", "res_smcheck.cpp", "(sm && !((M.srcmods >> sm) & 1))", "false"), ("d", "res_smcheck.cpp", "if (!(M.rel[cl] & radd)) return NX_SM_E_RELADDR;", ""), ("d", "res_smcheck.cpp", "if (bits & ~(uint32_t)M.dstmods) return NX_SM_E_DSTMOD;", ""),
                ("d", "res_smcheck.cpp", "((NX_SM_TEXLD_COORD_MODELS >> c.mbit) & 1) && !plain(t[at])", "false"), ("d", "res_smgen.py", 'ms = sum(1 << MI[m] for m in e["models"] if m in MI)', "ms = (1 << len(order)) - 1"),
                ("d", "res_smcheck.cpp", "if (c.major >= 2 && (optok & 0x10000000u)) { if (!c.M->pred) return NX_SM_E_PRED;", "if (c.major >= 2 && (optok & 0x10000000u)) { if (false) return NX_SM_E_PRED;"),
                ("a", "res_shader.cpp", "if (*sz < nbytes) return D3DERR_INVALIDCALL;", ""), ("e", "res_query.cpp", "(flags & D3DISSUE_BEGIN) ? NXQ_BUILDING : NXQ_SIGNALED", "NXQ_SIGNALED"),
                ("e", "res_query.cpp", "if (flags & D3DGETDATA_FLUSH) nxd3d_query_flush(this);", ""), ("e", "res_query.cpp", "if (state == NXQ_BUILDING) return S_FALSE;", "")]
        def one(i):
            part, f, old, new = muts[i]; root = os.path.join(tmp, f"m{i}_root", "d3d9"); mdev = os.path.join(root, "dev"); os.makedirs(root)
            shutil.copytree(DEV, mdev, ignore=shutil.ignore_patterns("__pycache__")); os.symlink(os.path.join(ROOT, "render", "d3d9", "ffp"), os.path.join(root, "ffp"))     # dev/res_fvf.cpp includes ../ffp/
            txt = open(os.path.join(mdev, f)).read()
            open(os.path.join(mdev, f), "w").write(mutsrc.apply(txt, old, new, f"{f} #{i}"))
            x, pr, e = build(mdev, os.path.join(tmp, f"m{i}"), jobs=2)
            if not x: return i, None, e
            rr, ps = run(x, data); return i, ps, rr.returncode
        with cf.ThreadPoolExecutor(3) as ex:
            for i, ps, rc in ex.map(one, range(len(muts))):
                part = muts[i][0]; ok = ps is not None and (ps.get(part, (0, 0))[1] > 0)
                print(f"mutation {i} ({muts[i][1]}: {muts[i][2][:48]!r} -> part {part}): {'caught' if ok else 'NOT CAUGHT'} {'' if ok else str((ps, rc))[:300]}")
                if not ok: fails.append(f"g: mutation {i} not caught ({ps} {rc})")
    shutil.rmtree(tmp, ignore_errors=True)
    print("FAILED: " + "; ".join(fails) if fails else "ALL OK")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
