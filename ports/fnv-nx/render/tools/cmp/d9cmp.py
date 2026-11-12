#!/usr/bin/env python3
"""d9cmp.py A.d9s B.d9s [--profile env.tsv] [--max N] [--json out.json] [--fold-threads] [--spec d3d9_spec.json]
Comparator of two D9S1 call streams (G-STREAM / G-REPLAY).  Rules: README.md.  Exit 0 equal, 1 det mismatch, 2 usage / incomparable."""
import argparse, json, os, struct, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import d9s1  # noqa: E402

CLS = ("det", "env", "pix")


class Bij:
    """consistent one-to-one renaming of ids (objects or tokens) between the two streams; 0 only maps to 0"""
    def __init__(s): s.f, s.r = {}, {}

    def bind(s, a, b):
        if a == 0 or b == 0: return a == b
        if s.f.get(a, b) != b or s.r.get(b, a) != a: return False
        s.f[a], s.r[b] = b, a; return True

    def renamed(s): return sum(1 for a, b in s.f.items() if a != b)


def collapsible(r):
    """polling rule (README): ret tagged pix, no det/env payload recorded, spec has no effects/closes/locked param, no ids born or retired"""
    m = r.m
    if "effects" in m or "closes" in m or any(p["kind"] == "locked" for p in m["params"]) or r.new or r.retired: return False
    ret = [i for i in r.items if i.label == "ret"]
    if not ret or ret[0].cls != 2: return False
    return all(not (i.cls in (0, 1) and i.rec) for i in r.items if i.label != "ret")


def collapse(recs):
    """per-thread list -> (representatives, number of records removed): a maximal run of collapsible records with the same (iface, slot, this, depth, inputs) keeps its first record"""
    out, n, key = [], 0, None
    for r in recs:
        k = (r.iface, r.slot, r.this, r.depth, tuple((i.label, i.data) for i in r.items if i.cls is None and i.label[:2] != "h:" or i.label in ("h:this",))) if collapsible(r) else None
        if k is not None and k == key: n += 1; continue
        out.append(r); key = k
    return out, n


def first_diff(a, b):
    for j in range(min(len(a), len(b))):
        if a[j] != b[j]: return j
    return min(len(a), len(b))


