#!/usr/bin/env python3
"""san_summary.py <NX_SAN_LOG dir>...: structural summary of sanitizer reports (files asan.<pid>, ubsan.<pid>).
Distinct finding = (kind, top frame under ports/fnv-nx).  Prints one line per distinct finding plus counts by kind.  No raw logs."""
import collections, os, re, sys

FR = re.compile(r"^\s*#\d+ 0x[0-9a-f]+ in (\S+) (\S+?)(?::(\d+))?(?::\d+)?$")
OWN = "ports/fnv-nx/"


def own(path): return OWN in path


def frames(lines):
    out = []
    for l in lines:
        m = FR.match(l)
        if m: out.append((m.group(1), m.group(2), m.group(3) or "0"))
    return out


def top(fr):
    for fn, p, ln in fr:
        if own(p): return "%s:%s %s" % (p.split(OWN, 1)[1], ln, fn)
    return fr[0][0] + " (no product frame)" if fr else "(no frame)"


def parse(path):
    L = open(path, errors="replace").read().split("\n"); res = []; i = 0
    while i < len(L):
        l = L[i]
        m = re.search(r"(\S+):(\d+):\d+: runtime error: (.*)", l)
        if m:  # UBSan: location line is the frame; kind = message with numbers removed
            kind = re.sub(r"[-+]?\d[\d.e+]*|0x[0-9a-f]+", "N", m.group(3)); kind = re.sub(r"'[^']*'", "'T'", kind)[:60]
            res.append(("ubsan: " + kind, "%s:%s" % (m.group(1).split(OWN)[-1], m.group(2)) if own(m.group(1)) else top(frames(L[i + 1:i + 30])))); i += 1; continue
        m = re.match(r"==\d+==ERROR: (?:Address|Leak)Sanitizer: (\S+)", l)
        if m and m.group(1) == "detected": i += 1; continue   # leak header: the per-leak blocks follow
        if m:
            j = i + 1; blk = []
            while j < len(L) and not L[j].startswith("SUMMARY"): blk.append(L[j]); j += 1
            res.append(("asan: " + m.group(1), top(frames(blk)))); i = j; continue
        m = re.match(r"(Direct|Indirect) leak of", l)
        if m:
            j = i + 1; blk = []
            while j < len(L) and L[j].strip(): blk.append(L[j]); j += 1
            res.append(("leak", top(frames(blk)))); i = j; continue
        i += 1
    return res


def main():
    seen = collections.Counter()
    for d in sys.argv[1:]:
        for f in sorted(os.listdir(d)):
            if f.startswith(("asan.", "ubsan.")):
                for k in parse(os.path.join(d, f)): seen[k] += 1
    byk = collections.Counter(k for k, _ in seen)
    for (k, fr), n in sorted(seen.items()): print("%-48s %s  x%d" % (k, fr, n))
    print("distinct=%d  by kind: %s" % (len(seen), dict(byk)))


if __name__ == "__main__": main()
