"""enc.py: python encoder of the EXPECTED D9S1 stream from the plan (what the wrapper must record for the calls the driver makes and
the answers the echo gives), written from STREAM.md.  Independent of the generated wrapper and of the reader."""
import struct
from plan import PTRVAL, arena_init, arena_write

CLS = {"det": 0, "env": 1, "pix": 2}
QCLS = {8: 2}                                                              # profile query type is EVENT (pix)


def lock_img(d, written):
    if d["sh"] == "dc": return bytes((d["hdc"] * 3 + i * 11) & 255 for i in range(d["nbytes"]))
    return bytes((arena_write(d["k"], j) if written else arena_init(d["slot"], j))
                 for dd in range(d["depth"]) for r in range(d["rows"]) for i in range(d["rowbytes"]) for j in [d["base_off"] + dd * d["spitch"] + r * d["pitch"] + i])


class Enc:
    def __init__(self, plan, sha):
        self.pl, self.sha = plan, sha; self.names = plan.names; self.out = bytearray(b"D9S1" + struct.pack("<I", 1) + sha + struct.pack("<I", 0))
        self.oid, self.nid, self.tid, self.ntok, self.seq = {}, 0, {}, 0, 0

    def run(self):
        for c in self.pl.calls:
            if "nested_in" in c: continue
            for n in self.pl.calls:
                if n.get("nested_in") == c["k"]: self.record(n)
            self.record(c)
        return bytes(self.out)

    # ---- body primitives
    def w(self, v): self.b += struct.pack("<I", v & 0xFFFFFFFF)
    def pad(self): self.b += b"\0" * (-len(self.b) & 3)
    def raw(self, x): self.b += x; self.pad()
    def buf(self, x): self.w(len(x)); self.raw(x)
    def hdr(self, cls, rec): self.w(cls | (int(bool(rec)) << 8))

    def obj(self, num):
        if not num: return self.w(0)
        if num not in self.oid:
            self.nid += 1; self.oid[num] = self.nid; self.new.append((self.nid, self.names.index(self.pl.objs[num].iface)))
        self.w(self.oid[num])

    def tok(self, raw):
        if not raw: return self.w(0)
        if raw not in self.tid: self.ntok += 1; self.tid[raw] = self.ntok
        self.w(self.tid[raw])

    def tokn(self, key):
        self.ntok += 1; self.tid[key] = self.ntok; self.w(self.ntok); return self.ntok

    def st(self, name, img):
        for f in self.pl.spec["structs"][name]["fields"]:
            seg = img[f["off"]:f["off"] + f["size"]]
            if f["kind"] in ("ptr", "ohandle"): self.tok(struct.unpack("<I", seg[:4])[0])
            elif f["kind"] == "pad": self.b += b"\0" * f["size"]
            elif f["kind"] == "struct":
                n = f.get("count", 1)
                for i in range(n): self.st(f["struct"], seg[i * (f["size"] // n):(i + 1) * (f["size"] // n)])
            else: self.b += seg
        self.pad()

    def pointee(self, c, p, v, post):
        k = p["kind"]
        if k in ("value", "enum", "flags", "float"): self.raw(struct.pack("<I", v & 0xFFFFFFFF))
        elif k == "ohandle": self.tok(v)
        elif k == "handle": self.obj(v)
        elif k == "struct": self.st(p["struct"], v)
        elif k == "buffer":
            off = self.pl.ev(c, p["off"]) if "off" in p else 0
            n = self.pl.ev(c, p["len"]); data = v[off:off + n]
            if "canon" in p and c["canon"][p["name"]]:
                self.w(n)
                if p["canon"]["as"] == "handle":
                    for x in c["objbuf"][p["name"]]: self.obj(x)
                else:
                    for i in range(0, n - n % 4, 4): self.tok(struct.unpack_from("<I", data, i)[0])
                    self.b += data[n - n % 4:]
                self.pad()
            else: self.buf(data)

    def record(self, c):
        pl, m, a, outs = self.pl, c["m"], c["args"], c["outs"]; self.b = bytearray(); self.new = []; hr = c["hr"]
        this = c["this"]; self.obj(this); thisid = struct.unpack_from("<I", self.b, 0)[0] if this else 0; self.b = bytearray()
        pl_hr = c["hr"]; c["hr"] = 0
        for p in m["params"]:
            n, k = p["name"], p["kind"]; ptr = p["byref"] or k in ("buffer", "locked", "struct")
            if not ptr:
                if k == "float": self.w(struct.unpack("<I", struct.pack("<f", a[n]))[0])
                elif k == "handle": self.obj(a[n])
                elif k == "ohandle": self.tok(a[n])
                else: self.w(a[n])
                continue
            pres = a.get(n, 1) is not None if k in ("buffer", "struct") else True
            self.w(1 if pres else 0)
            if p["dir"] == "out" or k == "locked" or not pres: continue
            if "in_when" in p:
                iw = pl.ev(c, p["in_when"]) != 0; self.w(int(iw))
                if not iw: continue
            self.pointee(c, p, a[n], False)
        c["hr"] = pl_hr
        if m["ret_kind"] != "void":
            self.hdr(CLS[m["ret_cls"]], 1)
            if m["ret_kind"] == "handle": self.obj(c["retv"])
            elif m["ret_kind"] == "ohandle": self.tok(c["retv"])
            else: self.w(c["retv"])
        for p in m["params"]:
            n, k = p["name"], p["kind"]
            if p["dir"] == "in": continue
            ok = hr == 0 or m["ret_kind"] != "hresult"
            if k == "locked":
                d = outs[n]; self.hdr(0, ok); d["tokid"] = self.tokn(("lock", d["slot"])) if ok else 0
                if d["sh"] in ("rect", "box"):
                    for f in pl.spec["structs"][p["struct"]]["fields"]:
                        if f["kind"] == "int": self.hdr(CLS[p["field_cls"][f["name"]]], ok); self.w(d["pitch"] if f["name"] in ("Pitch", "RowPitch") else d["spitch"]) if ok else None
                self.hdr(d["cls"], ok and d["onl"])
                if ok and d["onl"]: self.buf(lock_img(d, False))
                continue
            v = outs[n]; pres = v is not None
            rec = pres and (p["when"] == "always" or ok)
            cls = QCLS.get(8, 2) if p["cls"] == "by:query_type" else CLS[p["cls"]]
            self.hdr(cls, rec)
            if rec: self.pointee(c, p, v, True)
        if "closes" in m:
            d = c["close"]; ok = d is not None and hr == 0
            self.hdr(0, ok)
            if ok: self.w(d["tokid"])
            self.hdr(d["cls"] if d else 0, ok and d["onu"])
            if ok and d["onu"]: self.buf(lock_img(d, True))
        ret = [thisid] if m["name"] == "Release" and c["iface"] and c["retv"] == 0 else []
        hd = struct.pack("<7I", 0, self.seq, c.get("thread", 0), (self.names.index(c["iface"]) if c["iface"] else 0xFFFF) | (m["slot"] << 16 if c["iface"] else c["export"] << 16), thisid, c.get("depth", 0), len(self.new))
        for i, f in self.new: hd += struct.pack("<2I", i, f)
        hd += struct.pack("<I", len(ret)) + b"".join(struct.pack("<I", x) for x in ret)
        rec = bytearray(hd) + self.b; rec[0:4] = struct.pack("<I", len(rec)); self.out += rec; self.seq += 1