class Cmp:
    def __init__(s, profile, maxm):
        s.bij = {"obj": Bij(), "tok": Bij()}; s.maxm, s.profile = maxm, profile
        s.n = dict(compared=0, equal=0, masked_pix=0, env_equal=0, env_diff=0, env_profile_checked=0, env_profile_diff_a=0, env_profile_diff_b=0, det_mismatch=0)
        s.mism, s.envd = [], []

    def same(s, x, y):
        """-> (equal, byte index of the first difference)"""
        if x.rec != y.rec: return False, 0
        if x.data is None or y.data is None: return x.data is y.data, 0
        if len(x.data) != len(y.data): return False, first_diff(x.data, y.data)
        if x.kind in ("obj", "tok"):
            return s.bij[x.kind].bind(*(struct.unpack("<I", d)[0] for d in (x.data, y.data))), 0
        pos = set()
        for p in x.idpos: pos |= {p, p + 1, p + 2, p + 3}
        for j in range(len(x.data)):
            if j not in pos and x.data[j] != y.data[j]: return False, j
        for p in x.idpos:
            if not s.bij["tok"].bind(*(struct.unpack_from("<I", d, p)[0] for d in (x.data, y.data))): return False, p
        return True, 0

    def mismatch(s, ra, rb, label, why, x=None, y=None, j=0):
        s.n["det_mismatch"] += 1
        if len(s.mism) >= s.maxm: return
        win = lambda it: (it.data[(j & ~3):(j & ~3) + 32].hex() if it is not None and it.data is not None else None)
        s.mism.append(dict(seq_a=ra.seq if ra else None, seq_b=rb.seq if rb else None, thread=(ra or rb).thread, method=(ra or rb).name, item=label, why=why, byte=j,
                           off_a=(x.off + j if x is not None else None), off_b=(y.off + j if y is not None else None), win_a=win(x), win_b=win(y)))

    def record(s, ra, rb):
        s.n["compared"] += 1; bad = None
        if ra.iface != rb.iface or ra.slot != rb.slot: return s.mismatch(ra, rb, "h:iface/slot", "different method", ra.items[0], rb.items[0])
        ia, ib = ra.items, rb.items
        for k in range(max(len(ia), len(ib))):
            if k >= len(ia) or k >= len(ib):
                if not bad: bad = ("item count", (ia[k] if k < len(ia) else ib[k]).label, None, None, 0)
                break
            x, y = ia[k], ib[k]
            if x.label != y.label:
                if not bad: bad = ("item sequence differs", f"{x.label} / {y.label}", x, y, 0)
                break
            if x.cls is not None and y.cls is not None:
                if x.cls != y.cls:
                    if not bad: bad = (f"class tag {CLS[x.cls]} / {CLS[y.cls]}", x.label, x, y, 0)
                    continue
                if x.cls == 2: s.n["masked_pix"] += 1; continue
                if x.cls == 1:
                    s.profile_check(ra, x, "a"); s.profile_check(rb, y, "b")
                    ok, j = s.same(x, y)
                    if ok: s.n["env_equal"] += 1
                    else:
                        s.n["env_diff"] += 1
                        if len(s.envd) < 20 or not s.envd: s.envd.append(dict(seq_a=ra.seq, method=ra.name, item=x.label, a=x.data.hex() if x.data is not None else None, b=y.data.hex() if y.data is not None else None))
                        s.first_env = getattr(s, "first_env", ra.seq)
                    continue
            ok, j = s.same(x, y)
            if not ok and not bad: bad = ("value", x.label, x, y, j)
        if bad: s.mismatch(ra, rb, bad[1], bad[0], bad[2], bad[3], bad[4])
        else: s.n["equal"] += 1

    def profile_check(s, r, it, side):
        rows = s.profile.get((r.name, it.label)) if s.profile else None
        if not rows: return
        ck = b"".join(i.data for i in r.items if i.cls is None and i.data and not i.label.startswith("h:")).hex()      # the call's inputs: the key of a keyed row
        for k, v in rows:
            if k in (None, ck):
                s.n["env_profile_checked"] += 1
                if (it.data.hex() if it.data is not None else "") != v: s.n["env_profile_diff_" + side] += 1


def by_thread(recs, fold):
    d = {}
    for r in recs: d.setdefault(0 if fold else r.thread, []).append(r)
    return d


def compare(ra, rb, profile=None, maxm=1, fold=False):
    ta, tb = by_thread(ra, fold), by_thread(rb, fold); c = Cmp(profile, maxm); collapsed = 0
    for t in ta: ta[t], n = collapse(ta[t]); collapsed += n
    for t in tb: tb[t], n = collapse(tb[t]); collapsed += n
    pos = {t: 0 for t in tb}; pairs = []; order = sorted((r.seq, t, r) for t in ta for r in ta[t])
    first_key = None
    for _, t, r in order:
        i = pos.get(t, 0)
        if t not in tb or i >= len(tb[t]):
            c.n["det_mismatch"] += 1; c.mism.append(dict(seq_a=r.seq, seq_b=None, thread=t, method=r.name, item="record", why="missing record in B")) if len(c.mism) < c.maxm else None
        else:
            pairs.append((r.seq, tb[t][i].seq)); before = c.n["det_mismatch"]; c.record(r, tb[t][i]); pos[t] = i + 1
        if first_key is None and c.n["det_mismatch"]: first_key = r.seq
    for t in tb:
        for r in tb[t][pos[t]:]:
            c.n["det_mismatch"] += 1
            if len(c.mism) < c.maxm: c.mism.append(dict(seq_a=None, seq_b=r.seq, thread=t, method=r.name, item="record", why="extra record in B"))
            if first_key is None: first_key = 10 ** 9
    swaps = sum(1 for p, q in zip(pairs, pairs[1:]) if q[1] < p[1])
    res = dict(c.n); res.update(records_a=len(ra), records_b=len(rb), collapsed=collapsed, interleave_swaps=swaps, threads_a=len(ta), threads_b=len(tb),
                    renamed_ids=c.bij["obj"].renamed() + c.bij["tok"].renamed(), first_mismatch=c.mism[0] if c.mism else None, mismatches=c.mism,
                    first_env_diff=(c.envd[0] if c.envd else None), env_diffs=c.envd)
    fe = getattr(c, "first_env", None)
    res["first_mismatch_vs_env_diff"] = "n/a" if fe is None or first_key is None else "before" if first_key < fe else "after" if first_key > fe else "same record"
    res["exit"] = 1 if c.n["det_mismatch"] else 0
    return res


