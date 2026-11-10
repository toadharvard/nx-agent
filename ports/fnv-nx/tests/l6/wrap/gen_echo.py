"""gen_echo.py: emits echo.cpp from the plan: an ECHO implementation of every D3D9 interface (answers = the plan's blob; property getters answer
from the object profile) and the DRIVER that calls every method of every interface with the plan's arguments through the generated wrapper."""
import re, struct
from plan import PROF, SHADER, DECL, arena_write

NSZ = {"value": 4, "enum": 4, "flags": 4, "float": 4, "ohandle": 4}


def hexs(b): return ",".join(str(x) for x in b) or "0"


class Echo:
    def __init__(self, plan, spec, mut=None):
        self.pl, self.spec, self.names, self.mut = plan, spec, plan.names, mut; self.blob = bytearray(); self.offs = []; self.L = []; self.D = []

    def item(self, b): self.blob += struct.pack("<I", len(b)) + b + b"\0" * (-len(b) & 3)

    def build_blob(self):
        for c in self.pl.calls:
            self.offs.append(len(self.blob)); m = c["m"]; mutated = False
            for p in m["params"]:
                if p["dir"] == "in": continue
                n, v = p["name"], c["outs"].get(p["name"])
                k = p["kind"]
                if k == "handle": self.item(struct.pack("<2I", v or 0, self.names.index(self.pl.objs[v].iface) if v else 0))
                elif k == "locked": self.item(struct.pack("<5I", v["pitch"], v["spitch"], v["slot"], v["base_off"], v["hdc"]))
                elif k in ("struct", "buffer"): self.item(self.mutate(c, bytearray(v or b"")))
                else: self.item(self.mutate(c, bytearray(struct.pack("<I", v & 0xFFFFFFFF))))
            if m["ret_kind"] == "handle": self.item(struct.pack("<2I", c["retv"], self.names.index("IDirect3D9")))
            elif m["ret_kind"] != "void": self.item(struct.pack("<I", c["retv"] & 0xFFFFFFFF))

    def mutate(self, c, b):
        if self.mut and self.mut[0] == "out" and c["k"] == self.mut[1] and b: b[0] ^= 1
        return bytes(b)

    # ---- echo method / export body
    def body(self, m, iface, exp):
        P, hr = m["params"], m["ret_kind"] == "hresult"; outs = [p for p in P if p["dir"] != "in"]; rk = m["ret_kind"]
        cnt = len(outs) + (0 if rk == "void" else 1); ret = m["ret"]; L = []
        if iface and (iface, m["name"]) in PROF: return self.prof_body(m, iface)
        L.append(f"EB b; E_load(b, {cnt}); (void)b; {{ unsigned sk = g_k; echo_nested(sk); g_k = sk; }}" if iface else f"EB b; E_load(b, {cnt}); (void)b;")
        if rk != "void": L.append(f"uint32_t e_ret = *(const uint32_t *)b.p[{cnt - 1}]; ")
        for j, p in enumerate(outs):
            n, k = p["name"], p["kind"]; w = "((int32_t)e_ret >= 0)" if p.get("when") == "ok" and hr else "true"
            if k == "handle": L.append(f"if ({n}) *{n} = ({p['ctype'][:-1]})echo_obj(*(const uint32_t *)b.p[{j}], *(const uint32_t *)(b.p[{j}] + 4));")
            elif k == "locked":
                q = f"const uint32_t *l = (const uint32_t *)b.p[{j}]; E_arena(l[2]); unsigned char *bits = g_arena[l[2]] + l[3];"
                sh = p["lock"]["shape"]; st = self.spec["structs"].get(p.get("struct", ""), {"fields": []})["fields"]
                pf = [f["name"] for f in st if f["kind"] == "ptr"]; pit = [f["name"] for f in st if f["kind"] == "int"]
                if sh == "linear": a = f"*(void **){n} = bits;"
                elif sh == "dc": a = f"*(HDC *){n} = (HDC)(uintptr_t)l[4];"
                else: a = f"{n}->{pit[0]} = l[0]; " + (f"{n}->{pit[1]} = l[1]; " if sh == "box" else "") + f"{n}->{pf[0]} = bits;"
                L.append(f"if ({n} && {w}) {{ {q} {a} }}")
            elif k == "struct": L.append(f"if ({n} && {w}) memcpy({n}, b.p[{j}], b.n[{j}]);")
            elif k == "buffer": L.append(f"if ({n} && {w} && b.n[{j}]) memcpy({n}, b.p[{j}], b.n[{j}]);")
            else: L.append(f"if ({n} && {w}) memcpy({n}, b.p[{j}], 4);")
        if rk == "void": return L
        if rk == "handle": L.append(f"return ({ret})echo_obj(e_ret, *(const uint32_t *)(b.p[{cnt - 1}] + 4));")
        elif iface and m["name"] == "Release" and m["slot"] == 2: L.append("if (e_ret == 0) { g_eobj[num] = 0; delete this; return 0; } return e_ret;")
        elif rk == "float": L.append(f"{ret} f; memcpy(&f, &e_ret, 4); return f;")
        elif rk == "ohandle": L.append(f"return ({ret})(uintptr_t)e_ret;")
        else: L.append(f"return ({ret})e_ret;")
        return L

    def prof_body(self, m, iface):
        n = m["name"]; P = m["params"]
        if n in ("GetDesc", "GetLevelDesc"): return [f"memcpy({P[-1]['name']}, kDesc[num], sizeof(*{P[-1]['name']}));", "return 0;"]
        if n == "GetLevelCount": return ["return 3;"]
        if n == "GetDataSize": return ["return 4;"]
        if n == "GetType": return ["return (D3DQUERYTYPE)8;"]
        d, sz = ("pData", "pSizeOfData") if n == "GetFunction" else ("pDecl", "pNumElements")
        blob, cnt = ("kShader", "8") if n == "GetFunction" else ("kDecl", "2")
        return [f"if ({d}) memcpy({d}, {blob}, sizeof {blob}); *{sz} = {cnt}; return 0;"]

    # ---- driver call
    def drive(self, c):
        m, k, a, P = c["m"], c["k"], c["args"], c["m"]["params"]; D = ["{ g_k = %d;" % k]; args = []; post = []
        mu = self.mut and self.mut[1] == k
        for i, p in enumerate(P):
            n, kd, v = p["name"], p["kind"], a.get(p["name"]); an = f"A{k}_{i}"
            ptr = p["byref"] or kd in ("buffer", "locked", "struct")
            if not ptr:
                if kd == "float": args.append(f"(float){v!r}")
                elif kd == "handle": args.append(f"({p['ctype']})O[{0 if mu and self.mut[0] == 'handle' else v}]")
                elif kd == "ohandle": args.append(f"({p['ctype']})(uintptr_t){v}u")
                else: args.append(f"({p['ctype']})({v + (1 if mu and self.mut[0] == 'in' else 0)}u)")
                continue
            if kd == "handle":
                D.append(f"void *H{k}_{i} = 0;"); args.append(f"({p['ctype']})&H{k}_{i}")
                nm = c["outs"].get(n); post.append(f"if (H{k}_{i}) {{ if (O[{nm}] && O[{nm}] != H{k}_{i}) ++g_ident_fail; O[{nm}] = H{k}_{i}; }}")
                continue
            ok = c["hr"] == 0 or p.get("when") == "always" or p["dir"] == "in"; out = c["outs"].get(n)
            if kd in ("buffer", "struct") and n in a and v is None:
                args.append(f"({p['ctype']})0"); continue
            if p["dir"] == "in" and kd in ("struct", "buffer"): init = v
            elif p["dir"] == "in": init = struct.pack("<I", v)
            elif p["dir"] == "inout": init = struct.pack("<I", v) if isinstance(v, int) else v
            elif kd == "buffer": init = b"\xee" * (len(out or b"") + 16)
            elif kd == "struct": init = b"\xee" * self.spec["structs"][p["struct"]]["size"]
            else: init = b"\xee" * 32
            init = bytes(init)
            if mu and self.mut[0] == "in" and kd in ("struct", "buffer") and init: init = bytes([init[0] ^ 1]) + init[1:]
            D.append(f"alignas(8) unsigned char {an}[{len(init) + 8}] = {{{hexs(init)}}};")
            args.append(f"*(const GUID *){an}" if p["ctype"] in ("REFIID", "REFGUID") else f"({p['ctype']}){an}")
            if kd == "buffer" and p["dir"] == "in" and n in c.get("objbuf", {}):
                D.append("".join(f"((void **){an})[{x}] = O[{num}];" for x, num in enumerate(c["objbuf"][n])))
            if p["dir"] != "in" and ok and out is not None and kd != "locked":
                exp = out if isinstance(out, bytes) else struct.pack("<I", out)
                post.append(f"if (memcmp({an}, (const unsigned char[]){{{hexs(exp)}}}, {len(exp)})) ++g_fwd_fail;")
            if kd == "locked" and c["hr"] == 0:
                d = out; sh = d["sh"]; st = self.spec["structs"].get(p.get("struct", ""), {"fields": []})["fields"]
                if sh != "dc":
                    off = next((f["off"] for f in st if f["kind"] == "ptr"), 0)
                    wr = (f"{{ unsigned char *bs = *(unsigned char **)({an} + {off}); for (unsigned dd = 0; dd < {d['depth']}; dd++) for (unsigned r = 0; r < {d['rows']}; r++) for (unsigned i = 0; i < {d['rowbytes']}; i++) "
                          f"{{ unsigned rel = dd * {d['spitch']} + r * {d['pitch']} + i; bs[rel] = ({d['base_off']} + rel) * 5 + 3 + {d['k']}; }} }}")
                    post.append(wr)
        call = (f"d9wrap_{m['name']}(real_{m['name']}{''.join(', ' + x for x in args)})" if c["export"] is not None
                else f"(({c['iface']}*)O[{c['this']}])->{m['name']}({', '.join(args)})")
        rk = m["ret_kind"]
        if rk == "void": D.append(call + ";")
        elif rk == "handle": D.append(f"void *r = (void *){call}; O[{c['retv']}] = r;")
        elif rk == "float": D.append(f"float r = {call}; uint32_t rb; memcpy(&rb, &r, 4); if (rb != {c['retv'] & 0xFFFFFFFF}u) ++g_ret_fail;")
        else: D.append(f"uint32_t r = (uint32_t)(uintptr_t){call}; if (r != {c['retv'] & 0xFFFFFFFF}u) ++g_ret_fail;")
        if c.get("thread"): return ["{ std::thread th([&] {"] + D + post + ["} }); th.join(); }"]
        return D + post + ["}"]

    def render(self):
        self.build_blob(); pl, spec = self.pl, self.spec; o = []
        o += ['#include "d9wrap.gen.h"', "#include <stdio.h>", "#include <thread>", f"static const unsigned NOBJ = {len(pl.objs) + 2}, NSLOT = {pl.slot + 1}; static unsigned g_k; static unsigned long g_ident_fail, g_ret_fail, g_fwd_fail;",
              "static void *O[NOBJ]; static void *g_eobj[NOBJ]; static unsigned char g_arena[NSLOT][65536];",
              "extern const unsigned char kBlob[]; extern const unsigned kOff[];", "struct EB { const unsigned char *p[16]; unsigned n[16]; };",
              "static void E_load(EB &b, unsigned cnt) { const unsigned char *q = kBlob + kOff[g_k]; for (unsigned i = 0; i < cnt; i++) { unsigned len = *(const unsigned *)q; b.p[i] = q + 4; b.n[i] = len; q += 4 + ((len + 3) & ~3u); } }",
              "static void E_arena(unsigned s) { for (unsigned j = 0; j < 65536; j++) g_arena[s][j] = (s * 31 + j * 7) & 255; }",
              "static void *echo_obj(unsigned num, unsigned iface);", "static void echo_nested(unsigned k) { switch (k) {" + "".join(f"case {n['nested_in']}: " + " ".join(self.drive(n)) + " break;" for n in pl.calls if "nested_in" in n) + "default: break; } }", "void d9port_dc_pixels(void *hdc, uint32_t, uint32_t, uint32_t, void *dst, size_t n) { for (size_t i = 0; i < n; i++) ((unsigned char *)dst)[i] = ((uintptr_t)hdc * 3 + i * 11) & 255; }",
              f"static const unsigned char kShader[] = {{{hexs(SHADER)}}}, kDecl[] = {{{hexs(DECL)}}};"]
        descs = {}
        for ob in pl.objs.values():
            mm = [m for m in pl.meths if m["iface"] == ob.iface and m["name"] in ("GetDesc", "GetLevelDesc") and (ob.iface, m["name"]) in PROF]
            descs[ob.num] = pl.desc_image(ob, next(q["struct"] for q in mm[0]["params"] if q["kind"] == "struct")) if mm else b""
        w = max(len(d) for d in descs.values()) + 1
        o.append(f"static const unsigned char kDesc[NOBJ][{w}] = {{{{0}}, " + ", ".join("{" + hexs(descs[n]) + "}" for n in sorted(descs)) + "};")
        by = {n: [m for m in pl.meths if m["iface"] == n] for n in self.names}
        for n in self.names:
            o.append(f"struct E_{n} : {n} {{ unsigned num; E_{n}(unsigned x) : num(x) {{}}")
            for m in by[n]:
                o.append(f"  {m['ret']} STDMETHODCALLTYPE {m['name']}({', '.join(q['ctype'] + ' ' + q['name'] for q in m['params'])}) override {{ " + " ".join(self.body(m, n, False)) + " }")
            o.append("};")
        o.append("static void *echo_obj(unsigned num, unsigned iface) { if (!num) return 0; if (g_eobj[num]) return g_eobj[num]; switch (iface) {")
        for i, n in enumerate(self.names): o.append(f"case {i}: g_eobj[num] = (void *)({n} *)new E_{n}(num); break;")
        o.append("} return g_eobj[num]; }")
        for i, e in enumerate(spec["exports"]):
            if "params" in e and "ret_kind" in e:
                o.append(f"static {e['ret']} {'WINAPI ' if False else ''}real_{e['name']}({', '.join(q['ctype'] + ' ' + q['name'] for q in e['params']) or 'void'}) {{ " + " ".join(self.body(e, None, True)) + " }")
        o.append("static void sink(void *ctx, const void *p, size_t n) { fwrite(p, 1, n, (FILE *)ctx); }")
        o.append("static const unsigned kBlobLen = %d;" % len(self.blob))
        o.append("int main(int argc, char **argv) { FILE *f = fopen(argv[1], \"wb\"); d9s_open(sink, f, d9wrap_spec_sha256);")
        for c in pl.calls:
            if "nested_in" not in c: o += self.drive(c)
        o.append('fclose(f); printf("DRIVER calls=%u ident_fail=%lu ret_fail=%lu fwd_fail=%lu\\n", ' + str(len(pl.calls)) + ', g_ident_fail, g_ret_fail, g_fwd_fail); return 0; }')
        o.append("const unsigned kOff[] = {" + ",".join(map(str, self.offs)) + "};")
        o.append("const unsigned char kBlob[] = {" + hexs(self.blob) + "};")
        return "\n".join(o) + "\n"
