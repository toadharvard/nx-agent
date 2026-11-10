"""d9s1_read.py: independent pure-python reader of D9S1 streams (written from render/d3d9/gen/STREAM.md; shares no code with the wrapper).
read_stream(data, spec) -> (header, [Rec]).  Rec.items is a flat list of (label, cls, rec, value); every malformed byte raises ReadError."""
import hashlib, struct


class ReadError(Exception):
    pass


class Rec:
    def __init__(self):
        self.items = []

    def __repr__(self):
        return f"Rec(seq={self.seq} {self.name} this={self.this} depth={self.depth})"


class R:
    def __init__(self, d, pos=0):
        self.d, self.p = d, pos

    def w(self):
        if self.p + 4 > len(self.d): raise ReadError("truncated word")
        v = struct.unpack_from("<I", self.d, self.p)[0]; self.p += 4; return v

    def raw(self, n):
        if self.p + n > len(self.d): raise ReadError("truncated payload")
        b = self.d[self.p:self.p + n]; self.p += n
        pad = (-n) & 3
        if self.d[self.p:self.p + pad] != b"\0" * pad: raise ReadError("nonzero padding")
        self.p += pad; return b

    def buf(self):
        return self.raw(self.w())


def sfields(spec, name):
    """payload byte count of a struct: sum of its field sizes (ptr/ohandle fields are 4-byte tokens)."""
    return sum(4 if f["kind"] in ("ptr", "ohandle") else f["size"] for f in spec["structs"][name]["fields"])


def value(r, p, spec):
    k = p["kind"]
    if k in ("value", "enum", "flags", "float"): return r.raw(p.get("size", 4))
    if k in ("ohandle", "handle"): return r.w()
    if k == "struct": return r.raw(sfields(spec, p["struct"]))
    if k == "buffer": return r.buf()
    raise ReadError(f"kind {k}")


def isptr(p):
    return p["byref"] or p["kind"] in ("buffer", "locked", "struct")


def read_stream(data, spec):
    names = sorted(spec["interfaces"])
    mt = {(m["iface"], m["slot"]): m for m in spec["methods"]}
    if data[:4] != b"D9S1": raise ReadError("magic")
    ver, = struct.unpack_from("<I", data, 4)
    if ver != 1: raise ReadError("version")
    hdr = {"version": ver, "sha": data[8:40], "flags": struct.unpack_from("<I", data, 40)[0]}
    pos, recs, nid, retired = 44, [], 0, set()
    while pos < len(data):
        r = R(data, pos); rec = Rec()
        n = r.w(); rec.seq = r.w(); rec.thread = r.w(); w3 = r.w(); rec.this = r.w(); rec.depth = r.w()
        if n % 4 or pos + n > len(data): raise ReadError(f"record at {pos}: bad length {n}")
        if rec.seq != len(recs): raise ReadError(f"record at {pos}: seq {rec.seq} != {len(recs)}")
        ifi, rec.slot = w3 & 0xFFFF, w3 >> 16
        rec.iface = names[ifi] if ifi != 0xFFFF else None
        rec.new = [(r.w(), r.w()) for _ in range(r.w())]
        for i, f in rec.new:
            nid += 1
            if i != nid: raise ReadError(f"seq {rec.seq}: new id {i}, expected {nid}")
        rec.retired = [r.w() for _ in range(r.w())]
        for i in rec.retired:
            if i in retired or i > nid: raise ReadError(f"seq {rec.seq}: bad retire {i}")
            retired.add(i)
        m = mt[(rec.iface, rec.slot)] if rec.iface else spec["exports"][rec.slot]
        rec.name = m["name"]; rec.m = m; it = rec.items
        for p in m["params"]:
            nm = p["name"]
            if not isptr(p): it.append((f"in:{nm}", None, 1, value(r, p, spec))); continue
            pres = r.w(); it.append((f"in:{nm}:present", None, 1, pres))
            if pres and p["dir"] != "out" and p["kind"] != "locked":
                if "in_when" in p:
                    iw = r.w(); it.append((f"in:{nm}:when", None, 1, iw))
                    if not iw: continue
                it.append((f"in:{nm}", None, 1, value(r, p, spec)))
        if m["ret_kind"] != "void":
            h = r.w(); it.append(("ret", h & 255, h >> 8, value(r, {"kind": "handle" if m["ret_kind"] == "handle" else "ohandle" if m["ret_kind"] == "ohandle" else "value"}, spec)))
        for p in m["params"]:
            if p["dir"] == "in": continue
            nm = p["name"]
            if p["kind"] == "locked":
                h = r.w(); it.append((f"lock:{nm}:tok", h & 255, h >> 8, r.w() if h >> 8 else None))
                if p["lock"]["shape"] in ("rect", "box"):
                    for f in spec["structs"][p["struct"]]["fields"]:
                        if f["kind"] == "int":
                            h = r.w(); it.append((f"lock:{nm}:{f['name']}", h & 255, h >> 8, r.raw(4) if h >> 8 else None))
                h = r.w(); it.append((f"lock:{nm}:data", h & 255, h >> 8, r.buf() if h >> 8 else None)); continue
            h = r.w(); c, rc = h & 255, h >> 8
            it.append((f"out:{nm}", c, rc, value(r, p, spec) if rc else None))
        if "closes" in m:
            h = r.w(); it.append(("close:tok", h & 255, h >> 8, r.w() if h >> 8 else None))
            h = r.w(); it.append(("close:data", h & 255, h >> 8, r.buf() if h >> 8 else None))
        if r.p != pos + n: raise ReadError(f"seq {rec.seq}: {m['name']} body ends at {r.p - pos}, record length {n}")
        pos += n; recs.append(rec)
    return hdr, recs


def spec_sha(path):
    return hashlib.sha256(open(path, "rb").read()).digest()
