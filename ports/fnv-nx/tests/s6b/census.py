#!/usr/bin/env python3
"""S6b translation census: every program of the given .sdp packages through sdp2dksh, failures classified by cause.

  python3 -I tests/s6b/census.py PKG.sdp... [--build DIR] [--work DIR] [--jobs N] [--examples] [--determinism N]

Identical bytecode is translated once (the unique blobs are packed into shards under --work, which therefore holds
game-derived data and must stay out of git), and counts are reported per program and per unique blob. uam's GLSL
diagnostics (stderr only) are attached to the failure that follows them. Prints only counts (safe for the docs);
--examples adds the first offending GLSL line per cause, which is game-derived: do not commit it.
--determinism N: also translate the first N unique programs (0 = all) each in its own process and count the DKSH
files (with the epilogue variants) that differ from the shard run's: must be 0 (uam once read uninitialised
scheduling data, so a program's code depended on what the process had compiled before).
"""
import argparse, collections, os, re, struct, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

def extract(path):
    d = open(path, "rb").read(); out = []; off = 12
    while off + 260 <= len(d):
        nm = d[off:off + 256].split(b"\0")[0].decode("latin1"); sz = struct.unpack_from("<I", d, off + 256)[0]
        out.append((nm, d[off + 260:off + 260 + sz])); off += 260 + sz
    return out