def load_profile(path):
    d = {}
    for ln in open(path):
        f = ln.rstrip("\n").split("\t")
        if len(f) >= 3 and not ln.startswith("#"): d.setdefault((f[0], f[1]), []).append((f[3] if len(f) > 3 else None, f[2]))
    return d


def main(argv):
    ap = argparse.ArgumentParser(prog="d9cmp.py", description=__doc__.split("\n")[0], add_help=True)
    ap.add_argument("a"); ap.add_argument("b"); ap.add_argument("--profile"); ap.add_argument("--max", type=int, default=1); ap.add_argument("--json")
    ap.add_argument("--fold-threads", action="store_true"); ap.add_argument("--spec", default=os.path.join(HERE, "..", "spec", "d3d9_spec.json"))
    try: o = ap.parse_args(argv)
    except SystemExit as e: return 2 if e.code else 0
    try:
        raw = open(o.spec, "rb").read(); spec = json.loads(raw); da, db = open(o.a, "rb").read(), open(o.b, "rb").read()
        prof = load_profile(o.profile) if o.profile else None
        ha, ra = d9s1.read_stream(da, spec); hb, rb = d9s1.read_stream(db, spec)
    except (OSError, ValueError, d9s1.ReadError) as ex:
        print(f"d9cmp: incomparable: {ex}", file=sys.stderr); return 2
    if ha["sha"] != hb["sha"]: print("d9cmp: incomparable: the streams have different spec hashes", file=sys.stderr); return 2
    if ha["sha"] != d9s1.spec_sha(raw): print("d9cmp: incomparable: the streams do not belong to this spec file", file=sys.stderr); return 2
    res = compare(ra, rb, prof, o.max, o.fold_threads)
    if o.json: json.dump(res, open(o.json, "w"), indent=1)
    keys = ("records_a", "records_b", "compared", "equal", "masked_pix", "env_equal", "env_diff", "collapsed", "interleave_swaps", "det_mismatch")
    print("d9cmp: " + " ".join(f"{k}={res[k]}" for k in keys) + (f" env_profile_checked={res['env_profile_checked']} profile_diff_a={res['env_profile_diff_a']} profile_diff_b={res['env_profile_diff_b']}" if prof else ""))
    if res["first_env_diff"]: e = res["first_env_diff"]; print(f"first env diff: seq {e['seq_a']} {e['method']} {e['item']} A={e['a']} B={e['b']}; first det mismatch is {res['first_mismatch_vs_env_diff']} it")
    for m in res["mismatches"]:
        print(f"MISMATCH seq {m['seq_a']}/{m['seq_b']} thread {m['thread']} {m['method']} {m['item']}: {m['why']}" + (f" byte {m['byte']} off {m['off_a']}/{m['off_b']} A={m.get('win_a')} B={m.get('win_b')}" if m.get("off_a") is not None else ""))
    print("RESULT: " + ("DET MISMATCH" if res["exit"] else "EQUAL")); return res["exit"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
