"""d9s1.py: reader of D9S1 streams for the comparator (written from render/d3d9/gen/STREAM.md; no code shared with the wrapper or the test reader).
read_stream(data, spec) -> (header, [Rec]).  Every record is a flat list of Items; every Item knows its file offset, so a comparator can point at bytes.
Item.kind: 'raw' (bytes), 'obj' (object id word), 'tok' (token id word).  Item.cls: None for plain inputs/header fields, else 0 det / 1 env / 2 pix (the item's HDR).
Item.idpos: byte offsets inside a raw struct payload that hold token ids.  Item.hoff: file offset of the item's HDR word (items with a class), off: of its payload.  Malformed input raises ReadError."""
import struct, hashlib


class ReadError(Exception):
    pass


class Item:
    __slots__ = ("label", "cls", "rec", "kind", "data", "off", "idpos", "hoff")

    def __init__(s, label, cls, rec, kind, data, off, idpos=(), hoff=None):
        s.label, s.cls, s.rec, s.kind, s.data, s.off, s.idpos, s.hoff = label, cls, rec, kind, data, off, idpos, hoff


class Rec:
    pass


def spec_sha(raw): return hashlib.sha256(raw).digest()


class Layout:
    """payload size and token positions of spec structs (STRUCT(s) of STREAM.md)"""
    def __init__(s, spec): s.sp, s.c = spec["structs"], {}

    def get(s, name):
        if name not in s.c:
            pos, off = [], 0
            for f in s.sp[name]["fields"]:
                k = f["kind"]
                if k in ("ptr", "ohandle"): pos.append(off); off += 4
                elif k == "struct":
                    n, (sz, sub) = f.get("count", 1), s.get(f["struct"])
                    for i in range(n): pos += [off + i * sz + x for x in sub]
                    off += n * sz
                else: off += f["size"]
            s.c[name] = (off, tuple(pos))
        return s.c[name]


class R:
    def __init__(s, d, p): s.d, s.p = d, p

    def w(s):
        if s.p + 4 > len(s.d): raise ReadError("truncated word")
        v, = struct.unpack_from("<I", s.d, s.p); s.p += 4; return v

    def raw(s, n):
        if s.p + n > len(s.d): raise ReadError("truncated payload")
        b = s.d[s.p:s.p + n]; s.p += n; pad = (-n) & 3
        if s.d[s.p:s.p + pad] != b"\0" * pad: raise ReadError("nonzero padding")
        s.p += pad; return b

    def buf(s): return s.raw(s.w())


def isptr(p): return p["byref"] or p["kind"] in ("buffer", "locked", "struct")


def read_stream(data, spec):
    names, lay = sorted(spec["interfaces"]), Layout(spec)
    mt = {(m["iface"], m["slot"]): m for m in spec["methods"]}
    if data[:4] != b"D9S1" or len(data) < 44: raise ReadError("bad magic or short header")
    if struct.unpack_from("<I", data, 4)[0] != 1: raise ReadError("version")
    hdr = {"sha": data[8:40]}; pos, recs, nid, retired = 44, [], 0, set()
    while pos < len(data):
        r = R(data, pos); rec = Rec(); I = rec.items = []; rec.off = pos
        try:
            n = r.w(); rec.seq = r.w(); rec.thread = r.w(); w3 = r.w()
            if n % 4 or n < 24 or pos + n > len(data): raise ReadError("bad record length")
            if rec.seq != len(recs): raise ReadError(f"seq {rec.seq} != {len(recs)}")
            rec.len = n; ifi, rec.slot = w3 & 0xFFFF, w3 >> 16
            rec.iface = names[ifi] if ifi != 0xFFFF else None
            I.append(Item("h:iface/slot", None, 1, "raw", struct.pack("<I", w3), pos + 12))
            rec.this = None; I.append(Item("h:this", None, 1, "obj", data[r.p:r.p + 4], r.p)); rec.this = r.w()
            I.append(Item("h:depth", None, 1, "raw", data[r.p:r.p + 4], r.p)); rec.depth = r.w()
            rec.new, rec.retired = [], []
            I.append(Item("h:n_new", None, 1, "raw", data[r.p:r.p + 4], r.p))
            for _ in range(r.w()):
                I.append(Item("h:new.id", None, 1, "obj", data[r.p:r.p + 4], r.p)); i = r.w()
                I.append(Item("h:new.iface", None, 1, "raw", data[r.p:r.p + 4], r.p)); f = r.w()
                nid += 1
                if i != nid: raise ReadError(f"new id {i}, expected {nid}")
                rec.new.append((i, f))
            I.append(Item("h:n_ret", None, 1, "raw", data[r.p:r.p + 4], r.p))
            for _ in range(r.w()):
                I.append(Item("h:ret.id", None, 1, "obj", data[r.p:r.p + 4], r.p)); i = r.w()
                if i in retired or i > nid: raise ReadError(f"bad retire {i}")
                retired.add(i); rec.retired.append(i)
            m = mt[(rec.iface, rec.slot)] if rec.iface else spec["exports"][rec.slot]
            if "params" not in m: raise ReadError("export without signature")
            rec.name, rec.m = m["name"], m
            body(r, rec, m, spec, lay)
            if r.p != pos + n: raise ReadError(f"body ends at {r.p - pos}, record length {n}")
        except (ReadError, KeyError, IndexError) as ex:
            raise ReadError(f"record at offset {pos} (seq {len(recs)}): {ex!r}")
        pos += n; recs.append(rec)
    return hdr, recs


