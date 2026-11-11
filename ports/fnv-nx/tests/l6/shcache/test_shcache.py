#!/usr/bin/env python3
"""test_shcache.py: tests of render/shaders/cache (R1c): the shader cache format + loader (shcache.c, LP64 and ILP32), the builder (mkshcache.py), the D9S1
shader extractor (d9s_shaders.py) and the link-set key rule.  python3 -I tests/l6/shcache/test_shcache.py [--no-mutations]   Needs no game data.
 a format round trip + lookup of every key + misses   b miss + put + reopen, torn log at every length, every byte flipped (log, main), truncated main, wrong build id
 c builder determinism (shuffled input, other hash seed), hash functions vs C, key canonicalisation   d d9s_shaders.py on a synthetic D9S1 stream
 e link-set rule vs fnvnx_dk_assign (exhaustive singletons + 30000 random sets, both stages), reflection JSON pairs   f pipeline on synthetic shaders (only when the
 translator is built: FNVNX_SHCACHE_BUILD=dir with libfnvnx_shaders.a and deps)   g mutation checks (each must make parts a-e or d fail)."""
import importlib.util, json, os, random, shutil, subprocess, sys, tempfile, concurrent.futures as cf
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(); import mutsrc
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CACHE = os.path.join(ROOT, "render", "shaders", "cache"); SHD = os.path.join(ROOT, "render", "shaders")
BID = bytes(range(16)).hex(); BID2 = bytes(range(1, 17)).hex()


def load(path, name):
    sp = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m); return m


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


