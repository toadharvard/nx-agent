"""plan.py: the test's model of a run: builds the call plan (what the driver passes, what the echo implementation answers) from d3d9_spec.json,
independently of the generated wrapper.  Every method of every interface is called once (plus producer / identity / cleanup calls)."""
import json, os, re, struct
from pyfn import ev, refs, PROFILE_FMTS, FMT

PROF = {("IDirect3DSurface9", "GetDesc"), ("IDirect3DVolume9", "GetDesc"), ("IDirect3DVertexBuffer9", "GetDesc"), ("IDirect3DIndexBuffer9", "GetDesc"),
        ("IDirect3DTexture9", "GetLevelDesc"), ("IDirect3DCubeTexture9", "GetLevelDesc"), ("IDirect3DVolumeTexture9", "GetLevelDesc"),
        ("IDirect3DTexture9", "GetLevelCount"), ("IDirect3DCubeTexture9", "GetLevelCount"), ("IDirect3DVolumeTexture9", "GetLevelCount"),
        ("IDirect3DVertexShader9", "GetFunction"), ("IDirect3DPixelShader9", "GetFunction"), ("IDirect3DVertexDeclaration9", "GetDeclaration"),
        ("IDirect3DQuery9", "GetDataSize"), ("IDirect3DQuery9", "GetType")}
GUID = bytes(range(0x40, 0x50)); SHADER = struct.pack("<2I", 0xFFFE0200, 0xFFFF); DECL = struct.pack("<HHBBBB", 0, 0, 3, 0, 0, 0) + struct.pack("<HHBBBB", 0xFF, 0, 17, 0, 0, 0)
PTRVAL = 0x00C0FFEE; FAIL = 0x8876086C
pat = lambda seed, n: bytes((seed * 37 + i * 11 + 5) & 255 for i in range(n))
arena_init = lambda slot, j: (slot * 31 + j * 7) & 255
arena_write = lambda k, j: (j * 5 + 3 + k) & 255


class Obj:
    def __init__(s, num, iface, creator):
        s.num, s.iface, s.rc, s.tall, s.tv, s.pd, s.parent, s.key, s.creator = num, iface, 0, None, {}, {}, None, (0, 0), creator
        s.usage = {"CreateRenderTarget": 1, "CreateDepthStencilSurface": 2}.get(creator, 0)