def pointee(r, p, spec, lay, label):
    k, o = p["kind"], r.p
    if k in ("value", "enum", "flags", "float"): return Item(label, None, 1, "raw", r.raw(p.get("size", 4)), o)
    if k == "ohandle": return Item(label, None, 1, "tok", r.raw(4), o)
    if k == "handle": return Item(label, None, 1, "obj", r.raw(4), o)
    if k == "struct": return Item(label, None, 1, "raw", r.raw(lay.get(p["struct"])[0]), o, lay.get(p["struct"])[1])
    if k == "buffer": return Item(label, None, 1, "raw", r.buf(), o + 4)
    raise ReadError(f"param kind {k}")


def hitem(r):
    """reads one HDR word: (class, rec flag, offset of the HDR)"""
    o = r.p; h = r.w(); c, rc = h & 255, h >> 8
    if c > 2: raise ReadError(f"class {c}")
    return c, rc, o


def body(r, rec, m, spec, lay):
    I = rec.items
    for p in m["params"]:
        nm = p["name"]
        if not isptr(p): I.append(pointee(r, p, spec, lay, f"in:{nm}")); continue
        o = r.p; pres = r.w(); I.append(Item(f"in:{nm}:present", None, 1, "raw", struct.pack("<I", pres), o))
        if pres and p["dir"] != "out" and p["kind"] != "locked":
            if "in_when" in p:
                o = r.p; iw = r.w(); I.append(Item(f"in:{nm}:when", None, 1, "raw", struct.pack("<I", iw), o))
                if not iw: continue
            I.append(pointee(r, p, spec, lay, f"in:{nm}"))
    rk = m["ret_kind"]
    if rk != "void":
        c, rc, o = hitem(r); kind = "obj" if rk == "handle" else "tok" if rk == "ohandle" else "raw"
        I.append(Item("ret", c, rc, kind, r.raw(4), o + 4, (), o))
    for p in m["params"]:
        if p["dir"] == "in": continue
        nm = p["name"]
        if p["kind"] == "locked":
            c, rc, o = hitem(r); I.append(Item(f"lock:{nm}:tok", c, rc, "tok", r.raw(4) if rc else None, o + 4, (), o))
            if p["lock"]["shape"] in ("rect", "box"):
                for f in spec["structs"][p["struct"]]["fields"]:
                    if f["kind"] == "int":
                        c, rc, o = hitem(r); I.append(Item(f"lock:{nm}:{f['name']}", c, rc, "raw", r.raw(4) if rc else None, o + 4, (), o))
            c, rc, o = hitem(r); I.append(Item(f"lock:{nm}:data", c, rc, "raw", r.buf() if rc else None, o + 8, (), o))
            continue
        c, rc, o = hitem(r)
        if rc:
            it = pointee(r, p, spec, lay, f"out:{nm}"); it.cls, it.rec, it.hoff = c, rc, o
        else: it = Item(f"out:{nm}", c, 0, "raw", None, o + 4, (), o)
        I.append(it)
    if "closes" in m:
        c, rc, o = hitem(r); I.append(Item("close:tok", c, rc, "tok", r.raw(4) if rc else None, o + 4, (), o))
        c, rc, o = hitem(r); I.append(Item("close:data", c, rc, "raw", r.buf() if rc else None, o + 8, (), o))