class Suite:
    def __init__(s, cdir, tmp, exes, mk, d9s, sy):
        s.cdir, s.tmp, s.exes, s.mk, s.d9s, s.sy, s.res = cdir, tmp, exes, mk, d9s, sy, {}; s.part = "?"; s.msgs = []

    def check(s, ok, msg=""):
        c = s.res.setdefault(s.part, [0, 0]); c[0] += 1
        if not ok: c[1] += 1; s.msgs.append(f"{s.part}: {msg}")
        return ok

    def run(s, exe, *a): return sh([exe, *map(str, a)]).stdout.split("\n")

    def entries(s, n=40, seed=1):
        r = random.Random(seed); es = []; seen = set()
        while len(es) < n:
            nl = r.choice([0, 0, 1, 2, 5]); l = bytes(sorted(r.sample(range(224), nl)))
            h = r.choice([r.getrandbits(64), 0xAB, 0xAB, 0xAB]); key = (h, r.choice([10, 20]), r.randint(0, 1), r.choice([0, 4, 8, 12, 0x3C]), l)
            if (key[0], key[2], key[3], s.mk.l_hash(l), key[1]) in seen: continue
            seen.add((key[0], key[2], key[3], s.mk.l_hash(l), key[1])); es.append(key + (bytes(r.getrandbits(8) for _ in range(r.choice([0, 1, 7, 64, 300]))),))
        return es

    def rows(s, es, miss=False):
        out = []
        for h, n, st, fl, l, p in es:
            lx = l.hex() or "-"
            out.append(f"{h:x} {n} {st} {fl:x} {lx} {'MISS' if miss else p.hex() or '-'}")
        return "\n".join(out) + "\n"

    def wr(s, name, data):
        p = os.path.join(s.tmp, name); open(p, "wb" if isinstance(data, bytes) else "w").write(data); return p

    def lookup(s, exe, main, log, rows):
        o = s.run(exe, "lookup", main, log or "-", BID, s.wr("rows.tsv", rows))[0]; a = o.split()
        return len(a) == 4 and int(a[3]) == 0 and int(a[1]) > 0

    # ---------------------------------------------------------------- a
    def part_a(s):
        s.part = "a"; es = s.entries(); data = s.mk.build(es, bytes.fromhex(BID)); main = s.wr("main.shc", data)
        bid, back = s.mk.parse(data); s.check(bid.hex() == BID and sorted(back) == sorted(es), "builder -> parse round trip")
        for exe in s.exes:
            s.check(s.lookup(exe, main, None, s.rows(es)), f"{exe}: lookup of every key")
            mis = [(h ^ 1, n, st, fl, l, p) for h, n, st, fl, l, p in es] + [(h, n + 1, st, fl, l, p) for h, n, st, fl, l, p in es] + [(h, n, 1 - st, fl, l, p) for h, n, st, fl, l, p in es]
            mis += [(h, n, st, fl ^ 4, l, p) for h, n, st, fl, l, p in es] + [(h, n, st, fl, bytes(sorted(set(l) | {0xDF})), p) for h, n, st, fl, l, p in es]
            have = {(h, n, st, fl, l) for h, n, st, fl, l, _ in es}; mis = [e for e in mis if e[:5] not in have]
            s.check(len(mis) > 100 and s.lookup(exe, main, None, s.rows(mis, True)), f"{exe}: {len(mis)} near-miss keys all miss")
            s.check(s.run(exe, "info", main, "-", BID)[0].split()[:2] == ["1", str(len(es))], f"{exe}: info")
        empty = s.mk.build([], bytes.fromhex(BID)); s.check(s.run(s.exes[0], "info", s.wr("e.shc", empty), "-", BID)[0].split()[:2] == ["1", "0"], "empty cache is valid")
        s.check(s.mk.parse(s.mk.build([(1, 2, 0, 0, b"", b"x")] * 1, bytes(16)))[1][0][5] == b"x", "single entry")
        for bad in ([(1, 2, 0, 0, b"", b"a")] * 2, [(1, 2, 2, 0, b"", b"a")], [(1, 2, 0, 1, b"", b"a")], [(1, 2, 0, 0, bytes([224]), b"a")], [(1, 2, 0, 0, bytes([5, 5]), b"a")]):
            try: s.mk.build(bad, bytes(16)); s.check(False, "builder accepted an illegal entry")
            except ValueError: s.check(True)
        s.es, s.main = es, main

    # ---------------------------------------------------------------- b
    def part_b(s):
        s.part = "b"; es, main = s.es, s.main
        for exe in s.exes:
            log = os.path.join(s.tmp, "log.bin")
            if os.path.exists(log): os.remove(log)
            new = [(0x1111 + i, 33, i & 1, 0, bytes([4 * 16 + 1] * (i % 2)), bytes([i] * (5 + i))) for i in range(4)]
            for e in new[:3]: s.check(s.run(exe, "put", main, log, BID, f"{e[0]:x}", e[1], e[2], f"{e[3]:x}", e[4].hex() or "-", e[5].hex())[0] == "1", "put of a new key stores")
            data = open(log, "rb").read(); s.check(data[:4] == b"FNSL" and data[8:24] == bytes.fromhex(BID), "log header")
            s.check(s.run(exe, "put", main, log, BID, f"{new[0][0]:x}", new[0][1], new[0][2], "0", new[0][4].hex() or "-", "00")[0] == "0" and open(log, "rb").read() == data, "put of a present key: 0, log unchanged")
            h, n, st, fl, l, p = es[0]; s.check(s.run(exe, "put", main, log, BID, f"{h:x}", n, st, f"{fl:x}", l.hex() or "-", "00")[0] == "0" and open(log, "rb").read() == data, "put of a key of the main file: 0")
            s.check(s.lookup(exe, main, log, s.rows(es + new[:3])), "reopen: main and log entries hit"); s.check(s.lookup(exe, main, log, s.rows(new[3:], True)), "not-put key misses")
            ends, pos = [], 24                                    # record ends of the log, from the encoder of the format
            for e in new[:3]: pos += 30 + len(e[4]) + len(e[5]); ends.append(pos)
            s.check(pos == len(data), "log record sizes follow the format")
            t = [list(map(int, x.split())) for x in s.run(exe, "truncs", main, log, BID)[:-1]]
            s.check(len(t) == len(data) + 1 and all(e == sum(1 for x in ends if x <= k) and v == (0 if k < 24 else max([24] + [x for x in ends if x <= k])) for k, e, v in t), "torn log at every length: entries and valid prefix")
            f = [list(map(int, x.split())) for x in s.run(exe, "logflips", main, log, BID)[:-1]]
            s.check(len(f) == len(data) and all(e == (0 if k < 24 else sum(1 for x in ends if x <= k)) for k, e, v in f), "every flipped log byte drops its record and the rest")
            s.run(exe, "put", main, log, BID, "2222", 1, 0, "0", "-", "aa"); torn = open(log, "rb").read(); open(log, "wb").write(torn + b"\x07garbage-tail")
            s.check(s.run(exe, "put", main, log, BID, "3333", 1, 0, "0", "-", "bb")[0] == "1" and s.lookup(exe, main, log, "2222 1 0 0 - aa\n3333 1 0 0 - bb\n"), "put after a torn tail truncates it first")
            o = s.run(exe, "flips", main, BID); s.check(o[0] == "0 1" and o[1] == "nobid 1" and o[2] == "prefixes 0", f"every flipped byte / proper prefix of the main file is rejected ({o[:3]})")
            s.check(s.run(exe, "info", main, "-", BID2)[0].split()[0] == "0", "other build id: empty"); s.check(s.run(exe, "info", main, log, BID2)[0].split()[:3] == ["0", "0", "0"], "other build id: log empty too")
            s.check(s.run(exe, "info", s.wr("junk.shc", b"FNSC" + bytes(60)), log, BID)[0].split()[0] == "0", "bad body: empty")
            s.check(s.run(exe, "info", s.wr("zero.shc", b""), log, BID)[0].split()[:3] == ["0", "0", "5"], "empty main file, the log still loads")
            s.check(s.lookup(exe, s.wr("junk2.shc", b"x" * 500), None, s.rows(es[:3], True)), "junk main file: every lookup misses")
            v2 = bytearray(open(main, "rb").read()); v2[4] = 2; s.check(s.run(exe, "info", s.wr("v2.shc", bytes(v2)), "-", BID)[0].split()[0] == "0", "version mismatch: empty")

    # ---------------------------------------------------------------- c
    def part_c(s):
        s.part = "c"; es = s.entries(30, 2); a = s.mk.build(es, bytes(16)); r = random.Random(5); sh_ = es[:]; r.shuffle(sh_)
        s.check(a == s.mk.build(sh_, bytes(16)), "same entries, other order: byte-identical"); s.check(a != s.mk.build(es, bytes([1] * 16)), "build id is part of the file")
        s.check(a != s.mk.build(es[:-1], bytes(16)), "different entries differ")
        code = f"import importlib.util as u;sp=u.spec_from_file_location('m','{CACHE}/mkshcache.py');m=u.module_from_spec(sp);sp.loader.exec_module(m);import sys;sys.stdout.buffer.write(m.build({es!r},bytes(16)))"
        o = [subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, env={"PYTHONHASHSEED": str(k)}).stdout for k in (1, 77)]
        s.check(o[0] == o[1] == a, "two processes, other hash seeds: byte-identical")
        d = os.path.join(s.tmp, "tr"); os.makedirs(d)
        with open(os.path.join(d, "entries.tsv"), "w") as f:
            f.write("id\n")
            for i, (h, n, st, fl, l, p) in enumerate(es[:6]):
                f.write(f"e{i}\t{h:016x}\t{n}\t{'v' if st == 0 else 'p'}\t{fl:x}\t{l.hex() or '-'}\n"); open(os.path.join(d, f"e{i}.dksh"), "wb").write(p[:3]); open(os.path.join(d, f"e{i}.json"), "wb").write(p[3:])
        out = os.path.join(s.tmp, "cli.shc"); r1 = sh(["python3", "-I", os.path.join(CACHE, "mkshcache.py"), "--build-id", BID, d, out])
        s.check(r1.returncode == 0 and open(out, "rb").read() == s.mk.build([(h, n, st, fl, l, s.mk.pack_payload(p[:3], p[3:])) for h, n, st, fl, l, p in es[:6]], bytes.fromhex(BID)), "mkshcache CLI from a translated directory")
        for exe in s.exes:
            for data in (b"", b"abc", bytes(range(256)), b"\xff" * 33):
                h, lh = s.run(exe, "hash", data.hex())[0].split(); s.check(int(h, 16) == s.mk.fnv1a64(data) and int(lh, 16) == s.mk.l_hash(data), "FNV-1a / l_hash equal in C and python")
            s.check(int(s.run(exe, "hash", "")[0].split()[0], 16) == 0xCBF29CE484222325, "FNV offset basis"); s.check(s.run(exe, "hash", b"a".hex())[0].split()[0] == "af63dc4c8601ec8c", "FNV-1a 64 of 'a' (published vector)")
            s.check(s.run(exe, "keymake", "ab", 5, 1, "ffff", "5a0a")[0] == "2 0a5a 65532", "key_make sorts L and masks flags (only KEEP_GLSL|NO_DKSH are not in the key)")
            # the key covers every translator input: one flag bit set -> a different key, for EVERY bit but the two declared non-key ones (FLAT_COLOR and VS_COMPLETE among them)
            base = s.run(exe, "keymake", "ab", 5, 0, "0", "-")[0]
            for b in range(32):
                got = s.run(exe, "keymake", "ab", 5, 0, "%x" % (1 << b), "-")[0]
                s.check((got != base) == (b >= 2), f"flag bit {b} {'is' if b >= 2 else 'is not'} part of the key")
            s.check(s.run(exe, "keymake", "ab", 5, 0, "80", "-")[0] == "0  128", "FNVNX_SHADER_FLAT_COLOR (bit 7) is kept in the key")
            s.check(s.run(exe, "keymake", "ab", 5, 0, "40", "-")[0] == "0  64", "FNVNX_SHADER_VS_COMPLETE (bit 6) is kept in the key")
            for bad in ("0a0a", "e0", "00" * 32): s.check(s.run(exe, "keymake", "ab", 5, 1, 0, bad)[0] == "ERR", f"key_make rejects {bad[:6]}")
            s.check(s.run(exe, "keymake", "ab", 5, 2, 0, "-")[0] == "ERR", "stage 2 rejected")
            s.check(s.run(exe, "payload")[0].endswith("FAIL 0"), "payload container + failing sink stores nothing")

    # ---------------------------------------------------------------- d
    def part_d(s):
        s.part = "d"; sy, D = s.sy, s.d9s; w = sy.Writer(); dev = 1
        V = [sy.shader("vs", [("position", 0), ("texcoord", i)], i) for i in range(4)]; P = [sy.shader("ps", [("texcoord", 0)], i) for i in range(4)]
        for i in range(3): w.create(dev, "vs", V[i], 10 + i, thread=i % 2)
        w.create(dev, "ps", P[0], 20); w.create(dev, "ps", P[1], 21); w.create(dev, "ps", P[1], 22); w.create(dev, "vs", V[0], 13, hr=-1)            # a duplicate bytecode, a failed create
        w.set(dev, "vs", 10); w.set(dev, "ps", 20); [w.draw(dev) for _ in range(3)]; w.present(dev)                                                    # frame 0: pair (V0,P0) x3
        w.create(dev, "vs", V[3], 14); w.create(dev, "ps", P[2], 23); w.set(dev, "vs", 11); w.draw(dev); w.draw(dev, hr=-1); w.present(dev)          # frame 1: (V1,P0), a failed draw
        w.set(dev, "vs", 0); w.draw(dev); w.set(dev, "ps", 0); w.draw(dev); w.set(dev, "vs", 12); w.draw(dev); w.set(dev, "ps", 22); w.set(dev, "vs", 0); w.draw(dev)  # ffp both, ffp ps, ffp vs
        w.set(dev, "vs", 12, hr=-1); w.draw(dev)                                                                                                      # failed Set: vs stays NULL, still ffp vs
        w.begin_sb(dev); w.set(dev, "vs", 12); w.set(dev, "ps", 23); w.end_sb(dev, 30); w.draw(dev); w.sb("Apply", 30); w.draw(dev); w.present(dev)     # recording does not bind; Apply does: (V2,P2)
        w.set(dev, "vs", 14); w.create_sb(dev, 3, 31); w.set(dev, "vs", 10); w.sb("Apply", 31); w.draw(dev)                                          # VERTEXSTATE block captured vs=14: (V3,P2)
        w.sb("Capture", 31); w.set(dev, "vs", 11); w.sb("Apply", 31); w.set(dev, "ps", 21); w.draw(dev)                                           # Capture refreshed to 14 again: (V3,P1)
        w.reset(dev); w.draw(dev); w.set(2, "vs", 11); w.set(2, "ps", 20); w.draw(2); w.draw(dev); w.present(dev); w.present(2, depth=1)           # Reset -> ffp both; device 2 has its own state
        data = bytes(w.out); sc = D.scan(data, w.spec, __import__("hashlib").sha256(open(sy.SPEC_PATH, "rb").read()).hexdigest()); st, shd, pairs, fc = sc
        H = {k: D.fnv1a64(b) for k, b in [("V0", V[0]), ("V1", V[1]), ("V2", V[2]), ("V3", V[3]), ("P0", P[0]), ("P1", P[1]), ("P2", P[2])]}
        s.check(st["distinct_vs"] == 4 and st["distinct_ps"] == 3 and set(shd) == set(H.values()), "distinct shaders by hash")
        s.check(st["counters"]["creates_vs"] == 4 and st["counters"]["creates_ps"] == 4 and st["counters"]["create_failed"] == 1, f"create counts {st['counters']}")
        s.check(st["frames"] == 4 and st["create_calls_per_frame"] == {0: {"vs": 3, "ps": 3}, 1: {"vs": 1, "ps": 1}}, f"per-frame creation counts {st['create_calls_per_frame']} frames {st['frames']}")
        exp = {(H["V0"], H["P0"]): 3, (H["V1"], H["P0"]): 2, (H["V2"], H["P2"]): 1, (H["V3"], H["P2"]): 1, (H["V3"], H["P1"]): 1}
        s.check({k: v[0] for k, v in pairs.items()} == exp, f"pairs {({k: v[0] for k, v in pairs.items()})}")
        c = st["counters"]; s.check((c["draws_vs_ps"], c["draws_ffp_both"], c["draws_ffp_ps"], c["draws_ffp_vs"], c["draw_failed"], c["set_failed"]) == (8, 3, 1, 4, 1, 1), f"draw classes {c}")
        s.check(st["nested_calls_by_role"] == {"present": 1} and c["stateblock_apply"] == 3 and c["stateblock_capture"] == 1 and c["stateblock_begin"] == 1 and c["stateblock_end"] == 1, f"state block + nesting stats {c}")
        o = os.path.join(s.tmp, "d9s"); sp = os.path.join(s.tmp, "x.d9s"); open(sp, "wb").write(data); r = sh(["python3", "-I", os.path.join(s.cdir, "d9s_shaders.py"), o, sp])
        s.check(r.returncode == 0 and all(open(os.path.join(o, f"{h:016x}.bin"), "rb").read() == b for h, (_, b, _) in shd.items()), "CLI writes <hash>.bin")
        rows = [l.split("\t") for l in open(os.path.join(o, "pairs.tsv")).read().splitlines()[1:]]
        s.check(sorted((int(a, 16), int(b, 16), int(n)) for a, b, n, _ in rows) == sorted((a, b, n) for (a, b), n in exp.items()) and json.load(open(os.path.join(o, "stats.json")))["distinct_pairs"] == 5, "pairs.tsv + stats.json")
        hold = D.fnv1a64; D.fnv1a64 = lambda b: 7
        try: D.scan(data, w.spec); s.check(False, "a 64-bit collision must raise")
        except D.CollisionError: s.check(True)
        D.fnv1a64 = hold
        s.check(sh(["python3", "-I", os.path.join(s.cdir, "d9s_shaders.py"), os.path.join(s.tmp, "o2"), s.wr("bad.d9s", b"D9S2" + data[4:])]).returncode == 2, "bad magic: exit 2")
        w2 = sy.Writer(); w2.set(1, "vs", 99); r = sh(["python3", "-I", os.path.join(s.cdir, "d9s_shaders.py"), os.path.join(s.tmp, "o3"), s.wr("unk.d9s", bytes(w2.out))]); s.check(r.returncode == 2 and "unknown object" in r.stderr, "binding an unknown object: exit 2")
        w3 = sy.Writer(); w3.create(1, "vs", V[0], 10); w3.create(1, "ps", V[0], 11); r = sh(["python3", "-I", os.path.join(s.cdir, "d9s_shaders.py"), os.path.join(s.tmp, "o4"), s.wr("st.d9s", bytes(w3.out))]); s.check(r.returncode == 2, "pixel shader created from vertex bytecode: exit 2")
        s.check(sh(["python3", "-I", os.path.join(s.cdir, "d9s_shaders.py"), os.path.join(s.tmp, "o5"), s.wr("tr.d9s", data[:-3])]).returncode == 2, "truncated stream: exit 2")
        s.V, s.P = V, P

    # ---------------------------------------------------------------- e
    def part_e(s):
        s.part = "e"
        for exe in s.exes:
            o = s.run(exe, "linkset"); s.check(o[0].startswith("CHECKS") and o[0].endswith("FAIL 0") and int(o[0].split()[1]) > 900000, f"{exe}: link-set rule vs fnvnx_dk_assign: {o[0]}")
            js = '{"hash": "x", "stage": "vertex", "outputs": [\n {"usage": "POSITION", "index": 0, "name": "oPos", "location": -2},\n {"usage": "TEXCOORD", "index": 3, "name": "o\\"T3", "location": 5},\n {"usage": "COLOR", "index": 2, "name": "oD2", "location": 9}],\n "inputs": [{"usage": "UNKNOWN", "index": -1, "name": "vPos", "location": -1}, {"usage": "COLOR", "index": 0, "name": "oC0", "location": 0}, {"usage": "NORMAL", "index": 1, "name": "x", "location": 20}], "ctab": [{"name": "a, \\"b\\"", "set": "float"}]}'
            p = s.wr("r.json", js); s.check(s.run(exe, "refl", p, "outputs")[0] == "0053a2", "reflection outputs -> pairs (escaped quote in a name)")
            s.check(s.run(exe, "refl", p, "inputs")[0] == "31", "reflection inputs: vPos and oC colours skipped")
            s.check(s.run(exe, "refl", s.wr("m.json", js[:60]), "outputs")[0] == "ERR" and s.run(exe, "refl", p, "nosuch")[0] == "ERR", "malformed json / missing section")
            s.check(s.run(exe, "refl", s.wr("bi.json", '{"outputs": [{"usage": "TEXCOORD", "index": 16, "name": "x"}]}'), "outputs")[0] == "ERR", "index outside 0..15 is an error")

    # ---------------------------------------------------------------- f
    def part_f(s):
        s.part = "f"; bd = os.environ.get("FNVNX_SHCACHE_BUILD"); sy = s.sy
        if not bd or not os.path.exists(os.path.join(bd, "host", "libfnvnx_shaders.a")): print("PART f: translator not built (FNVNX_SHCACHE_BUILD unset): skipped"); return
        deps = os.path.join(bd, "deps"); exe = os.path.join(s.tmp, "shxlate"); fl = ["-O1", "-std=gnu11", "-I", os.path.join(SHD, "include"), "-I", CACHE]
        r = sh(["g++", "-x", "c", *fl, os.path.join(CACHE, "shxlate.c"), os.path.join(CACHE, "shcache_key.c"), "-x", "none", os.path.join(bd, "host", "libfnvnx_shaders.a"), os.path.join(deps, "mojoshader/build/libmojoshader.a"), os.path.join(deps, "uam/build/libuam.a"), "-lm", "-lpthread", "-o", exe])
        if not s.check(r.returncode == 0, "shxlate builds: " + r.stderr[-300:]): return
        D = os.path.join(s.tmp, "syn"); os.makedirs(D); V = [sy.shader("vs", [("position", 0), ("texcoord", 0), ("color", 2)], i) for i in range(2)] + [sy.shader("vs", [("position", 0), ("texcoord", 1)], 7)]
        P = [sy.shader("ps", [("texcoord", 0), ("color", 2)], i) for i in range(2)] + [sy.shader("ps", [("texcoord", 1)], 7)]
        for b in V + P: open(os.path.join(D, f"{s.d9s.fnv1a64(b):016x}.bin"), "wb").write(b)
        pr = [(0, 0), (1, 1), (2, 2)]; open(os.path.join(D, "pairs.tsv"), "w").write("vs\tps\tdraws\tfirst_frame\n" + "".join(f"{s.d9s.fnv1a64(V[a]):016x}\t{s.d9s.fnv1a64(P[b]):016x}\t1\t0\n" for a, b in pr))
        o1, o2 = os.path.join(s.tmp, "x1"), os.path.join(s.tmp, "x2"); r1 = sh([exe, o1, D]); r2 = sh([exe, "--reverse", o2, D])
        s.check(r1.returncode == 0 and r2.returncode == 0, "shxlate: " + r1.stdout + r1.stderr[-300:] + open(os.path.join(o1, "fail.tsv")).read()[:300])
        s.check(all(open(os.path.join(o1, f), "rb").read() == open(os.path.join(o2, f), "rb").read() for f in os.listdir(o1) if f != "timing.tsv") and sorted(os.listdir(o1)) == sorted(os.listdir(o2)), "forward and reversed order: all outputs byte-equal")
        ent = [l.split("\t") for l in open(os.path.join(o1, "entries.tsv")).read().splitlines()[1:]]; links = [l.split("\t")[2] for l in open(os.path.join(o1, "links.tsv")).read().splitlines()]
        s.check(len(ent) == 6 + 4 and links.count("-") == 1 and len(links) == 3, f"6 stage-alone + 2 pairs x 2 stage variants, one canonical pair ({len(ent)} entries, links {links})")
        out = os.path.join(s.tmp, "syn.shc"); s.check(sh(["python3", "-I", os.path.join(CACHE, "mkshcache.py"), "--build-id", BID, o1, out]).returncode == 0, "mkshcache over the translation")
        rows = "".join(f"{h} {n} {0 if st == 'v' else 1} {fl} {l} {(s.mk.pack_payload(open(os.path.join(o1, i + '.dksh'), 'rb').read(), open(os.path.join(o1, i + '.json'), 'rb').read())).hex()}\n" for i, h, n, st, fl, l, *_ in ent)
        for exe_ in s.exes[:1]: s.check(s.lookup(exe_, out, None, rows), "every translated entry is found with exactly its translator bytes")

    def core(s, parts="abce"):
        for p in parts: getattr(s, "part_" + p)()


