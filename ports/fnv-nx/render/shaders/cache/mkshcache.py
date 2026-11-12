#!/usr/bin/env python3
"""mkshcache.py: builds the shader cache file (format: shcache.h) from a directory of translations, deterministically
(same entries -> byte-identical file, whatever the order they are listed in).
  python3 -I mkshcache.py [--build-id HEX32 | --extra-input FILE...] TRANSLATED_DIR OUT.shc
TRANSLATED_DIR/entries.tsv (written by shxlate): id code_key(64 hex) code_len stage(v|p) flags l_hex(- = none); each id has <id>.dksh and <id>.json
(reflection).  The payload of an entry is shcache_payload_pack(dksh, json).  Default build id: build_id() = SHA-256 over the translator sources
(render/shaders/src, include, the two patches, build.sh, plus every --extra-input file, e.g. a local patch of the pinned uam) cut to 16 bytes: a different
translator makes the file EMPTY at load, never stale."""
import hashlib, os, struct, sys

def _key_flags_mask():
    """the key mask from the translator header: the complement of FNVNX_SHADER_NONKEY_FLAGS (one definition, shared with shcache.h)"""
    import os, re
    t = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "include", "fnvnx_shaders.h")).read()
    bit = dict((m.group(1), int(m.group(2))) for m in re.finditer(r"FNVNX_SHADER_(\w+)\s*=\s*1u\s*<<\s*(\d+)", t))
    nk = re.search(r"#define\s+FNVNX_SHADER_NONKEY_FLAGS\s+\(\(uint32_t\)\(([^)]*)\)\)", t).group(1)
    v = 0
    for nm in re.findall(r"FNVNX_SHADER_(\w+)", nk): v |= 1 << bit[nm]
    return 0xFFFFFFFF & ~v


MAGIC, VERSION, FLAGS_MASK = b"FNSC", 2, _key_flags_mask()
HEADER, INDEX = 80, 84


def sha256(b): return hashlib.sha256(b).digest()


def l_key(l): return sha256(l) if l else b"\0" * 32


def check_l(l):
    if len(l) > 31 or any(v // 16 > 13 for v in l) or any(a >= b for a, b in zip(l, l[1:])): raise ValueError("L must be ascending, unique, usage <= 13, <= 31 pairs")


def pack_payload(dksh, json): return struct.pack("<II", len(dksh), len(json)) + dksh + json


def build_id(root=None, extra=()):
    root = root or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    h = hashlib.sha256()
    for f in extra: h.update(b"extra\0" + hashlib.sha256(open(f, "rb").read()).digest())
    for sub in ("src", "include", "mojoshader.patch", "uam.patch", "build.sh"):
        p = os.path.join(root, sub)
        files = sorted(os.path.join(d, f) for d, _, fs in os.walk(p) for f in fs) if os.path.isdir(p) else [p]
        for f in files:
            h.update(os.path.relpath(f, root).encode() + b"\0" + hashlib.sha256(open(f, "rb").read()).digest())
    return h.digest()[:16]


def build(entries, bid):
    """entries: iterable of (code_key (32 bytes, SHA-256 of the bytecode), code_len, stage 0|1, flags, l bytes, payload bytes). Returns the file bytes."""
    es = []
    for h, n, st, fl, l, pay in entries:
        l = bytes(l); check_l(l)
        if len(h) != 32 or st not in (0, 1) or fl & ~FLAGS_MASK: raise ValueError("bad code key, stage or flags")
        es.append(((bytes(h), st, fl, l_key(l), n), l, pay))
    es.sort(key=lambda e: e[0])
    for a, b in zip(es, es[1:]):
        if a[0] == b[0]: raise ValueError("two entries with one key tuple %r (hash collision or duplicate)" % (a[0],))
    idx, blobs = bytearray(), bytearray(); off = HEADER + INDEX * len(es)
    for (h, st, fl, lh, n), l, pay in es:
        b = bytes([len(l)]) + l; b += b"\0" * (-len(b) & 3); b += pay
        idx += h + lh + struct.pack("<IIBBBBII", n, fl, st, len(l), 0, 0, off + len(blobs), len(b)); blobs += b
    body = bytes(idx) + bytes(blobs); size = HEADER + len(body)
    return MAGIC + struct.pack("<I", VERSION) + bid + struct.pack("<IIII", len(es), HEADER, HEADER + len(idx), size) + sha256(body) + b"\0" * 8 + body


def parse(data):
    """reader (for tests and tools): returns (build_id, [(code_key, code_len, stage, flags, l, payload)]) or raises ValueError"""
    if data[:4] != MAGIC or struct.unpack_from("<I", data, 4)[0] != VERSION: raise ValueError("magic or version")
    cnt, io, bo, size = struct.unpack_from("<IIII", data, 24); bh = data[40:72]
    if size != len(data) or bh != sha256(data[HEADER:]) or io != HEADER or bo != HEADER + cnt * INDEX or any(data[72:80]): raise ValueError("header")
    out = []
    for i in range(cnt):
        e = HEADER + i * INDEX; h, lh = data[e:e + 32], data[e + 32:e + 64]
        n, fl, st, nl, _, _, off, sz = struct.unpack_from("<IIBBBBII", data, e + 64)
        l = data[off + 1:off + 1 + nl]; pre = (1 + nl + 3) & ~3
        if data[off] != nl or lh != l_key(l) or off + sz > len(data): raise ValueError("entry %d" % i)
        out.append((h, n, st, fl, bytes(l), data[off + pre:off + sz]))
    return data[8:24], out


def load_dir(d):
    es = []
    for ln in open(os.path.join(d, "entries.tsv")).read().splitlines()[1:]:
        id_, h, n, st, fl, lx = ln.split("\t")[:6]
        es.append((bytes.fromhex(h), int(n), 0 if st == "v" else 1, int(fl, 16), b"" if lx == "-" else bytes.fromhex(lx),
                   pack_payload(open(os.path.join(d, id_ + ".dksh"), "rb").read(), open(os.path.join(d, id_ + ".json"), "rb").read())))
    return es


def main(argv):
    bid, extra = None, []
    while argv[:1] == ["--extra-input"]: extra.append(argv[1]); argv = argv[2:]
    if argv[:1] == ["--build-id"]: bid, argv = bytes.fromhex(argv[1]), argv[2:]
    if len(argv) != 2 or (bid is not None and len(bid) != 16): print(__doc__); return 2
    data = build(load_dir(argv[0]), bid if bid is not None else build_id(extra=extra))
    open(argv[1], "wb").write(data); print(f"{argv[1]}: {len(data)} bytes, {struct.unpack_from('<I', data, 24)[0]} entries")
    return 0


if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
