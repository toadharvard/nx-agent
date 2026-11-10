#!/usr/bin/env python3
"""SM1-3 translator totality census (R11).  python3 -I sm_census.py --build DIR --deps DIR [--work DIR] [--jobs 2]

  --build  render/shaders build dir holding s6b_asm and sdp2dksh   (build.sh --tests output)
  --deps   build.sh deps dir (needs mojoshader/mojoshader_internal.h for the opcode numbers)
Universe: sm_cases.cases().  Per row: assemble with s6b_asm; if that fails encode with sm_hand.py (encoded_by=hand);
when both succeed the bytes are compared (mismatches counted in the summary, s6b_asm bytes win).  Then
sdp2dksh --no-dksh --glsl (translate stage) and sdp2dksh (uam stage); status = ok | translate_fail(msg) | uam_fail(msg) |
cannot_encode(msg).  Writes sm_ops.tsv next to this script; prints the summary used in sm_README.md.
"""
import argparse, collections, concurrent.futures as cf, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sm_cases, sm_hand

def first_line(s):
    s = (s or "").strip().splitlines()
    return s[0].replace("\t", " ")[:200] if s else ""

def tool(exe, args, cwd=None):
    r = subprocess.run([exe] + args, capture_output=True, text=True, cwd=cwd, timeout=120)
    return r

def run_case(i, row, a, ops):
    m, kind, feat, text, legal = row
    d = os.path.join(a.work, "c%04d" % i)
    os.makedirs(d, exist_ok=True)
    asmf, binf = os.path.join(d, "x.asm"), os.path.join(d, "x.bin")
    open(asmf, "w").write(text)
    enc, note, hand = "asm", "", None
    try: hand = sm_hand.assemble(text, ops)
    except Exception as e: note = "hand:" + str(e)[:80]
    r = tool(os.path.join(a.build, "s6b_asm"), [asmf, binf])
    mism = 0
    if r.returncode == 0 and os.path.exists(binf):
        got = open(binf, "rb").read()
        if hand is not None and hand != sm_hand.strip_comments(got): mism = 1
    else:
        asmerr = first_line(r.stderr)
        if hand is None: return dict(row=row, status="cannot_encode", detail="asm: %s; %s" % (asmerr, note), enc="-", mism=0)
        open(binf, "wb").write(hand); enc = "hand"; note = "asm: " + asmerr
    out = {}
    for stage, flags in (("t", ["--no-dksh"]), ("u", [])):
        od = os.path.join(d, stage); os.makedirs(od, exist_ok=True)
        r = tool(os.path.join(a.build, "sdp2dksh"), ["-o", od, "--glsl"] + flags + ["--blob", binf])
        idx = os.path.join(od, "index.tsv")
        if r.returncode > 1 and not os.path.exists(idx):
            return dict(row=row, status="translate_fail" if stage == "t" else "uam_fail", enc=enc, mism=mism,
                        detail="tool crashed rc=%d %s" % (r.returncode, first_line(r.stderr)))
        f = open(idx).read().splitlines()[1].split("\t")
        out[stage] = (f[4], f[10] if len(f) > 10 else "", f[3])
        if f[4] != "ok":
            dg = [x for x in r.stderr.splitlines() if x.strip() and not x.startswith(("Shader failed", "FAIL", "sdp2dksh"))]
            if dg and stage == "u": f[10] = f[10] + " :: " + dg[0][:120]
            return dict(row=row, status=("translate_fail" if stage == "t" else "uam_fail"), enc=enc, mism=mism,
                        detail=first_line(f[10] if len(f) > 10 else r.stderr) + ((" [" + note + "]") if enc == "hand" else ""))
    return dict(row=row, status="ok", enc=enc, mism=mism, detail=note)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True); ap.add_argument("--deps", required=True)
    ap.add_argument("--work", default="/home/user/work/l6/sm/cases"); ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--out", default=os.path.join(HERE, "sm_ops.tsv"))
    a = ap.parse_args()
    ops = sm_hand.load_opcodes(os.path.join(a.deps, "mojoshader", "mojoshader_internal.h"))
    rows = sm_cases.cases()
    os.makedirs(a.work, exist_ok=True)
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        res = list(ex.map(lambda t: run_case(t[0], t[1], a, ops), enumerate(rows)))
    with open(a.out, "w") as o:
        o.write("model\tkind\tfeature\tstatus\tdetail\tencoded_by\n")
        for r in res:
            m, kind, feat, text, legal = r["row"]
            st = r["status"]
            st = st if st == "ok" else "%s(%s)" % (st, r["detail"].split(" [")[0]) if st != "cannot_encode" else "cannot_encode"
            det = ("legal=? " if legal == "?" else "") + (r["detail"] if r["status"] == "cannot_encode" or r["status"] == "ok" else "")
            if r["mism"]: det += " hand!=asm"
            if r["status"] in ("translate_fail", "uam_fail") and " [" in r["detail"]: det += r["detail"].split(" [", 1)[1].rstrip("]")
            o.write("\t".join([m, kind, feat, st.replace("\t", " "), det.strip().replace("\t", " "), r["enc"]]) + "\n")
    cnt = collections.Counter(r["status"] for r in res)
    print("universe=%d ok=%d translate_fail=%d uam_fail=%d cannot_encode=%d" % (len(res), cnt["ok"], cnt["translate_fail"],
          cnt["uam_fail"], cnt["cannot_encode"]))
    print("encoded_by hand=%d asm=%d; asm-vs-hand byte mismatches=%d" % (sum(r["enc"] == "hand" for r in res),
          sum(r["enc"] == "asm" for r in res), sum(r["mism"] for r in res)))
    per = collections.Counter((r["status"], first_line(r["detail"]).split(" [")[0][:90]) for r in res if r["status"] != "ok")
    for (s, d), n in per.most_common(): print("%4d %s %s" % (n, s, d))

if __name__ == "__main__":
    main()