def fnv1a64(b):
    h = 0xcbf29ce484222325
    for c in b: h = ((h ^ c) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h

def pack(entries, path):
    body = b"".join(n.encode("latin1")[:256].ljust(256, b"\0") + struct.pack("<I", len(d)) + d for n, d in entries)
    open(path, "wb").write(struct.pack("<III", 100, len(entries), len(body)) + body)

def centroid_use(bc):
    """(centroid input dcls, centroid destination modifiers on other instructions) of an SM2+ blob, from the tokens"""
    t = struct.unpack("<%dI" % (len(bc) // 4), bc[:len(bc) // 4 * 4])
    if not t or (t[0] >> 8 & 0xFF) < 2: return 0, 0
    i, dcl, ins = 1, 0, 0
    while i < len(t):
        op = t[i] & 0xFFFF
        if op == 0xFFFF: break
        n = (t[i] >> 16 & 0x7FFF) if op == 0xFFFE else (t[i] >> 24 & 0xF)
        if op == 0x1F and i + 2 < len(t) and t[i + 2] >> 20 & 4: dcl += 1
        # opcodes with a destination as the first parameter: arithmetic 0x01-0x13, 0x20-0x28 (minus 0x26 rep/0x27
        # endrep), texld/texldd/texldl, dsx/dsy, setp; flow control and def* carry none
        elif n and i + 1 < len(t) and (1 <= op <= 0x13 or op in (0x20, 0x21, 0x22, 0x23, 0x25, 0x42, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F)) \
                and t[i + 1] >> 20 & 4: ins += 1
        i += 1 + n
    return dcl, ins

def cause(err, diag):
    """a stable class name for one failure"""
    if err.startswith("uam:"):
        for l in diag:
            m = re.search(r"error: (.*)", l)
            if m:
                e = re.sub(r"`[^']*'|'[^']*'|\"[^\"]*\"", "'_'", m.group(1))
                return "uam: " + re.sub(r"\d+", "N", e).strip()[:120]
        return "uam: (no diagnostic)"
    e = re.sub(r"'[^']*'", "'_'", re.sub(r"\d+", "N", err))
    head, _, msgs = e.partition(": ")                  # "<backend>: msg; msg; ..." -> distinct messages once
    keep = []
    for m in (m.strip(" ;") for m in msgs.split("; ")):
        if m and not any(k.startswith(m) for k in keep): keep.append(m)   # the error buffer cuts the last one
    return (head + ": " + "; ".join(keep))[:140]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pkgs", nargs="+")
    ap.add_argument("--build", default=os.environ.get("FNVNX_SHADERS_BUILD", os.path.join(ROOT, "render/shaders/build/host")))
    ap.add_argument("--work", default=os.path.join(ROOT, "render/shaders/build/census"))
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--examples", action="store_true")
    ap.add_argument("--determinism", type=int, default=None)
    a = ap.parse_args()
    tool = os.path.join(a.build, "sdp2dksh")
    os.makedirs(a.work, exist_ok=True)

    progs, uniq = [], {}
    for p in a.pkgs:
        for nm, bc in extract(p):
            h = fnv1a64(bc); progs.append((os.path.basename(p), nm, h))
            uniq.setdefault(h, (nm, bc))
    keys = sorted(uniq)
    shards = [keys[i::a.jobs] for i in range(a.jobs)]
    procs = []
    for i, ks in enumerate(shards):
        sdp = os.path.join(a.work, "shard%d.sdp" % i); od = os.path.join(a.work, "out%d" % i)
        os.makedirs(od, exist_ok=True)
        pack([("%016x" % k, uniq[k][1]) for k in ks], sdp)      # unique entry names
        procs.append((od, subprocess.Popen(["nice", tool, "-o", od, sdp],
                                           stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, errors="replace")))
    res = {}
    for od, pr in procs:
        _, err = pr.communicate()
        diag, fails = [], {}
        for l in err.splitlines():
            m = re.match(r"FAIL [^:]*:(.*?): (.*)", l)
            if m: fails[m.group(1)] = (m.group(2), diag); diag = []
            else: diag.append(l)
        for line in open(os.path.join(od, "index.tsv")).read().splitlines()[1:]:
            f = line.split("\t")
            h = int(f[0], 16)
            c = None if f[4] == "ok" else cause(f[10], fails.get(f[2], ("", []))[1])
            ex = None
            if c and a.examples:
                ex = next((l for l in fails.get(f[2], ("", []))[1] if "error" in l), f[10])
            res[h] = (f[3], c, ex)

    by_prog, by_uniq, models, examples = collections.Counter(), collections.Counter(), collections.defaultdict(collections.Counter), {}
    ok_p = sum(1 for _, _, h in progs if res[h][1] is None)
    for _, _, h in progs:
        m, c, _ = res[h]
        if c: by_prog[c] += 1; models[c][m] += 1
    for h, (m, c, ex) in res.items():
        if c: by_uniq[c] += 1; examples.setdefault(c, ex)
    print("programs %d (unique %d): translated %d, failed %d (unique %d)" % (
        len(progs), len(uniq), ok_p, len(progs) - ok_p, sum(by_uniq.values())))
    cd = [centroid_use(uniq[h][1]) for _, _, h in progs]
    cu = {h: centroid_use(b) for h, (_, b) in uniq.items()}
    print("centroid: %d programs (unique %d) declare a _centroid input, %d (unique %d) of them fail; "
          "%d programs (unique %d) use _centroid on a non-dcl instruction" % (
          sum(1 for x in cd if x[0]), sum(1 for x in cu.values() if x[0]),
          sum(1 for (_, _, h), x in zip(progs, cd) if x[0] and res[h][1]), sum(1 for h, x in cu.items() if x[0] and res[h][1]),
          sum(1 for x in cd if x[1]), sum(1 for x in cu.values() if x[1])))
    print("| cause | programs | unique | by model |")
    print("|---|---|---|---|")
    for c, n in by_prog.most_common():
        print("| %s | %d | %d | %s |" % (c.replace("|", "/"), n, by_uniq[c],
                                         ", ".join("%s %d" % kv for kv in sorted(models[c].items()))))
    if a.examples:
        for c, ex in examples.items(): print("example [%s]: %s" % (c, ex))
    if a.determinism is not None:
        import glob, shutil
        ks = keys if a.determinism == 0 else keys[:a.determinism]
        shard_of = {k: i for i, s in enumerate(shards) for k in s}
        alone = os.path.join(a.work, "alone"); nd = nf = 0
        for k in ks:
            h = "%016x" % k; od = os.path.join(alone, h); shutil.rmtree(od, ignore_errors=True); os.makedirs(od)
            b = os.path.join(od, h); open(b, "wb").write(uniq[k][1])
            subprocess.run(["nice", tool, "-o", od, "--blob", b], capture_output=True)
            for f in glob.glob(os.path.join(a.work, "out%d" % shard_of[k], h + "*.dksh")):
                nf += 1; g = os.path.join(od, os.path.basename(f))
                nd += not os.path.exists(g) or open(f, "rb").read() != open(g, "rb").read()
            shutil.rmtree(od)
        print("determinism: %d of %d DKSH differ between the shard run and one process per program (%d programs)" % (nd, nf, len(ks)))
        return 1 if nd else 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