class Plan:
    def __init__(self, spec):
        self.spec, self.names = spec, sorted(spec["interfaces"]); self.objs, self.calls, self.cache, self.slot, self.inst, self.open = {}, [], {}, 0, {}, {}
        self.meths = sorted(spec["methods"], key=lambda m: (m["iface"], m["slot"])); self.sidx = spec["structs"]
        self.iid = {n: bytes.fromhex(spec["interfaces"][n]["iid"].replace("-", "")) for n in names_(spec)}
        self.iid = {n: b[0:4][::-1] + b[4:6][::-1] + b[6:8][::-1] + b[8:] for n, b in self.iid.items()}

    # ---- object model
    def isa(self, a, b):
        while a:
            if a == b: return True
            a = self.spec["interfaces"][a]["base"]
        return False

    def new_obj(self, iface, creator, rc):
        o = Obj(len(self.objs) + 1, iface, creator); o.rc = rc; self.objs[o.num] = o; return o

    def prof(self, o):
        i = o.iface; ib = i == "IDirect3DIndexBuffer9"
        return {"length": 64, "fmt": 101 if ib else PROFILE_FMTS[o.num % 3], "width": 8, "height": 4, "depth": 2, "levels": 3, "usage": o.usage, "pool": 0,
                "shader_bytes": 8, "decl_bytes": 16, "data_size": 4}

    def struct_image(self, name, seed, vals=None):
        st = self.sidx[name]; b = bytearray(b"\xAA" * st["size"]); vals = vals or {}
        for f in st["fields"]:
            o, sz, k = f["off"], f["size"], f["kind"]
            if k in ("int", "enum", "flags", "float") and sz == 4: b[o:o + 4] = struct.pack("<I", vals.get(f["name"], (seed * 13 + o) & 0xFFFF))
            elif k in ("ptr", "ohandle"): b[o:o + 4] = struct.pack("<I", 0x4000 + seed * 16 + o)
            elif k == "struct":
                for c in range(f.get("count", 1)): b[o + c * (sz // f.get("count", 1)):o + (c + 1) * (sz // f.get("count", 1))] = self.struct_image(f["struct"], seed + c)
            elif k != "pad": b[o:o + sz] = pat(seed + o, sz)
        return bytes(b)

    # ---- rule environment
    def env(self, c):
        e, a, out = {}, c["args"], c["outs"]
        for p in c["m"]["params"]:
            n, kd = p["name"], p["kind"]
            if kd in ("value", "enum", "flags", "float"):
                if p["byref"]: e[n] = b"x"; e["*" + n] = a.get(n, 0); e["*" + n + "@out"] = out.get(n, 0)
                else: e[n] = a[n] if kd != "float" else 0
            elif kd == "handle": e[n] = a.get(n, 0)
            else:
                v = a.get(n, b"x" if p["dir"] != "in" else None)
                e[n] = v if isinstance(v, (bytes, type(None))) else b"x"
        o = self.objs.get(c["this"])
        if o: e.update({"this." + k: v for k, v in self.prof(o).items()})
        return e

    def ev(self, c, s):
        o = self.objs.get(c["this"])
        return ev(self.spec, s, self.env(c), {g: f for g, f in o.pd.items()} if o else {}, c.get("hr", 0))

    # ---- one call
    def call(self, m, this, export=None, riid=None):
        k = len(self.calls); P = m["params"]; rp = refs(m)
        c = {"k": k, "m": m, "this": this, "export": export, "args": {}, "outs": {}, "iface": m.get("iface") if export is None else None, "hr": 0, "retv": None}
        self.calls.append(c); o = self.objs.get(this); a = c["args"]
        for i, p in enumerate(P):                                            # pass 1: scalars, handles, structs
            n, kd = p["name"], p["kind"]
            if kd in ("value", "enum", "flags", "float"):
                if kd == "float": a[n] = 1.5 + k
                elif p["byref"]: a[n] = 16 if n in rp else (k * 3 + i) & 0x3F
                elif p.get("flags") == "D3DLOCK": a[n] = (0, 16, 8192)[k % 3]
                elif p.get("flags") == "D3DSPD": a[n] = (this + k) % 2
                elif p.get("enum") == "D3DPRIMITIVETYPE": a[n] = 4
                elif n in ("index_format", "Format") and n in rp: a[n] = 101
                else: a[n] = 4 if n in rp else (k * 7 + i * 3 + 1) & 0x7F
            elif kd == "ohandle": a[n] = 0x1000 + k * 16 + i
            elif kd == "handle" and p["dir"] == "in":
                a[n] = self.inst.get(p["iface"]) or next(x.num for x in self.objs.values() if x.rc > 0 and self.isa(x.iface, p["iface"]))
            elif kd == "struct" and p["dir"] != "out":
                if p["ctype"] == "REFGUID": a[n] = GUID
                elif p["ctype"] == "REFIID": a[n] = self.iid[riid or "IUnknown"]
                elif p.get("null") and k % 2 == 0: a[n] = None
                elif p["struct"] == "RECT": a[n] = struct.pack("<4i", 0, 0, 4, 2)
                elif p["struct"] == "D3DBOX": a[n] = struct.pack("<6I", 0, 0, 4, 2, 0, 1)
                else: a[n] = self.struct_image(p["struct"], k + i)
            elif kd == "buffer" and p["dir"] == "out" and p.get("null") and k % 4 == 0: a[n] = None
        for i, p in enumerate(P):                                            # pass 2: input buffers
            if p["kind"] == "buffer" and p["dir"] == "in": a[p["name"]] = self.in_buffer(c, p, k + i)
        if m["ret_kind"] == "hresult" and k % 7 == 3 and not any(p["kind"] in ("locked", "handle") and p["dir"] != "in" for p in P) and "closes" not in m \
                and c["iface"] not in (None, "IUnknown") and (c["iface"], m["name"]) not in PROF and not m["name"].endswith("PrivateData"):
            c["hr"] = FAIL
        self.outs(c, o, riid)
        if m["ret_kind"] != "void" and c["retv"] is None: c["retv"] = c["hr"] if m["ret_kind"] == "hresult" else (k * 5 + 1) & 0xFF
        return c

    def desc_image(self, o, sname):
        pr = self.prof(o)
        return self.struct_image(sname, o.num * 3, {"Format": pr["fmt"], "Usage": pr["usage"], "Pool": 0, "Width": 8, "Height": 4, "Depth": 2, "Size": 64})

    def in_buffer(self, c, p, seed):
        if p.get("null") and c["k"] % 2 == 0: return None
        ln = p["len"]; on = "canon" in p and bool(self.ev(c, p["canon"]["when"]))
        if "canon" in p: c.setdefault("canon", {})[p["name"]] = on
        if "sm_bytes(" in ln: body = SHADER
        elif re.search(r"\bdecl_bytes\(", ln): body = DECL
        elif "wstr_bytes" in ln: body = "AB".encode("utf-16-le") + b"\0\0"
        elif "u32(" in ln: body = struct.pack("<8I", 32, 1, 2, 64, 0, 0, 0, 0) + struct.pack("<8i", 0, 0, 1, 1, 0, 0, 1, 1)
        elif on and p["canon"]["as"] == "handle":
            nums = [next(x.num for x in self.objs.values() if x.rc > 0 and self.isa(x.iface, p["canon"]["iface"]))] * (self.ev(c, ln) // 4)
            c.setdefault("objbuf", {})[p["name"]] = nums; body = b"\0" * len(nums) * 4
        elif on: body = struct.pack("<I", PTRVAL)
        else: body = None
        off = self.ev(c, p["off"]) if "off" in p else 0
        buf = pat(seed + 1, off) + (body if body is not None else pat(seed, self.ev(c, ln)))
        c["args"][p["name"]] = buf
        assert self.ev(c, ln) == len(buf) - off, (p["name"], ln)             # the rule really measures what we built
        return buf

    def outs(self, c, o, riid):
        m, P = c["m"], c["m"]["params"]; a, out = c["args"], c["outs"]; prof = (c["iface"], m["name"]) in PROF
        for i, p in enumerate(P):
            n, kd = p["name"], p["kind"]
            if p["dir"] == "in" or kd == "buffer": continue
            if kd in ("struct", "value", "ohandle") and n in a and a[n] is None: out[n] = None; continue
            if kd == "locked": out[n] = self.lock_out(c, p, o)
            elif kd == "handle": out[n] = self.handle_out(c, p, o)
            elif prof and n == "pSizeOfData": out[n] = 8
            elif prof and n == "pNumElements": out[n] = 2
            elif kd == "struct":
                out[n] = self.desc_image(o, p["struct"]) if prof else self.struct_image(p["struct"], c["k"] * 3 + i)
            elif kd == "ohandle": out[n] = 0x2000 + c["k"]
            elif n in refs(m): out[n] = 4
            else: out[n] = (c["k"] * 13 + i * 5 + 2) & 0xFFFF
        if prof: c["retv"] = {"GetLevelCount": 3, "GetDataSize": 4, "GetType": 8}.get(m["name"])
        for i, p in enumerate(P):                                            # buffers: lengths may use the other outs and ret
            if p["dir"] == "in" or p["kind"] != "buffer": continue
            n = p["name"]
            if n in a and a[n] is None: out[n] = None; continue
            L = self.ev(c, p["len"])
            if "canon" in p: c.setdefault("canon", {})[n] = bool(self.ev(c, p["canon"]["when"]))
            if "canon" in p and c["canon"][n]: out[n] = struct.pack("<I", PTRVAL)
            elif prof and m["name"] == "GetFunction": out[n] = SHADER
            elif prof and m["name"] == "GetDeclaration": out[n] = DECL
            else: out[n] = pat(c["k"] + i + 9, L)
            assert len(out[n]) == L, (m["name"], L)
        self.apply(c, o)

    def handle_out(self, c, p, o):
        m, a = c["m"], c["args"]; iface = p["iface"]
        if iface == "$riid": iface = next(n for n in self.names if self.iid[n] == a["riid"])
        if p["own"] == "new": return self.new_obj(iface, m["name"], 1).num
        key = (c["this"], m["name"], p["name"], iface, tuple(a.get(x) for x in ("Level", "FaceType", "iSwapChain", "Type", "iSwapChain")))
        if key not in self.cache:
            no = self.new_obj(iface, m["name"], 0); self.cache[key] = no.num
            if iface in ("IDirect3DSurface9", "IDirect3DVolume9") and o and o.iface.endswith("Texture9"):
                no.parent = o.num; no.key = (a["FaceType"], a["Level"]) if "FaceType" in a else (a["Level"], 0)
        self.objs[self.cache[key]].rc += 1
        return self.cache[key]

    def taint(self, o, key):
        r = self.objs[o.parent] if o.parent else o; key = o.key if o.parent else key
        if key in r.tv: return r.tv[key]
        if r.tall is not None: return r.tall
        return bool(r.usage & 3)                                             # profile usage never has AUTOGENMIPMAP

    def lock_out(self, c, p, o):
        L, sh, a = p["lock"], p["lock"]["shape"], c["args"]
        key = (tuple(a[x] for x, y in L["match"]) + (0, 0))[:2]
        d = {"slot": self.slot, "k": c["k"], "key": key, "onl": bool(self.ev(c, L["on_lock"])), "onu": bool(self.ev(c, L["on_unlock"])), "cls": 2 if self.taint(o, key) else 0, "sh": sh, "hdc": 0x5000 + c["k"]}
        self.slot += 1
        if sh == "linear":
            nb = self.ev(c, L["bytes"]); d.update(base_off=self.ev(c, L["off"]), rows=1, rowbytes=nb, depth=1, pitch=0, spitch=0)
        else:
            fmt, w, h = self.ev(c, L["fmt"]), self.ev(c, L["w"]), self.ev(c, L["h"]); dd = self.ev(c, L["d"]) if sh == "box" else 1
            rb, rw = self.ev(c, f"rowbytes({fmt},{w})"), self.ev(c, f"rows({fmt},{h})")
            d.update(base_off=0, rows=rw, rowbytes=rb, depth=dd, pitch=rb + 8, spitch=(rb + 8) * rw + 8, hdc=0x5000 + c["k"])
        d["nbytes"] = d["rows"] * d["rowbytes"] * d["depth"]
        self.open[(o.num, key)] = d
        return d

    def apply(self, c, o):
        m, a = c["m"], c["args"]
        if m["name"] == "AddRef" and o: o.rc += 1; c["retv"] = o.rc
        if m["name"] == "Release" and o: o.rc -= 1; c["retv"] = o.rc
        if m["ret_kind"] == "hresult" and c["hr"]: return
        if m["name"] == "SetPrivateData": o.pd[GUID] = a["flags"] & 1
        if m["name"] == "FreePrivateData": o.pd.pop(GUID, None)
        if "closes" in m:
            kk = (tuple(a[y] for x, y in self.lockmatch(c)) + (0, 0))[:2]; d = self.open.pop((o.num, kk), None); c["close"] = d
        for e in m.get("effects", []):
            for kind, args in re.findall(r"(pix|copy)\(([^)]*)\)", e):
                ar = [x.strip() for x in args.split(",")]; get = lambda n: o if n == "this" else self.objs.get(a.get(n))
                if ar[0] in ("rendertargets", "depthstencil"): continue
                if kind == "pix": self.setv(get(ar[0]), True)
                else: self.copyv(get(ar[0]), get(ar[1]))

    def lockmatch(self, c):
        return next(q["lock"]["match"] for mm in self.spec["methods"] if mm["iface"] == c["iface"] and mm["name"] in c["m"]["closes"] for q in mm["params"] if q["kind"] == "locked")

    def setv(self, o, v):
        if o.parent: self.objs[o.parent].tv[o.key] = v
        else: o.tall = v; o.tv = {}

    def copyv(self, d, s):                                                    # whole-object rule of the runtime: any tainted level taints the destination
        self.setv(d, bool(self.taint(s, (0, 0)) or (not s.parent and any(s.tv.values()))))

    # ---- the run
    def build(self):
        spec = self.spec; M = {(m["iface"], m["name"]): m for m in self.meths}
        ex = [(i, e) for i, e in enumerate(spec["exports"]) if "params" in e and "ret_kind" in e]
        for i, e in ex:
            c = self.call(e, 0, export=i)
            if e["ret_kind"] == "handle": c["retv"] = self.new_obj("IDirect3D9", e["name"], 1).num; self.inst["IDirect3D9"] = c["retv"]
            elif e["name"] == "Direct3DCreate9Ex": self.inst["IDirect3D9Ex"] = c["outs"]["ppD3D"]
        for _ in range(len(self.names)):
            for t in self.names:
                if t in self.inst: continue
                src = [(m, p) for own in ("new", "addref") for m in self.meths if m["iface"] in self.inst for p in m["params"] if p["kind"] == "handle" and p["dir"] == "out" and p["iface"] == t and p["own"] == own]
                if src: m, p = src[0]; self.inst[t] = self.call(m, self.inst[m["iface"]])["outs"][p["name"]]
                else:
                    for s in list(self.inst):
                        if s != "IUnknown" and "IUnknown" in spec["interfaces"]: self.inst[t] = self.call(M[(s, "QueryInterface")], self.inst[s], riid=t)["outs"]["ppvObj"]; break
        for t in self.names:
            for _ in range(3): self.call(M[(t, "AddRef")], self.inst[t])
        first = len(self.calls); self.cover = first
        for m in self.meths: self.call(m, self.inst[m["iface"]], riid=self.names[len(self.calls) % len(self.names)])
        for c in list(self.calls[first:]):
            for p in c["m"]["params"]:
                if p["kind"] == "handle" and p["dir"] == "out" and p["own"] == "addref" and c["hr"] == 0 and c["outs"][p["name"]] and p["iface"] != "$riid":
                    self.call(c["m"], c["this"]); break
        for o in sorted(self.objs.values(), key=lambda x: -x.num):
            for _ in range(o.rc):
                if o.rc > 0: self.call(M[(o.iface, "Release")], o.num)
        e = next(e for i, e in ex if e["ret_kind"] == "handle"); i = spec["exports"].index(e)
        c = self.call(e, 0, export=i); o = self.new_obj("IDirect3D9", e["name"], 1); c["retv"] = o.num
        self.call(M[("IDirect3D9", "Release")], o.num)
        return self.calls


def names_(spec):
    return sorted(spec["interfaces"])
