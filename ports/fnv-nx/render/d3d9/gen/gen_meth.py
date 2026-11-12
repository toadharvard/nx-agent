"""gen_meth.py: emits one wrapped method / export of d9wrap.gen.cpp from its spec entry (kinds, rules, effects exactly as d3d9_spec.json defines them)."""
import re
from gen_expr import Ctx, GenError
import gen_tab as T

CLS = {"det": 0, "env": 1, "pix": 2}
REFS = ("REFIID", "REFGUID")


def isptr(p):
    return p["byref"] or p["kind"] in ("buffer", "locked", "struct")


class Meth:
    def __init__(self, spec, names, m, export=None):
        self.spec, self.names, self.m, self.export = spec, names, m, export
        self.iface = None if export is not None else m["iface"]
        self.what = m["name"] if export is not None else f"{self.iface}::{m['name']}"
        self.P = m.get("params", [])
        self.hr = m.get("ret_kind") == "hresult"
        props = T.props_of(self.iface) if self.iface else None
        self.cpre = Ctx(m, spec, props, False, self.what + " (entry rule)")
        self.cpost = Ctx(m, spec, props, True, self.what + " (output rule)")
        self.L = []; self.used = set()

    def a(self, s):
        self.L.append("    " + s)

    def err(self, msg):
        raise GenError(f"{self.what}: {msg}")

    def ifx(self, name):
        return f"D9I_{name}"

    # ---- pointee encoders (shared by inputs and outputs)
    def pointee(self, p, ctx):
        n, k = p["name"], p["kind"]; ref = p["ctype"] in REFS
        if k in ("value", "enum", "flags", "float"):
            if p.get("size", 4) != 4: self.err(f"param {n}: value size {p.get('size')} has no emitter")
            self.a(f"d9_rec.raw({n}, 4);")
        elif k == "ohandle": self.a(f"d9_rec.tok((uintptr_t)*{n});")
        elif k == "handle": self.a(f"d9_rec.obj(*{n});")
        elif k == "struct": self.a(f"d9_rec.st(&d9_sd_{p['struct']}, {'&' + n if ref else n});")
        elif k == "buffer":
            off = ctx.c(p["off"]) if "off" in p else "0"
            self.a(f"{{ uint64_t d9_len = {ctx.c(p['len'])}; const char *d9_ptr = (const char *){n} + {off};")
            if "canon" in p:
                if p["canon"]["as"] not in ("ohandle", "handle"): self.err(f"param {n}: canon as {p['canon']['as']} has no emitter")
                self.a(f"  if ({ctx.c(p['canon']['when'])}) d9_rec.canon(d9_ptr, d9_len, {int(p['canon']['as'] == 'handle')}); else d9_rec.buf(d9_ptr, d9_len); }}")
            else: self.a("  d9_rec.buf(d9_ptr, d9_len); }")
        else: self.err(f"param {n}: kind {k} has no emitter")

    def inputs(self):
        for p in self.P:
            n, k = p["name"], p["kind"]
            if not isptr(p):
                if k in ("value", "enum", "flags"):
                    if p.get("size", 4) != 4: self.err(f"param {n}: value size {p.get('size')} has no emitter")
                    self.a(f"d9_rec.w((uint32_t){n});")
                elif k == "float": self.a(f"d9_rec.w(d9s_fbits({n}));")
                elif k == "handle": self.a(f"d9_rec.obj({n});")
                elif k == "ohandle": self.a(f"d9_rec.tok((uintptr_t){n});")
                else: self.err(f"param {n}: by-value kind {k} has no emitter")
                continue
            ref = p["ctype"] in REFS; cond = "1" if ref else f"{n} != 0"
            self.a(f"d9_rec.w({cond});")
            if p["dir"] == "out" or k == "locked": continue
            if k == "handle": self.err(f"param {n}: pointer to input handle has no emitter")
            self.a(f"if ({cond}) {{")
            if "in_when" in p:
                self.a(f"  uint64_t d9_iw = {self.cpre.c(p['in_when'])}; d9_rec.w(d9_iw != 0); if (d9_iw) {{")
            self.pointee(p, self.cpre)
            if "in_when" in p: self.a("  }")
            self.a("}")

    # ---- D9REC_TRACE: one line before forwarding every Lock*/Unlock*/GetDC/ReleaseDC and one after (d9s_tr is a no-op unless d9s_trace)
    def tr_args(self):
        f, a = [], []
        for q in self.P:
            n, k = q["name"], q["kind"]
            if q["byref"] or k in ("buffer", "locked", "handle", "ohandle"):
                if k == "struct" and q["struct"] in ("RECT", "D3DBOX"): f.append(f"{n}=%s"); a.append(f"d9s_boxs((const void *){n}, {4 if q['struct'] == 'RECT' else 6})")
                continue
            if k in ("value", "enum", "flags"): f.append(f"{n}=%x"); a.append(f"(unsigned)(uint32_t){n}")
        return ("".join(" " + x for x in f), "".join(", " + x for x in a))

    def trace(self, tag, extra_f="", extra_a=""):
        f, a = self.tr_args(); nm = f"{self.iface}::{self.m['name']}"
        self.a(f'if (d9s_trace) d9s_tr("d9rec: {tag} {nm} this=%p id=%u{f}{extra_f}", (void *)this, o ? o->id : 0u{a}{extra_a});')

    # ---- locks
    def lock_pre(self, p):
        L, sh, c = p["lock"], p["lock"]["shape"], self.cpre
        k = [f"(uint32_t){a}" for a, b in L["match"]] + ["0", "0"]
        self.a(f"uint32_t d9_k0 = {k[0]}, d9_k1 = {k[1]}; int d9_lcls = d9s_taint(o, d9_k0, d9_k1) ? 2 : 0;")
        if sh == "linear": self.a(f"uint64_t d9_off = {c.c(L['off'])}, d9_bytes = {c.c(L['bytes'])};")
        elif sh in ("rect", "dc"): self.a(f"uint64_t d9_fmt = {c.c(L['fmt'])}, d9_w = {c.c(L['w'])}, d9_h = {c.c(L['h'])};")
        elif sh == "box": self.a(f"uint64_t d9_fmt = {c.c(L['fmt'])}, d9_w = {c.c(L['w'])}, d9_h = {c.c(L['h'])}, d9_d = {c.c(L['d'])};")
        else: self.err(f"lock shape {sh} has no emitter")
        self.a(f"int d9_onl = ({c.c(L['on_lock'])}) != 0, d9_onu = ({c.c(L['on_unlock'])}) != 0;")

    def lock_out(self, p):
        n, L, sh = p["name"], p["lock"], p["lock"]["shape"]
        self.a(f"{{ bool d9_c = ({n} != 0) && d9_ret >= 0; D9Lk d9_l; memset(&d9_l, 0, sizeof d9_l);")
        self.a("  d9_l.k0 = d9_k0; d9_l.k1 = d9_k1; d9_l.onunlock = d9_onu; d9_l.cls = d9_lcls;")
        pit = []
        if sh == "linear":
            self.a(f"  if (d9_c) {{ d9_l.base = (uintptr_t)*{n}; d9_l.rows = 1; d9_l.rowbytes = d9_bytes; d9_l.depth = 1; }}")
        elif sh == "dc":
            self.a(f"  if (d9_c) {{ d9_l.base = (uintptr_t)*{n}; d9_l.dc = 1; d9_l.fmt = d9_fmt; d9_l.w = d9_w; d9_l.h = d9_h; d9_l.rows = d9g_rows(d9_fmt, d9_h); d9_l.rowbytes = d9g_rowbytes(d9_fmt, d9_w); d9_l.depth = 1; }}")
        else:
            st = self.spec["structs"][p["struct"]]["fields"]
            pf = [f["name"] for f in st if f["kind"] == "ptr"]; pit = [f["name"] for f in st if f["kind"] == "int"]
            if len(pf) != 1 or len(pit) != (1 if sh == "rect" else 2): self.err(f"locked struct {p['struct']} does not fit shape {sh}")
            s2 = f" d9_l.spitch = (size_t){n}->{pit[1]};" if sh == "box" else ""
            self.a(f"  if (d9_c) {{ d9_l.base = (uintptr_t){n}->{pf[0]}; d9_l.pitch = (size_t){n}->{pit[0]};{s2} d9_l.rows = d9g_rows(d9_fmt, d9_h); "
                   f"d9_l.rowbytes = d9g_rowbytes(d9_fmt, d9_w); d9_l.depth = {'d9_d' if sh == 'box' else 1}; }}")
        self.a("  if (d9s_trace) { " + "d9s_tr(\"d9rec: < %s::%s hr=%08x ptr=%p pitch=%u spitch=%u bytes=%u\", \"" + self.iface + "\", \"" + self.m["name"] + "\", (unsigned)d9_ret, (void *)d9_l.base, (unsigned)d9_l.pitch, (unsigned)d9_l.spitch, d9_c ? (unsigned)d9s_image(d9_l, 0) : 0u); }")
        self.a("  d9_rec.hdr(0, d9_c); if (d9_c) d9_rec.tokn(d9_l.base);")
        for f in pit:
            fc = p.get("field_cls", {}).get(f)
            if fc not in CLS: self.err(f"locked field {f} has no class (field_cls)")
            self.a(f"  d9_rec.hdr({CLS[fc]}, d9_c); if (d9_c) d9_rec.w((uint32_t){n}->{f});")
        if p["cls"] != "by:taint": self.err(f"locked param {n}: class {p['cls']} has no emitter")
        self.a(f"  d9_rec.hdr(d9_lcls, d9_c && d9_onl); if (d9_c && d9_onl) d9_rec.img(d9_l);")
        self.a("  if (d9s_trace && d9_c) d9s_tr(\"d9rec: = snapshot of the lock bytes done\");")
        self.a("  if (d9_c) d9s_lock_add(o, d9_l); }")

    def closes(self):
        lm = [mm for mm in self.spec["methods"] if mm["iface"] == self.iface and mm["name"] in self.m["closes"]]
        lk = [q for mm in lm for q in mm["params"] if q["kind"] == "locked"]
        if len(lk) != 1: self.err("closes: expected exactly one locked param in " + ",".join(self.m["closes"]))
        k = [f"(uint32_t){b}" for a, b in lk[0]["lock"]["match"]] + ["0", "0"]
        pn = {q["name"] for q in self.P}
        for a, b in lk[0]["lock"]["match"]:
            if b not in pn: self.err(f"closes: unlock parameter {b} missing")
        return k

    # ---- the method
    def emit(self):
        m, P = self.m, self.P; rk = m["ret_kind"]; ret = m["ret"]
        guid = [q for q in P if q["ctype"] in REFS]
        if self.export is None:
            sig = ", ".join(f"{q['ctype']} {q['name']}" for q in P)
            self.L.append(f"{ret} STDMETHODCALLTYPE W_{self.iface}::{m['name']}({sig}) {{")
            self.a(f"{self.iface} *r = this->r; D9Obj *o = this->o; (void)r; (void)o;")
            self.a(f"D9Rec d9_rec(D9I_{self.iface}, {m['slot']}, o);")
        else:
            sig = ", ".join([f"{ret} (WINAPI *d9_real)({', '.join(q['ctype'] for q in P) or 'void'})"] + [f"{q['ctype']} {q['name']}" for q in P])
            self.L.append(f"{ret} d9wrap_{m['name']}({sig}) {{")
            self.a("D9Obj *o = 0; (void)o;"); self.a(f"D9Rec d9_rec(0xFFFF, {self.export}, o);")
        locks = [q for q in P if q["kind"] == "locked"]
        mark = len(self.L)                                       # deref snapshots are inserted here
        for q in locks: self.lock_pre(q)
        self.inputs()
        if "closes" in m:
            k = self.closes()
            self.a(f"uint32_t d9_k0 = {k[0]}, d9_k1 = {k[1]}; D9Lk *d9_lk = d9s_lock_find(o, d9_k0, d9_k1);")
            self.trace(">", " lock=%s snap=%u", ', d9_lk ? "open" : "NONE", d9_lk && d9_lk->onunlock ? (unsigned)d9s_image(*d9_lk, 0) : 0u')
            self.a("if (d9_lk && d9_lk->onunlock) d9s_lock_snap(d9_lk);")
            self.trace("=", " snapshot taken, calling the real call")
        args, frees = [], []
        for q in P:
            n = q["name"]
            if q["kind"] == "handle" and q["dir"] == "out":
                if q["ctype"][-2:] != "**": self.err(f"out handle {n}: ctype {q['ctype']}")
                self.a(f"{q['ctype'][:-1]} d9_h_{n} = 0; if ({n}) *{n} = 0;"); args.append(f"({n} ? &d9_h_{n} : 0)")
            elif q["kind"] == "handle" and q["dir"] == "in" and not q["byref"]: args.append(f"({q['ctype']})d9s_real({n})")
            elif q["kind"] == "handle": self.err(f"param {n}: handle dir {q['dir']} has no emitter")
            elif q["kind"] == "buffer" and q["dir"] == "in" and q.get("canon", {}).get("as") == "handle":   # wrapper pointers in the array -> real pointers
                if "off" in q: self.err(f"param {n}: canon handle with off has no emitter")
                self.a(f"uint32_t *d9_tc_{n} = 0; if ({n} && {self.cpre.c(q['canon']['when'])}) {{ uint64_t d9_len = {self.cpre.c(q['len'])}; d9_tc_{n} = (uint32_t *)malloc(d9_len + 4); memcpy(d9_tc_{n}, {n}, d9_len);")
                self.a(f"  for (uint64_t i = 0; i < d9_len / 4; i++) d9_tc_{n}[i] = (uint32_t)(uintptr_t)d9s_real((void *)(uintptr_t)d9_tc_{n}[i]); }}")
                args.append(f"(d9_tc_{n} ? ({q['ctype']})d9_tc_{n} : {n})"); frees.append(f"free(d9_tc_{n});")
            else: args.append(n)
        if any(q.get("iface") == "$riid" for q in P):
            if len(guid) != 1: self.err("$riid without exactly one REFIID param")
            self.a(f"int d9_ifx = d9s_iid_iface(&{guid[0]['name']});")
        if locks: self.trace(">", " k0=%u k1=%u, calling the real call", ", d9_k0, d9_k1")
        call = f"r->{m['name']}({', '.join(args)})" if self.export is None else f"d9_real({', '.join(args)})"
        if rk == "void": self.a(call + ";")
        elif rk == "handle": self.a(f"{ret} d9_r = {call}; {ret} d9_ret = ({ret})d9s_wrap_out(d9_r, {self.ifx(m['iface'])});")
        else: self.a(f"{ret} d9_ret = {call};")
        for f in frees: self.a(f)
        for q in P:                                              # wrap out handles, link children
            if q["kind"] == "handle" and q["dir"] == "out":
                n, ix = q["name"], ("d9_ifx" if q["iface"] == "$riid" else self.ifx(q["iface"]))
                self.a(f"if ({n}) *{n} = ({q['ctype'][:-1]})d9s_wrap_out(d9_h_{n}, {ix});")
                kk = self.child_keys(q)
                if kk: self.a(f"if ({n} && *{n}) d9s_child(*{n}, o, {kk[0]}, {kk[1]});")
        self.hooks(guid)
        if "closes" in m: self.trace("<", " hr=%08x", ", (unsigned)d9_ret")
        if rk != "void":
            self.a(f"d9_rec.hdr({CLS[m['ret_cls']]}, 1);")
            self.a({"hresult": "d9_rec.w((uint32_t)d9_ret);", "enum": "d9_rec.w((uint32_t)d9_ret);", "value": "d9_rec.w((uint32_t)d9_ret);", "float": "d9_rec.w(d9s_fbits(d9_ret));",
                    "ohandle": "d9_rec.tok((uintptr_t)d9_ret);", "handle": "d9_rec.obj(d9_ret);"}.get(rk) or self.err(f"ret_kind {rk} has no emitter"))
        for q in P:
            if q["dir"] == "in": continue
            n, k = q["name"], q["kind"]
            if k == "locked": self.lock_out(q); continue
            w = q.get("when")
            if w not in ("ok", "always"): self.err(f"param {n}: when {w}")
            if w == "ok" and not self.hr and m["ret_kind"] != "void": self.err(f"param {n}: when ok in a non-HRESULT, non-void method")
            okc = " && d9_ret >= 0" if w == "ok" and self.hr else ""   # a void method cannot fail: `ok` holds (GetGammaRamp)
            if q["cls"] not in CLS and q["cls"] != "by:query_type": self.err(f"param {n}: class {q['cls']} has no emitter")
            cls = "d9g_qtype_cls((uint32_t)d9g_prop(o, D9P_QTYPE))" if q["cls"] == "by:query_type" else str(CLS[q["cls"]])
            self.a(f"{{ bool d9_c = ({n} != 0){okc}; d9_rec.hdr({cls}, d9_c); if (d9_c) {{")
            if k == "handle": self.a(f"d9_rec.obj(*{n});")
            else: self.pointee(q, self.cpost)
            self.a("} }")
        if "closes" in m:
            self.a("{ bool d9_c = d9_lk && d9_ret >= 0; d9_rec.hdr(0, d9_c); if (d9_c) d9_rec.tokd(d9_lk->base);")
            self.a("  d9_rec.hdr(d9_lk ? d9_lk->cls : 0, d9_c && d9_lk->onunlock); if (d9_c && d9_lk->onunlock) d9_rec.buf(d9_lk->snap, d9_lk->snaplen);")
            self.a("  if (d9_c) d9s_lock_del(o, d9_lk); }")
        if self.export is None and m["name"] == "Release" and m["slot"] == 2: self.a("if (d9_ret == 0) d9_rec.retire(o);")
        self.a("d9_rec.commit();")
        if rk != "void": self.a("return d9_ret;")
        self.L.append("}")
        pre = sorted(self.cpre.pre | self.cpost.pre)
        snaps = [f"    uint64_t d9_pre_{n} = {n} ? (uint64_t)(uint32_t)*{n} : 0;" for n in pre]
        self.L[mark:mark] = snaps
        return self.L, self.cpre.used_props | self.cpost.used_props

    def child_keys(self, q):
        """A surface/volume handed out by a container (texture) is the sub-resource keyed by the container's lock-match parameters."""
        if q["iface"] not in ("IDirect3DSurface9", "IDirect3DVolume9") or self.export is not None: return None
        lk = [x for mm in self.spec["methods"] if mm["iface"] == self.iface for x in mm["params"] if x["kind"] == "locked" and x["lock"]["match"]]
        if not lk: return None
        pn = {x["name"] for x in self.P}; names = [b for a, b in lk[0]["lock"]["match"]]
        for b in names:
            if b not in pn: self.err(f"child getter lacks parameter {b} (unlock-match name of {self.iface})")
        names += ["0", "0"]
        return [f"(uint32_t){names[0]}", f"(uint32_t){names[1]}"]

    def hooks(self, guid):
        m, P = self.m, self.P
        if self.export is None and m["name"] == "SetPrivateData":
            fl = [q for q in P if q["kind"] == "flags" and q["flags"] == "D3DSPD"]
            if len(guid) != 1 or len(fl) != 1: self.err("SetPrivateData: GUID/flags params not found")
            self.a(f"if (d9_ret >= 0) d9s_pd_set(o, &{guid[0]['name']}, ({fl[0]['name']} & D3DSPD_IUNKNOWN) != 0);")
        if self.export is None and m["name"] == "FreePrivateData":
            if len(guid) != 1: self.err("FreePrivateData: GUID param not found")
            self.a(f"if (d9_ret >= 0) d9s_pd_free(o, &{guid[0]['name']});")
        for e in m.get("effects", []):
            for kind, args in re.findall(r"(pix|copy)\(([^)]*)\)", e):
                a = [x.strip() for x in args.split(",")]; ok = "d9_ret >= 0" if self.hr else "true"
                hs = {q["name"] for q in P if q["kind"] == "handle" and q["dir"] == "in"}
                if kind == "pix" and a[0] in ("rendertargets", "depthstencil"):
                    self.a(f"/* {e}: render targets are created with RENDERTARGET/DEPTHSTENCIL usage (or are back buffers / the auto depth), initially pix by the usage rule */")
                elif kind == "pix" and a[0] == "this": self.a(f"if ({ok}) d9s_pix(o);")
                elif kind == "pix" and a[0] in hs: self.a(f"if ({ok}) d9s_pix(d9s_obj({a[0]}));")
                elif kind == "copy" and len(a) == 2 and a[0] in hs and a[1] in hs: self.a(f"if ({ok}) d9s_copy(d9s_obj({a[0]}), d9s_obj({a[1]}));")
                else: self.err(f"effect {e}: no emitter")
