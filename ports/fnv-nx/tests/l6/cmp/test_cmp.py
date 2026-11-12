#!/usr/bin/env python3
"""test_cmp.py: self-test of the D9S1 comparator (d9cmp.py) and the G-REPLAY replayer (gen_replay.py + d9replay_rt.cpp).  python3 -I tests/l6/cmp/test_cmp.py
 a replay: record R1 with the echo implementation + driver (tests/l6/wrap), replay R1 against a fresh echo -> R2; d9cmp R1 R2 must be equal, all depth-0 records re-issued, every spec method covered
 b comparator mutations (one per item kind) -> exit 1 naming the record; pix masked; env -> env_diff;  c replay mutation: an echo with one det output changed -> exactly that method
 d polling collapse (k vs m repeats; a non-poll repeat is a mismatch);  e threads: other interleaving equal + counted (also with renumbered object ids); fail-closed generator, spec-hash refusal.  Exit != 0 on any failure."""
import contextlib, hashlib, io, json, os, re, shutil, struct, subprocess, sys, tempfile, concurrent.futures as cf
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install()
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
GEN, SPEC = (os.path.join(ROOT, "render", "d3d9", x) for x in ("gen", os.path.join("spec", "d3d9_spec.json"))); CMP = os.path.join(ROOT, "render", "tools", "cmp"); REPLAY = os.path.join(ROOT, "render", "tools", "replay"); INC = os.path.join(ROOT, "shim", "include")
sys.path[:0] = [os.path.join(ROOT, "tests", "l6", "wrap"), CMP]
import plan as P, enc, gen_echo, d9cmp, d9s1                       # noqa: E402
fails, counts = [], {}


def check(name, ok, msg=""):
    counts[name] = counts.get(name, 0) + 1
    if not ok: fails.append(f"{name}: {msg}")
    return ok


def sh(cmd): return subprocess.run(cmd, capture_output=True, text=True)


def cc(src, out, tmp):
    r = sh(["g++", "-m32", "-std=c++17", "-O0", "-w", "-I", INC, "-I", GEN, "-I", CMP, "-I", REPLAY, "-I", tmp, "-c", src, "-o", out]); return r.returncode == 0, r.stderr[-1500:]


def replay_echo(pl, spec, mut, sha):
    """the echo + driver of tests/l6/wrap as the implementation under replay: entry table, plan-call index per replayed record, recorded id -> plan object number"""
    src = gen_echo.Echo(pl, spec, mut).render().replace("int main(int argc, char **argv) {", "int driver_unused(int argc, char **argv) {")
    e = enc.Enc(pl, sha); e.run(); inv = {i: n for n, i in e.oid.items()}
    kmap = [c["k"] for c in pl.calls if "nested_in" not in c]; raws = {t: r for r, t in e.tid.items() if isinstance(r, int)}; ents = re.findall(r"static [^\n(]*? real_(\w+)\(", src)
    return src + '#include "d9replay_rt.h"\nextern "C" const D9Entry d9impl_entries[] = {' + "".join(f'{{"{n}", (void *)real_{n}}},' for n in ents) + "{0, 0}};\n" \
        f'extern "C" void d9impl_before_call(unsigned o, unsigned) {{ static const unsigned K[] = {{{",".join(map(str, kmap))}}}; g_k = K[o]; }}\n' \
        f'extern "C" uintptr_t d9impl_ohandle(unsigned t) {{ switch (t) {{ {"".join(f"case {t}: return {r}u;" for t, r in raws.items())} default: return 0x70000000u + t * 16; }} }}\n' \
        f'extern "C" void d9impl_bind(unsigned id, void *p) {{ static const unsigned N[] = {{{",".join(str(inv.get(i, 0)) for i in range(max(inv) + 1))}}}; if (id < sizeof N / sizeof *N && N[id]) O[N[id]] = p; }}\n', kmap


def run_cmp(a, b, tmp, *opt):
    pa, pb, pj = (os.path.join(tmp, x) for x in ("a.d9s", "b.d9s", "r.json")); open(pa, "wb").write(a); open(pb, "wb").write(b)
    if os.path.exists(pj): os.remove(pj)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()): rc = d9cmp.main([pa, pb, "--json", pj, *opt])
    return rc, (json.load(open(pj)) if os.path.exists(pj) else None)


