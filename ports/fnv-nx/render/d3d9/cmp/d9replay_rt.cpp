// d9replay_rt.cpp: G-REPLAY runtime.  d9replay IN.d9s OUT.d9s: re-issues every depth-0 record of IN through the generated wrapper against the linked
// implementation (d9impl_entries) on this one thread, in file order; the wrapper writes OUT; d9cmp.py compares IN and OUT.  Rules: README.md.
#include "d9replay_rt.h"
#include <stdio.h>
#include <map>
#include <string>
#include <string_view>
#include <vector>
typedef std::vector<uint8_t> Bytes;
static unsigned g_seq; static const char *g_name = "";
[[noreturn]] static void die(const char *m) { fprintf(stderr, "d9replay: seq %u %s: %s\n", g_seq, g_name, m); exit(2); }
struct Rd { const uint8_t *d; size_t p, end;
    uint32_t w() { if (p + 4 > end) die("truncated record"); uint32_t v; memcpy(&v, d + p, 4); p += 4; return v; }
    const uint8_t *raw(unsigned n) { const uint8_t *r = d + p; p += (n + 3) & ~3u; if (p > end) die("truncated payload"); return r; } };
struct Pv { uint32_t present = 0, iw = 1; const uint8_t *d = 0; unsigned n = 0; };
struct Po { uint32_t cls = 0, rec = 0; const uint8_t *d = 0; unsigned n = 0; uint32_t word() const { uint32_t v = 0; if (n >= 4) memcpy(&v, d, 4); return v; } };
struct Lk { Po tok, pitch[2], data; };
static unsigned psize(const D9SDesc *s) { unsigned n = 0; for (unsigned i = 0; i < s->n; i++) { const D9SField &f = s->f[i]; n += f.kind == 1 ? 4 : f.kind == 3 ? f.count * psize(f.sub) : f.size; } return n; }

// ---- object / token state
static std::map<unsigned, std::vector<std::pair<int, void *>>> g_obj;      // recorded id -> live wrapper pointers by interface
static std::map<unsigned, uintptr_t> g_tok;
static uintptr_t tokval(unsigned t) { if (!t) return 0; auto i = g_tok.find(t); return i != g_tok.end() ? i->second : d9impl_ohandle(t); }
static void *unk(unsigned t) { return t ? (void *)d9impl_ohandle(t) : 0; }
static void bind(unsigned id, int iface, void *p) { if (!id || !p) return; for (auto &e : g_obj[id]) if (e.first == iface) { e.second = p; goto done; } g_obj[id].push_back({iface, p});
    done: d9impl_bind(id, p); }
static void *ptr_for(unsigned id, int iface) {
    if (!id) return 0; auto &v = g_obj[id];
    for (auto &e : v) if (e.first == iface) return e.second;
    for (auto &e : v) if (e.first >= 0 && d9g_isa(e.first, iface)) return e.second;
    die(id ? "no live object of the needed interface for a recorded object id" : "");
}
static void dec_struct(const D9SDesc *d, const uint8_t *&s, uint8_t *m) {
    for (unsigned i = 0; i < d->n; i++) { const D9SField &f = d->f[i];
        if (f.kind == 0) { memcpy(m + f.off, s, f.size); s += f.size; }
        else if (f.kind == 1) { uint32_t t, v; memcpy(&t, s, 4); v = (uint32_t)tokval(t); memcpy(m + f.off, &v, 4); s += 4; }
        else if (f.kind == 2) s += f.size;
        else for (unsigned c = 0; c < f.count; c++) dec_struct(f.sub, s, m + f.off + c * (f.size / f.count)); }
}