def build_c(cdir, out, m32, extra=()):
    return sh(["gcc", "-m32" if m32 else "-m64", "-std=gnu11", "-w", "-O1", "-I", cdir, os.path.join(HERE, "t_shcache.c"), os.path.join(cdir, "shcache.c"), os.path.join(cdir, "shcache_key.c"), "-o", out, *extra])


MUTS = [("shcache.c", "if (rd64(b + 40) != shcache_hash64(", "if (0 && rd64(b + 40) != shcache_hash64("), ("shcache.c", "if (build_id && memcmp(b + 8, build_id, SHCACHE_BUILD_ID_LEN)) return 0;\n    cnt", "cnt"),
        ("shcache.c", "rd64(r + 4) != rec_chk(r, len)", "0"), ("shcache.c", "pos += len; c->info.log_valid_len = pos;", "pos += len;"), ("shcache.c", "    if (shcache_lookup(c, k, &p, &m)) return 0;\n", ""),
        ("shcache.c", "if (s1 != s2) return s1 < s2 ? -1 : 1;", ""), ("shcache_key.c", "if (vs_out[i] != FNVNX_DK_PAIR(FNVNX_USAGE_POSITION, 0) && vs_out[i] != FNVNX_DK_PAIR(FNVNX_USAGE_PSIZE, 0)) {", "if (1) {"),
        ("shcache_key.c", "fnvnx_dk_varying_location(u[i] / 16, u[i] % 16) < 0", "fnvnx_dk_varying_location(u[i] / 16, u[i] % 16) < -1"), ("shcache_key.c", "else if (!strcmp(mk, \"name\"))", "else if (0)"),
        ("mkshcache.py", "es.sort(key=lambda e: e[0])", "pass"), ("mkshcache.py", "(fnv1a64(l) or 1) if l else 0", "0"), ("d9s_shaders.py", "if k in sb: setattr(devs[sb[\"dev\"]], k, sb[k])", "pass"),
        ("d9s_shaders.py", "if dv.rec is not None: dv.rec[stg] = oid", "if False: pass"), ("d9s_shaders.py", "if dv.vs and dv.ps:", "if dv.vs or dv.ps:")]


