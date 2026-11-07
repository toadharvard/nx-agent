#!/usr/bin/env python3
"""savediff.py - the save/load round trip of an S18 scenario (D25 #9): save A, load A, save B; what B has that A has not.

    savediff.py <a.fos> <b.fos> [--json OUT]

A Fallout: New Vegas save starts with the magic "FO3SAVEGAME", a 32-bit header size and the header ('|'-separated
fields: the player's name, level, location, play time, the screenshot's size), then the screenshot (RGB), then the
game's state. The header and the screenshot are left out: play time, save number and the picture differ in any two
saves. The rest is cut into content-defined chunks (a gear rolling hash: a chunk boundary depends only on the bytes
near it, so an insertion moves no other boundary), and the two chunk sequences are aligned (difflib). Each run of
chunks that differs is one difference, keyed by the hash of the last equal chunk before it (its anchor), so the
same difference in another pair of saves of the same scene - the original's own round trip under Wine - has the same
key, and scenario.py report counts only the guest's differences the original does not have.

compare() -> {"diffs": n, "diff_bytes": n, "diff_keys": [...], "summary": "...", "body": [len a, len b], ...}.
The screenshot's size is calibrated per pair (compare()). The parser takes only the public container layout above; the state's own records are not decoded (next step, once
the farms have real saves: per change-form keys instead of anchors).
"""
from __future__ import annotations

import difflib
import hashlib
import json
import struct
import sys

MAGIC = b"FO3SAVEGAME"
MIN_CHUNK, AVG_MASK, MAX_CHUNK = 64, (1 << 8) - 1, 4096


def _gear():
    g, x = [], 0x9E3779B97F4A7C15
    for _ in range(256):   # a fixed pseudo-random table (splitmix64): the same boundaries on every machine
        x = (x + 0x9E3779B97F4A7C15) & (1 << 64) - 1
        z = x
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & (1 << 64) - 1
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & (1 << 64) - 1
        g.append((z ^ (z >> 31)) & 0xFFFFFFFF)
    return g


GEAR = _gear()


def chunks(b):
    """[(offset, length)] content-defined"""
    out, start, h, n = [], 0, 0, len(b)
    i = 0
    while i < n:
        h = ((h << 1) + GEAR[b[i]]) & 0xFFFFFFFF
        ln = i - start + 1
        if (ln >= MIN_CHUNK and (h & AVG_MASK) == 0) or ln >= MAX_CHUNK:
            out.append((start, ln))
            start, h = i + 1, 0
        i += 1
    if start < n:
        out.append((start, n - start))
    return out


def header(data):
    """(header end, [candidate screenshot sizes in bytes]) or (0, []) when the file is not a save"""
    if data[:len(MAGIC)] != MAGIC or len(data) < len(MAGIC) + 4:
        return 0, []
    hsize = struct.unpack_from("<I", data, len(MAGIC))[0]
    hend = len(MAGIC) + 4 + hsize
    if hend > len(data):
        return 0, []
    dims = [struct.unpack("<I", f)[0] for f in data[len(MAGIC) + 4:hend].split(b"|") if len(f) == 4]
    shots = []
    for k in range(len(dims) - 1):   # adjacent 32-bit fields that could be the picture's width and height
        w, h = dims[k], dims[k + 1]
        if 16 <= w <= 4096 and 16 <= h <= 4096 and hend + w * h * 3 <= len(data) and w * h * 3 not in shots:
            shots.append(w * h * 3)
    return hend, shots + [0]


def diff(ba, bb):
    """(keys, bytes, regions, chunk counts) of two bodies"""
    ca = [ba[o:o + n] for o, n in chunks(ba)]
    cb = [bb[o:o + n] for o, n in chunks(bb)]
    ka = [hashlib.sha1(c).hexdigest()[:12] for c in ca]
    kb = [hashlib.sha1(c).hexdigest()[:12] for c in cb]
    sm = difflib.SequenceMatcher(a=ka, b=kb, autojunk=False)
    keys, nbytes, regions = [], 0, []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        anchor = ka[i1 - 1] if i1 > 0 else "start"
        k = f"{tag}@{anchor}"
        n = max(sum(len(c) for c in ca[i1:i2]), sum(len(c) for c in cb[j1:j2]))
        keys.append(k)
        nbytes += n
        regions.append({"key": k, "a_chunks": i2 - i1, "b_chunks": j2 - j1, "bytes": n})
    return keys, nbytes, regions, (len(ka), len(kb))


def compare(path_a, path_b):
    """The screenshot's size is not taken on trust from a field position: each candidate (adjacent plausible
    width/height fields of the header, or none) is tried on both files, and the one that leaves the fewest differing
    bytes is used - the right one removes the picture, which differs in any two saves."""
    a, b = open(path_a, "rb").read(), open(path_b, "rb").read()
    ea, sa = header(a)
    eb, sb = header(b)
    rec = bool(ea and eb)
    best = None
    for shot in ([s for s in sa if s in sb] or [0]) if rec else [None]:
        ba, bb = (a[ea + shot:], b[eb + shot:]) if rec else (a, b)
        keys, nbytes, regions, nch = diff(ba, bb)
        if best is None or nbytes < best[1] or (nbytes == best[1] and shot < best[4]):
            best = (keys, nbytes, regions, nch, shot, ba, bb)
    keys, nbytes, regions, nch, shot, ba, bb = best
    hd = [{"recognised": rec, "header_bytes": e, "screenshot_bytes": shot} for e in (ea, eb)]
    s = (f"{len(keys)} differing regions, {nbytes} bytes of {len(bb)} (chunks {nch[0]} / {nch[1]}; "
         f"header {'recognised' if rec else 'NOT recognised'}, screenshot {shot} bytes)")
    return {"schema": "fnvnx-savediff/1", "diffs": len(keys), "diff_bytes": nbytes, "diff_keys": keys,
            "regions": regions[:200], "body": [len(ba), len(bb)], "header": hd, "summary": s,
            "identical_body": ba == bb}


def main(a):
    if len(a) < 2:
        print(__doc__)
        return 2
    r = compare(a[0], a[1])
    if "--json" in a:
        json.dump(r, open(a[a.index("--json") + 1], "w"), indent=1)
    print(r["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