// ---- record parsing (generic, from the method descriptor)
struct Parsed { std::vector<Pv> in; std::vector<Po> out; std::vector<Lk> lk; uint32_t retcls = 0, retv = 0; Po ctok, cdata; };
static void pointee(Rd &r, const D9RP &p, Pv &v) {
    switch (p.kind) { case K_STRUCT: v.n = psize(p.sd); v.d = r.raw(v.n); break; case K_BUFFER: v.n = r.w(); v.d = r.raw(v.n); break; default: v.n = 4; v.d = r.raw(4); }
}
static void parse(Rd &r, const D9RM &m, Parsed &x) {
    x.in.resize(m.np); x.out.resize(m.np); x.lk.resize(m.np);
    for (unsigned i = 0; i < m.np; i++) { const D9RP &p = m.p[i]; Pv &v = x.in[i]; bool ptr = p.byref || p.kind == K_STRUCT || p.kind == K_BUFFER || p.kind == K_LOCKED;
        if (!ptr) { v.present = 1; pointee(r, p, v); continue; }
        v.present = r.w();
        if (v.present && p.dir != DIR_OUT && p.kind != K_LOCKED) { if (p.in_when) { v.iw = r.w(); if (!v.iw) continue; } pointee(r, p, v); } }
    if (m.ret_kind != RK_VOID) { x.retcls = r.w() & 255; x.retv = r.w(); }
    auto hdr = [&](Po &o, bool buf, unsigned sz) { uint32_t h = r.w(); o.cls = h & 255; o.rec = h >> 8; if (!o.rec) return; if (buf) { o.n = r.w(); o.d = r.raw(o.n); } else { o.n = sz; o.d = r.raw(sz); } };
    for (unsigned i = 0; i < m.np; i++) { const D9RP &p = m.p[i]; if (p.dir == DIR_IN) continue;
        if (p.kind == K_LOCKED) { Lk &l = x.lk[i]; hdr(l.tok, false, 4); for (unsigned k = 0; k < p.npitch; k++) hdr(l.pitch[k], false, 4); hdr(l.data, true, 0); continue; }
        hdr(x.out[i], p.kind == K_BUFFER, p.kind == K_STRUCT ? psize(p.sd) : 4); }
    if (m.closes) { hdr(x.ctok, false, 4); hdr(x.cdata, true, 0); }
    if (r.p != r.end) die("record body length does not match the spec");
}

// ---- one call
static const unsigned kScratch = 65536;      // capacity of every out/inout buffer (the recorded application buffer size is not in the stream)
static void issue(Rd &r, unsigned this_id, const D9RM &m, void *fn, unsigned ordinal, std::vector<void *> &mem, unsigned seq) {
    Parsed x; parse(r, m, x); std::vector<D9A> a(m.np + 1); memset(a.data(), 0, (m.np + 1) * sizeof(D9A));
    auto al = [&](size_t n) { void *p = calloc(n + 16, 1); mem.push_back(p); return p; };
    static unsigned char zero[kScratch + 16]; void *self = 0; D9Obj *o = 0;
    if (m.iface >= 0) { self = ptr_for(this_id, m.iface); o = d9s_obj(self); }
    for (int ph = 0; ph < 2; ph++) for (unsigned i = 0; i < m.np; i++) { const D9RP &p = m.p[i]; const Pv &v = x.in[i]; const Po &out = x.out[i];
        bool ptr = p.byref || p.kind == K_STRUCT || p.kind == K_BUFFER || p.kind == K_LOCKED, buf = p.kind == K_BUFFER;
        if ((ph == 1) != buf) { if (ph == 0 && buf && v.present) a[i].p = zero; continue; }       // buffers last (their rules read other params); zero until then
        if (!ptr) {
            if (p.kind == K_HANDLE) a[i].p = ptr_for(*(const uint32_t *)v.d, p.iface); else if (p.kind == K_OHANDLE) a[i].u = tokval(*(const uint32_t *)v.d);
            else { uint32_t u; memcpy(&u, v.d, 4); a[i].u = u; } continue; }
        if (!v.present) continue;
        if (p.kind == K_LOCKED) { a[i].p = al(p.ssz); continue; }
        if (p.kind == K_BUFFER) {
            uint64_t off = p.has_off ? m.ev(a.data(), o, 0, i) : 0; unsigned n = v.d ? v.n : 0, cap = n > out.n ? n : out.n; uint8_t *b = (uint8_t *)al(off + (cap > kScratch ? cap : kScratch));
            if (n && p.cano && m.ev(a.data(), o, 1, i)) for (unsigned k = 0; k + 4 <= n; k += 4) { uint32_t t; memcpy(&t, v.d + k, 4); uintptr_t w = p.cano == 2 ? (uintptr_t)ptr_for(t, p.iface) : (uintptr_t)unk(t); memcpy(b + off + k, &w, 4); if (p.cano == 2 && !t) memset(b + off + k, 0, 4); }
            else if (n) memcpy(b + off, v.d, n);
            a[i].p = b; continue; }
        uint8_t *b = (uint8_t *)al(p.kind == K_STRUCT ? p.ssz : 16); a[i].p = b;
        if (p.dir != DIR_OUT && v.d) { if (p.kind == K_STRUCT) { const uint8_t *s = v.d; dec_struct(p.sd, s, b); } else if (p.kind == K_OHANDLE) { uint32_t w = (uint32_t)tokval(*(const uint32_t *)v.d); memcpy(b, &w, 4); } else memcpy(b, v.d, 4); } }
    if (m.closes && x.cdata.rec && x.ctok.rec) {                                          // what the application wrote before Unlock goes into the live locked memory
        auto t = g_tok.find(x.ctok.word()); D9Obj *so = self ? d9s_obj(self) : 0;
        for (D9Lk *l = so ? so->locks : 0; t != g_tok.end() && l; l = l->next) if (l->base == t->second) { if (!l->dc)
            for (size_t d = 0; d < l->depth; d++) for (size_t rw = 0; rw < l->rows; rw++) { size_t src = (d * l->rows + rw) * l->rowbytes; if (src + l->rowbytes <= x.cdata.n) memcpy((char *)l->base + d * l->spitch + rw * l->pitch, x.cdata.d + src, l->rowbytes); } break; } }
    d9impl_before_call(ordinal, seq);
    uintptr_t ret = m.inv(self, fn, a.data());
    int riid = -1; if (m.riid_param >= 0 && a[m.riid_param].p) riid = d9s_iid_iface(a[m.riid_param].p);
    for (unsigned i = 0; i < m.np; i++) { const D9RP &p = m.p[i]; if (p.dir == DIR_IN || !a[i].p) continue;
        if (p.kind == K_HANDLE && x.out[i].rec) bind(x.out[i].word(), p.iface >= 0 ? p.iface : riid, *(void **)a[i].p);
        if (p.kind == K_LOCKED && x.lk[i].tok.rec) { uint8_t *s = (uint8_t *)a[i].p; g_tok[x.lk[i].tok.word()] = (uintptr_t)(p.lshape == 1 || p.lshape == 2 ? *(void **)(s + p.ptr_off) : *(void **)s); } }
    if (m.ret_kind == RK_HANDLE) bind(x.retv, m.ret_iface, (void *)ret);
    if (m.closes && x.ctok.rec) g_tok.erase(x.ctok.word());
}