def main():
    tmp = tempfile.mkdtemp(prefix="shc_"); sys.path.insert(0, HERE); sy = load(os.path.join(HERE, "sy.py"), "sy"); mk = load(os.path.join(CACHE, "mkshcache.py"), "mk"); d9s = load(os.path.join(CACHE, "d9s_shaders.py"), "d9s")
    exes = []; os.symlink(os.path.join(SHD, "include"), os.path.join(tmp, "include"))              # mutated copies live in tmp/m<i> and include ../include
    for m32 in (False, True):
        x = os.path.join(tmp, "t_sh%d" % (32 if m32 else 64)); r = build_c(CACHE, x, m32)
        if r.returncode: print("C build failed:", r.stderr[-1500:]); return 1
        exes.append(x)
    S = Suite(CACHE, tmp, exes, mk, d9s, sy); S.core("abcd" + "e"); S.part_f(); res = S.res
    for p in sorted(res): print(f"PART {p}: {res[p][0]} checks, {res[p][1]} failed")
    fails = list(S.msgs)
    if "--no-mutations" not in sys.argv:
        def one(i):
            f, old, new = MUTS[i]; d = os.path.join(tmp, f"m{i}"); shutil.copytree(CACHE, d, ignore=shutil.ignore_patterns("__pycache__"))
            txt = open(os.path.join(d, f)).read()
            open(os.path.join(d, f), "w").write(mutsrc.apply(txt, old, new, f"{f} #{i}")); x = os.path.join(d, "t_sh64")
            if build_c(d, x, False).returncode: return i, None
            m = Suite(d, os.path.join(tmp, f"mt{i}"), [x], load(os.path.join(d, "mkshcache.py"), f"mk{i}"), load(os.path.join(d, "d9s_shaders.py"), f"d9s{i}"), sy); os.makedirs(m.tmp)
            for p in ("abcdef"[:5]):
                try: m.core(p)
                except Exception as e: m.part = p; m.check(False, repr(e))
            return i, sum(v[1] for v in m.res.values())
        with cf.ThreadPoolExecutor(2) as ex:
            for i, n in ex.map(one, range(len(MUTS))):
                ok = bool(n); print(f"mutation {i} ({MUTS[i][0]}: {MUTS[i][1][:44]!r}): {'caught (%d checks failed)' % n if ok else 'NOT CAUGHT'}")
                if not ok: fails.append(f"g: mutation {i} not caught")
        print(f"PART g: {len(MUTS)} checks, {sum(1 for f in fails if f.startswith('g:'))} failed")
    shutil.rmtree(tmp, ignore_errors=True); print("FAILED: " + "; ".join(fails[:20]) if fails else "ALL OK"); return 1 if fails else 0


if __name__ == "__main__": sys.exit(main())