def main():
    raw = open(SPEC, "rb").read(); spec = json.loads(raw); sha = hashlib.sha256(raw).digest(); tmp = tempfile.mkdtemp(prefix="d9cmp_")
    r = sh([sys.executable, "-I", os.path.join(GEN, "gen_wrap.py"), tmp]); check("gen_wrap runs", r.returncode == 0, r.stderr[-300:])
    r = sh([sys.executable, "-I", os.path.join(GEN, "gen_replay.py"), tmp]); check("gen_replay runs", r.returncode == 0, r.stderr[-300:]); print(r.stdout.strip())
    bad = json.loads(raw); bad["methods"][5]["params"] = bad["methods"][5]["params"] + [{"name": "zz", "kind": "bogus", "dir": "in", "byref": False, "ctype": "int"}]
    open(os.path.join(tmp, "bad.json"), "w").write(json.dumps(bad)); r = sh([sys.executable, "-I", os.path.join(GEN, "gen_replay.py"), os.path.join(tmp, "bad_out"), os.path.join(tmp, "bad.json")])
    check("gen_replay fails closed, naming the item", r.returncode == 2 and "zz" in r.stderr and "bogus" in r.stderr, r.stderr[-200:])
    pl = P.Plan(spec); pl.build(); drv = gen_echo.Echo(pl, spec).render(); rp, kmap = replay_echo(pl, spec, None, sha)
    cand = None  # a det output of a replayed call to mutate in the echo: found after R1 exists, so the mutated echo is compiled in a second round
    srcs = {"drv": drv, "rp": rp}
    for n, s in srcs.items(): open(os.path.join(tmp, f"echo_{n}.cpp"), "w").write(s)
    with cf.ThreadPoolExecutor(6) as ex:
        fut = {k: ex.submit(cc, os.path.join(tmp, f), os.path.join(tmp, k + ".o"), tmp) for k, f in (("gen", "d9wrap.gen.cpp"), ("rgen", "d9replay.gen.cpp"), ("drv", "echo_drv.cpp"), ("rp", "echo_rp.cpp"))}
        fut["st"] = ex.submit(cc, os.path.join(REPLAY, "d9stream.cpp"), os.path.join(tmp, "st.o"), tmp); fut["rt"] = ex.submit(cc, os.path.join(CMP, "d9replay_rt.cpp"), os.path.join(tmp, "rt.o"), tmp)
        for k, f in fut.items(): ok, err = f.result(); check(f"compile {k}", ok, err)
    if fails: return finish(tmp)
    o = lambda *n: [os.path.join(tmp, x + ".o") for x in n]
    r = sh(["g++", "-m32", "-pthread", "-o", os.path.join(tmp, "exe_drv")] + o("gen", "st", "drv")); check("link driver", r.returncode == 0, r.stderr[-300:])
    r = sh(["g++", "-m32", "-pthread", "-o", os.path.join(tmp, "exe_rp")] + o("gen", "st", "rp", "rgen", "rt")); check("link replayer", r.returncode == 0, r.stderr[-300:])
    if fails: return finish(tmp)
    R1p = os.path.join(tmp, "R1.d9s"); check("record R1", sh([os.path.join(tmp, "exe_drv"), R1p]).returncode == 0); R1 = open(R1p, "rb").read()
    hdr, recs = d9s1.read_stream(R1, spec); d0 = [r for r in recs if r.depth == 0]
    # ---- (a) replay
    r = sh([os.path.join(tmp, "exe_rp"), R1p, os.path.join(tmp, "R2.d9s")]); print(r.stdout.strip(), r.stderr.strip()); check("replay runs", r.returncode == 0, r.stderr[-300:])
    R2 = open(os.path.join(tmp, "R2.d9s"), "rb").read(); st = dict(re.findall(r"(\w+)=(\d+)", r.stdout)); wrapped_e = sum(1 for l in open(os.path.join(tmp, "d9wrap.manifest.txt")) if l.startswith("E "))
    rc, js = run_cmp(R1, R2, tmp, "--fold-threads"); check("a: d9cmp R1 R2 equal (exit 0)", rc == 0 and js["det_mismatch"] == 0, str(js and js["first_mismatch"]))
    check("a: records compared == records in R1 (nested ones reappeared by themselves)", js["compared"] == len(recs) == js["records_b"], f"{js['compared']} {len(recs)} {js['records_b']}")
    check("a: re-issued == depth-0 records of R1", int(st["issued"]) == len(d0) and int(st["nested_skipped"]) == len(recs) - len(d0), f"{st} {len(d0)}")
    check("a: every spec method replayed (503/503)", int(st["methods"]) == len(spec["methods"]) == 503 and len({(r.iface, r.slot) for r in d0 if r.iface}) == 503, st["methods"])
    check("a: every recordable export replayed", int(st["exports"]) == wrapped_e == len({r.slot for r in d0 if not r.iface}), f"{st['exports']}/{wrapped_e}")
    print(f"replay: records {len(recs)}, depth-0 {len(d0)}, masked pix {js['masked_pix']}, env equal {js['env_equal']}, collapsed {js['collapsed']}; methods {st['methods']}/{len(spec['methods'])}, exports {st['exports']}/{wrapped_e}")
    rc, _ = run_cmp(R1, R2, tmp); check("a: without --fold-threads the 2-thread record vs the 1-thread replay is reported (thread count differs)", rc == 1)
    pa = os.path.join(tmp, "a.d9s"); r = sh([sys.executable, "-I", os.path.join(CMP, "d9cmp.py"), pa, pa]); check("cli: exit 0, prints counts", r.returncode == 0 and "RESULT: EQUAL" in r.stdout, r.stdout + r.stderr)
    check("cli: usage error is exit 2", sh([sys.executable, "-I", os.path.join(CMP, "d9cmp.py")]).returncode == 2)
    # ---- (b) comparator mutations
    pk = lambda r, it: next((p for p in r.m["params"] if p["name"] == it.label.split(":")[1]), None) if it.label[:3] in ("in:", "out") else None
    def find(pred, recs_=recs):
        for r in recs_:
            for it in r.items:
                if pred(r, it): return r, it
        return None, None
    def patched(off, f=lambda b: b ^ 1, n=1):
        d = bytearray(R1)
        for i in range(n): d[off + i] = f(d[off + i])
        return bytes(d)
    def chunks(rs): return [R1[r.off:r.off + r.len] for r in rs]
    def build(cs):
        d = bytearray(R1[:44])
        for i, c in enumerate(cs): c = bytearray(c); struct.pack_into("<I", c, 4, i); d += c
        return bytes(d)
    def expect(name, rec, it_pred, mutated, label=None, opt=()):
        rc, js = run_cmp(R1, mutated, tmp, *opt); fm = js and js["first_mismatch"]
        check(f"b: mutation {name} -> exit 1 at the record", rc == 1 and fm and fm["seq_a"] == rec.seq and fm["method"] == rec.name and (label is None or label in fm["item"]), f"rc {rc} {fm} want seq {rec.seq} {rec.name} {label}")
        return js
    inp = lambda r, it: it.cls is None and it.label.startswith("in:") and not it.label.endswith((":present", ":when"))
    simple = lambda kind, ptr: (lambda r, it: inp(r, it) and (pk(r, it) or {}).get("kind") == kind and bool((pk(r, it) or {}).get("byref")) == ptr and it.data and not r.depth and r.slot != 0xFFFF)
    for name, pred, mut in (
        ("value", simple("value", False), lambda r, it: patched(it.off)), ("enum", simple("enum", False), lambda r, it: patched(it.off)),
        ("handle", lambda r, it: it.kind == "obj" and it.label.startswith("in:") and struct.unpack("<I", it.data)[0], lambda r, it: patched(it.off, lambda b: b ^ 0x40, 1) if False else R1[:it.off] + struct.pack("<I", struct.unpack("<I", it.data)[0] + 1000) + R1[it.off + 4:]),
        ("struct field", lambda r, it: inp(r, it) and (pk(r, it) or {}).get("kind") == "struct" and len(it.data) > 8 and len(set(range(len(it.data))) - {x + k for x in it.idpos for k in range(4)}) > 0, lambda r, it: patched(it.off + min(set(range(len(it.data))) - {x + k for x in it.idpos for k in range(4)}))),
        ("buffer byte", lambda r, it: inp(r, it) and (pk(r, it) or {}).get("kind") == "buffer" and len(it.data) >= 4, lambda r, it: patched(it.off)),
        ("return", lambda r, it: it.label == "ret" and it.cls == 0 and it.kind == "raw", lambda r, it: patched(it.off)),
        ("lock bytes", lambda r, it: it.label.startswith("lock:") and it.label.endswith(":data") and it.cls == 0 and it.rec and len(it.data) >= 4, lambda r, it: patched(it.off)),
        ("unlock bytes", lambda r, it: it.label == "close:data" and it.cls == 0 and it.rec and len(it.data) >= 4, lambda r, it: patched(it.off)),
        ("new id (iface)", lambda r, it: it.label == "h:new.iface" and r.depth == 0, lambda r, it: patched(it.off)),
        ("class tag det->env", lambda r, it: it.cls == 0 and it.rec and it.hoff is not None, lambda r, it: patched(it.hoff, lambda b: 1)),
        ("class tag pix->det", lambda r, it: it.cls == 2 and it.rec and it.hoff is not None, lambda r, it: patched(it.hoff, lambda b: 0))):
        r, it = find(pred); check(f"b: candidate for {name}", r is not None)
        if r is not None: expect(name, r, None, mut(r, it), None if name.startswith(("class", "new")) else it.label)
    r, it = find(lambda r, it: inp(r, it) and (pk(r, it) or {}).get("kind") == "buffer" and len(it.data) >= 8 and len(it.data) % 4 == 0 and "canon" not in pk(r, it))
    d = bytearray(R1[:it.off - 4]) + struct.pack("<I", len(it.data) - 4) + R1[it.off:it.off + len(it.data) - 4] + R1[it.off + len(it.data):]; struct.pack_into("<I", d, r.off, r.len - 4)
    d = bytes(d); expect("buffer length", r, None, d, it.label)
    rr = [x for x in recs if x.retired and x.depth == 0]; x1, x2 = rr[-3], rr[-2]; i1, i2 = [next(i for i in x.items if i.label == "h:ret.id") for x in (x1, x2)]; d = bytearray(R1); d[i1.off:i1.off + 4], d[i2.off:i2.off + 4] = i2.data, i1.data
    expect("retired id", x1, None, bytes(d), "h:ret.id")
    plain = lambda r: r.depth == 0 and not r.new and not r.retired and r.thread == 0 and all(i.kind == "raw" or i.cls is None for i in r.items)
    ks = [i for i in range(len(recs) - 3) if all(plain(recs[i + j]) for j in range(3)) and recs[i].m is not recs[i + 1].m and recs[i + 1].m is not recs[i + 2].m]; k = ks[len(ks) // 2]; cs = chunks(recs)
    expect("record order swap", recs[k], None, build(cs[:k] + [cs[k + 1], cs[k]] + cs[k + 2:]))
    expect("missing record", recs[k], None, build(cs[:k] + cs[k + 1:]))
    js = expect("extra record", recs[k + 1], None, build(cs[:k] + [cs[k]] + cs[k:]))
    r, it = find(lambda r, it: it.cls == 2 and it.rec and it.data and len(it.data) >= 4); check("b: candidate for pix", r is not None)
    base_masked = run_cmp(R1, R1, tmp)[1]["masked_pix"]; rc, js = run_cmp(R1, patched(it.off), tmp); check("b: mutation inside a pix item is masked (exit 0, same masked count)", rc == 0 and js["masked_pix"] == base_masked and js["det_mismatch"] == 0, str(js and js["first_mismatch"]))
    r, it = find(lambda r, it: it.cls == 1 and it.rec and it.data and not r.depth); check("b: candidate for env", r is not None)
    rc, js = run_cmp(R1, patched(it.off), tmp); check("b: env mutation -> env_diff, exit 0", rc == 0 and js["env_diff"] == 1 and js["first_env_diff"]["seq_a"] == r.seq and js["first_env_diff"]["item"] == it.label, str(js and js["env_diffs"]))
    ckey = lambda r: b"".join(i.data for i in r.items if i.cls is None and i.data and not i.label.startswith("h:")).hex()
    cnt = {}
    for x in recs:
        for i in x.items:
            if i.cls == 1: cnt[(x.name, i.label, ckey(x))] = cnt.get((x.name, i.label, ckey(x)), 0) + 1
    r, it = find(lambda r, it: it.cls == 1 and it.rec and it.data and not r.depth and cnt[(r.name, it.label, ckey(r))] == 1); check("b: candidate for profile", r is not None)
    tsv = os.path.join(tmp, "env.tsv"); open(tsv, "w").write(f"{r.name}\t{it.label}\t{it.data.hex()}\t{ckey(r)}\n"); rc, js = run_cmp(R1, patched(it.off), tmp, "--profile", tsv)
    check("b: --profile (keyed row): A matches the profile, mutated B does not", js["env_profile_diff_a"] == 0 and js["env_profile_diff_b"] == 1 and js["env_profile_checked"] == 2, str(js))
    bad = bytearray(R1); bad[10] ^= 1; rc, _ = run_cmp(R1, bytes(bad), tmp); check("spec-hash mismatch -> exit 2 (incomparable)", rc == 2); rc, _ = run_cmp(R1, R1[:-3], tmp); check("truncated stream -> exit 2", rc == 2)
    r, it = find(lambda r, it: it.cls == 0 and it.rec and it.hoff is not None and r.depth == 0 and not r.retired and not r.new and any(j.cls == 1 and j.rec for j in r.items)) ; 
    if r is not None:        # env diff followed by a det mismatch two records later: "after"; a det mismatch before an env diff: "before"
        e1 = next(j for j in r.items if j.cls == 1 and j.rec and j.data); m1 = next(j for j in recs[r.seq + 1].items if j.cls is None and j.label.startswith("in:") and j.data and j.kind == "raw" and not j.label.endswith((":present", ":when")))
        rc, js = run_cmp(R1, patched(m1.off) if False else bytes(bytearray(R1[:e1.off]) + bytes([R1[e1.off] ^ 1]) + R1[e1.off + 1:m1.off] + bytes([R1[m1.off] ^ 1]) + R1[m1.off + 1:]), tmp)
        check("b: env diff then det mismatch: reports 'after'", rc == 1 and js["first_mismatch_vs_env_diff"] == "after" and js["env_diff"] >= 1, str(js and js["first_mismatch_vs_env_diff"]))
    # ---- (d) polling collapse
    poll = next((r for r in recs if r.depth == 0 and d9cmp.collapsible(r)), None); check("d: a polling record (GetData-like) exists in R1", poll is not None)
    if poll:
        pc = R1[poll.off:poll.off + poll.len]; rep = lambda n: build([pc] * n)
        for a, b in ((3, 5), (1, 4), (6, 6)):
            rc, js = run_cmp(rep(a), rep(b), tmp); check(f"d: {a} vs {b} repeats of a pix-return poll equal, collapsed counted", rc == 0 and js["collapsed"] == a + b - 2 and js["compared"] == 1, str(js and (js["collapsed"], js["first_mismatch"])))
        it = next(i for i in poll.items if i.label.startswith("in:") and i.cls is None and i.data and i.kind == "raw" and not i.label.endswith((":present", ":when"))); alt = bytearray(pc); alt[it.off - poll.off] ^= 1
        rc, js = run_cmp(rep(3), build([pc] * 2 + [bytes(alt)]), tmp); check("d: a repeat with different inputs is not collapsed (mismatch)", rc == 1, str(js))
        sret = bytearray(pc); ri = next(i for i in poll.items if i.label == "ret"); sret[ri.off - poll.off] ^= 1; rc, js = run_cmp(rep(3), build([pc, pc, bytes(sret), pc]), tmp)
        check("d: polls ending with another pix return (S_FALSE .. S_OK) compare equal", rc == 0 and js["masked_pix"] >= 1, str(js and js["first_mismatch"]))
    nonp = next(r for r in recs if plain(r) and not d9cmp.collapsible(r)); nc = R1[nonp.off:nonp.off + nonp.len]
    rc, js = run_cmp(build([nc] * 2), build([nc] * 3), tmp); check("d: repeats of a call that is not a pix-return poll are NOT collapsed", rc == 1 and js["collapsed"] == 0, str(js))
    # ---- (e) threads
    ks4 = [i for i in range(len(recs) - 4) if all(plain(recs[i + j]) for j in range(4))]; k4 = ks4[len(ks4) // 2]; a, b, c, dd = cs[k4:k4 + 4]
    def th(x, t): x = bytearray(x); struct.pack_into("<I", x, 8, t); return bytes(x)
    S1, S2 = build([a, th(b, 1), c, th(dd, 1)]), build([a, c, th(b, 1), th(dd, 1)])
    rc, js = run_cmp(S1, S2, tmp); check("e: other interleaving, equal per-thread sequences -> equal, interleave_swaps counted", rc == 0 and js["interleave_swaps"] == 1 and js["det_mismatch"] == 0 and js["threads_a"] == 2, str(js))
    rc, js = run_cmp(S1, build([c, th(b, 1), a, th(dd, 1)]), tmp); check("e: per-thread order differs -> mismatch", rc == 1, str(js))
    rc, js = run_cmp(S1, build([a, th(b, 1), th(dd, 1)]), tmp); check("e: a thread with a missing record -> mismatch", rc == 1, str(js))
    mk = [i for i in range(len(recs) - 1) if all(recs[i + j].depth == 0 and len(recs[i + j].new) == 1 and not recs[i + j].retired and not any(x.kind == "tok" and x.data for x in recs[i + j].items) for j in (0, 1))
          and recs[i].new[0][0] + 1 == recs[i + 1].new[0][0] and not any(x.kind == "obj" and x.label != "h:new.id" and struct.unpack("<I", x.data)[0] == recs[i].new[0][0] for x in recs[i + 1].items)]
    check("e: two independent object-creating records exist", bool(mk))
    if mk:
        i = mk[0]; x, y = recs[i], recs[i + 1]; ia, ib = x.new[0][0], y.new[0][0]
        def ren(r, old, new):
            z = bytearray(R1[r.off:r.off + r.len])
            for it in r.items:
                if it.kind == "obj" and it.data and struct.unpack("<I", it.data)[0] == old: struct.pack_into("<I", z, it.off - r.off, new)
            return bytes(z)
        pre = cs[:i]; S1 = build(pre + [th(cs[i], 0), th(cs[i + 1], 1)]); S2 = build(pre + [th(ren(y, ib, ia), 1), th(ren(x, ia, ib), 0)])
        rc, js = run_cmp(S1, S2, tmp); check("e: other interleaving that renumbers object ids (first-appearance order) -> equal, ids renamed counted", rc == 0 and js["interleave_swaps"] == 1 and js["renamed_ids"] == 2, str(js and (js["first_mismatch"], js["renamed_ids"])))
        S3 = build(pre + [th(ren(y, ib, ia), 0), th(ren(x, ia, ib), 0)]); rc, js = run_cmp(S1, S3, tmp); check("e: the same swap inside ONE thread is a mismatch", rc == 1, str(js))
    # ---- (c) replay mutation: one det output of one replayed call changed in the echo
    d0o = {id(r): j for j, r in enumerate(d0)}; tgt = None
    for j, r in enumerate(d0):
        c = pl.calls[kmap[j]]
        if r.iface and c["hr"] == 0 and (c["iface"], c["m"]["name"]) not in P.PROF and not r.retired and any(i.label.startswith("out:") and i.cls == 0 and i.rec and i.data and pk(r, i)["kind"] in ("value", "struct", "buffer") for i in r.items): tgt = (j, r, c); break
    check("c: candidate (a det output of a replayed call)", tgt is not None)
    if tgt:
        j, r, c = tgt; src, _ = replay_echo(pl, spec, ("out", c["k"]), sha); open(os.path.join(tmp, "echo_rpm.cpp"), "w").write(src)
        ok, err = cc(os.path.join(tmp, "echo_rpm.cpp"), os.path.join(tmp, "rpm.o"), tmp); check("compile mutated echo", ok, err)
        r2 = sh(["g++", "-m32", "-pthread", "-o", os.path.join(tmp, "exe_rpm")] + o("gen", "st", "rpm", "rgen", "rt")); check("link mutated replayer", r2.returncode == 0, r2.stderr[-300:])
        if not fails: 
            check("c: replay against the mutated echo runs", sh([os.path.join(tmp, "exe_rpm"), R1p, os.path.join(tmp, "R3.d9s")]).returncode == 0)
            rc, js = run_cmp(R1, open(os.path.join(tmp, "R3.d9s"), "rb").read(), tmp, "--fold-threads"); fm = js["first_mismatch"]
            check("c: G-REPLAY reports exactly that method (one mismatch, at that record)", rc == 1 and js["det_mismatch"] == 1 and fm["method"] == r.name and fm["seq_a"] == r.seq and fm["item"].startswith("out:"), str(js and (js["det_mismatch"], fm)))
            print(f"replay mutation: call k={c['k']} {r.name} seq {r.seq} -> reported {fm and (fm['method'], fm['seq_a'], fm['item'])}")
    finish(tmp)


def finish(tmp):
    if not os.environ.get("D9CMP_KEEP"): shutil.rmtree(tmp, ignore_errors=True)
    print("checks: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) if len(counts) < 6 else f"checks: {sum(counts.values())} in {len(counts)} groups")
    for f in fails[:40]: print("FAIL", f[:600])
    print(f"RESULT: {'FAIL' if fails else 'PASS'} ({len(fails)} failures, {sum(counts.values())} checks)"); sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