static void sink(void *ctx, const void *p, size_t n) { fwrite(p, 1, n, (FILE *)ctx); }
int main(int argc, char **argv) {
    if (argc != 3) { fprintf(stderr, "usage: d9replay IN.d9s OUT.d9s\n"); return 2; }
    FILE *f = fopen(argv[1], "rb"); if (!f) { fprintf(stderr, "d9replay: cannot open %s\n", argv[1]); return 2; }
    Bytes in; { unsigned char t[65536]; size_t n; while ((n = fread(t, 1, sizeof t, f)) > 0) in.insert(in.end(), t, t + n); } fclose(f);
    if (in.size() < 44 || memcmp(in.data(), "D9S1", 4) || memcmp(in.data() + 8, d9wrap_spec_sha256, 32)) { fprintf(stderr, "d9replay: not a D9S1 stream of this spec\n"); return 2; }
    FILE *out = fopen(argv[2], "wb"); if (!out) return 2; d9s_open(sink, out, d9wrap_spec_sha256);
    std::map<std::string_view, void *> ent; for (const D9Entry *e = d9impl_entries; e->name; e++) ent[e->name] = e->fn;
    unsigned nrec = 0, issued = 0, nested = 0; std::map<std::pair<int, unsigned>, int> seen;
    for (size_t pos = 44; pos < in.size();) {
        Rd r{in.data(), pos, in.size()}; unsigned len = r.w(); g_seq = r.w(); r.w(); uint32_t w3 = r.w(), id = r.w(), depth = r.w(); if (len < 28 || pos + len > in.size()) die("bad record length");
        r.end = pos + len; pos += len; nrec++; g_name = "";
        if (depth) { nested++; continue; }
        unsigned nn = r.w(); r.p += 8 * nn; unsigned nr = r.w(); std::vector<unsigned> ret; for (unsigned i = 0; i < nr; i++) ret.push_back(r.w());
        int ifi = (w3 & 0xFFFF) == 0xFFFF ? -1 : (int)(w3 & 0xFFFF); unsigned slot = w3 >> 16;
        if (ifi >= 0 ? ((unsigned)ifi >= d9r_niface || d9r_ibase[ifi] + slot >= d9r_ibase[ifi + 1]) : slot >= d9r_nexports) die("record names a method outside the spec");
        const D9RM &m = ifi >= 0 ? d9r_methods[d9r_ibase[ifi] + slot] : d9r_exports[slot]; g_name = m.name; void *fn = 0;
        if (!m.inv) die("export has no signature in the spec");
        if (ifi < 0) { auto e = ent.find(m.name); if (e == ent.end()) die("the implementation's entry table has no such export"); fn = e->second; }
        std::vector<void *> mem; issue(r, id, m, fn, issued, mem, g_seq); for (void *p : mem) free(p); issued++; seen[{ifi, slot}] = 1;
        for (unsigned i : ret) g_obj.erase(i);
    }
    fclose(out); unsigned me = 0, ex = 0; for (auto &s : seen) (s.first.first >= 0 ? me : ex)++;
    printf("d9replay: records=%u issued=%u nested_skipped=%u methods=%u exports=%u\n", nrec, issued, nested, me, ex); return 0;
}
