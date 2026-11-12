// part d: shader creation, token walk, validator (python-encoded cases: sm_legal.txt / sm_illegal.txt), interface, GetFunction, hash; part a: shader creation rules
#include "t_common.h"
#include "res_core.h"
#include "fnvnx_sha256.h"
static const char* kErr[] = {"OK", "UNTERMINATED", "VERSION", "STAGE", "OPCODE", "OP_MODEL", "LENGTH", "DST_REG", "SRC_REG", "REG_INDEX", "SRCMOD", "DSTMOD", "RELADDR", "PRED", "DCL", "TEXLD_OPERAND"};
static int errcode(const char* s) { for (int i = 0; i < 16; i++) if (!strcmp(kErr[i], s)) return i; return -1; }
static uint32_t g_zero[(1 << 20) + 64];
static HRESULT mk(NxDevice9* d, int stage, const uint32_t* c, IUnknown** o) { return stage ? d->CreatePixelShader((const DWORD*)c, (IDirect3DPixelShader9**)o) : d->CreateVertexShader((const DWORD*)c, (IDirect3DVertexShader9**)o); }
static uint32_t* parse(char* s, uint32_t* n, uint32_t* buf) { *n = 0; for (char* p = strtok(s, " \n"); p; p = strtok(nullptr, " \n")) buf[(*n)++] = (uint32_t)strtoul(p, nullptr, 16); return buf; }
void t_shader() {
    static uint32_t buf[1 << 16]; static char line[1 << 19]; TEnv env = t_env(); NxDevice9* d = env.dev; IUnknown* o = nullptr;
    t_part('d'); int nlegal = 0, nacc = 0, nshown = 0, errh[16] = {};
    FILE* f = t_open("sm_legal.txt");
    while (f && fgets(line, sizeof line, f)) {
        char id[200]; int stage, off; if (sscanf(line, "%d %199s %n", &stage, id, &off) != 2 && off == 0) continue; uint32_t n; parse(line + off, &n, buf); nlegal++;
        NxSmIface ifc; int e = nx_sm_check(buf, n * 4, stage, &ifc); HRESULT hr = mk(d, stage, buf, &o); bool ok = e == 0 && hr == D3D_OK && nx_sm_bytes(buf) == n * 4; nacc += ok;
        if (o) o->Release(); o = nullptr;
        CHECK(ok); if (!ok) { errh[e]++; if (getenv("RES_DUMP")) fprintf(stderr, "REJ %d %s %s\n", stage, id, kErr[e]); if (nshown++ < 8) printf("  legal rejected: %d %s err %s hr %x\n", stage, id, kErr[e], (unsigned)hr); }
    }
    if (f) fclose(f); printf("legal cases %d accepted %d; rejections by error:", nlegal, nacc); for (int i = 1; i < 16; i++) if (errh[i]) printf(" %s=%d", kErr[i], errh[i]); printf("\n"); CHECK(nlegal >= 500);
    f = t_open("sm_illegal.txt"); int nill = 0, nrej = 0;
    while (f && fgets(line, sizeof line, f)) {
        char id[200], want[64]; int stage, off; if (sscanf(line, "%d %199s %63s %n", &stage, id, want, &off) < 3) continue; uint32_t n; parse(line + off, &n, buf); nill++;
        int e = nx_sm_check(buf, n * 4, stage, nullptr); HRESULT hr = mk(d, stage, buf, &o); nrej += hr == D3DERR_INVALIDCALL && o == nullptr;
        CHECK_HR(hr, D3DERR_INVALIDCALL); bool want_ok = false; for (char* w = strtok(want, ","); w; w = strtok(nullptr, ",")) want_ok |= errcode(w) == e;
        if (!CHECK(want_ok) && nshown++ < 20) printf("  illegal %s: got %s\n", id, kErr[e]);
        if (o) o->Release(); o = nullptr;
    }
    if (f) fclose(f); printf("illegal cases %d rejected %d\n", nill, nrej); CHECK(nill >= 100);
    // ---- directed: version / stage / walk
    uint32_t v11[] = {0xFFFE0101, 0x00000001 | 0, 0x800F0000, 0x90E40000, 0x0000FFFF};                                        // vs_1_1: mov r0, v0
    uint32_t com[] = {0xFFFE0101, 0x0003FFFE, 0x0000FFFF, 0x0000FFFF, 0x0000FFFF, 0x00000001, 0x800F0000, 0x90E40000, 0x0000FFFF};  // a comment holding END words must be skipped
    CHECK_EQ(nx_sm_bytes(v11), 20); CHECK_EQ(nx_sm_bytes(com), 36); CHECK_EQ(nx_sm_check(com, 36, 0, nullptr), 0); CHECK_EQ(nx_sm_check(v11, 20, 0, nullptr), 0);
    CHECK_EQ(nx_sm_check(v11, 20, 1, nullptr), NX_SM_E_STAGE); CHECK_HR(d->CreatePixelShader((const DWORD*)v11, (IDirect3DPixelShader9**)&o), D3DERR_INVALIDCALL);
    uint32_t bad[][5] = {{0xFFFD0101, 1, 0x800F0000, 0x90E40000, 0xFFFF}, {0x00000101, 1, 0x800F0000, 0x90E40000, 0xFFFF}, {0xFFFE0501, 0x02000001, 0x800F0000, 0x90E40000, 0xFFFF}, {0xFFFE0100 + 7, 1, 0x800F0000, 0x90E40000, 0xFFFF},
        {0xFFFE02FF, 0x02000001, 0x800F0000, 0x90E40000, 0xFFFF} /* vs_2_sw */};
    for (auto& b : bad) { CHECK(nx_sm_check(b, 20, 0, nullptr) != 0); CHECK_HR(d->CreateVertexShader((const DWORD*)b, (IDirect3DVertexShader9**)&o), D3DERR_INVALIDCALL); }
    CHECK_EQ(nx_sm_bytes(g_zero), 0); g_zero[0] = 0xFFFE0101; CHECK_EQ(nx_sm_bytes(g_zero), 0); CHECK_HR(d->CreateVertexShader((const DWORD*)g_zero, (IDirect3DVertexShader9**)&o), D3DERR_INVALIDCALL);   // no END in 1M tokens
    uint32_t v10[] = {0xFFFE0100, 0x00000001, 0x800F0000, 0x90E40000, 0x0000FFFF}; CHECK_EQ(nx_sm_check(v10, 20, 0, nullptr), 0);   // vs_1_0 = 1_1 grammar (table `undetermined`)
    uint32_t nov[] = {0xFFFE0101, 0x0000FFFF}; CHECK_EQ(nx_sm_check(nov, 8, 0, nullptr), 0); CHECK_EQ(nx_sm_check(nov, 4, 0, nullptr), NX_SM_E_UNTERMINATED); CHECK_EQ(nx_sm_check(nov, 7, 0, nullptr), NX_SM_E_UNTERMINATED);
    uint32_t trail[] = {0xFFFE0101, 0x0000FFFF, 0x0000FFFF}; CHECK(nx_sm_check(trail, 12, 0, nullptr) != 0);
    t_part('a');   // creation rules of shaders: null pointers, GetFunction semantics, hash
    mk(d, 0, v11, &o); NxVertexShader9* s = static_cast<NxVertexShader9*>(o); NxShaderCore* sc = s; UINT sz = 7; CHECK(s != nullptr); CHECK_HR(s->GetFunction(nullptr, &sz), D3D_OK); CHECK_EQ(sz, 20);
    uint32_t outb[8]; memset(outb, 0xCC, sizeof outb); sz = 19; CHECK_HR(s->GetFunction(outb, &sz), D3DERR_INVALIDCALL); CHECK_EQ(outb[0], 0xCCCCCCCC); CHECK_EQ(sz, 19);
    sz = 20; CHECK_HR(s->GetFunction(outb, &sz), D3D_OK); CHECK(!memcmp(outb, v11, 20) && outb[5] == 0xCCCCCCCC); sz = 32; CHECK_HR(s->GetFunction(outb, &sz), D3D_OK); CHECK_EQ(sz, 32);   // copy leaves *size (Wine)
    CHECK_HR(s->GetFunction(outb, nullptr), D3DERR_INVALIDCALL); CHECK_EQ(sc->nbytes, 20); CHECK(sc->code != v11 && !memcmp(sc->code, v11, 20));
    { uint8_t k[32]; fnvnx_sha256(v11, 20, k); CHECK(!memcmp(sc->key, k, 32)); }   // the content key is the SHA-256 of the bytecode (fnvnx_sha256 itself: lib/sha256_tests)
    CHECK_EQ(sc->iface.stage, 0); CHECK_EQ(sc->iface.major, 1); CHECK(sc->iface.in_mask == 1 && sc->iface.in_usage[0] == 0xFF && sc->iface.n_instr == 1);
    IDirect3DDevice9* gd; CHECK_HR(s->GetDevice(&gd), D3D_OK); gd->Release(); CHECK_EQ(s->Release(), 0);
    CHECK_HR(d->CreateVertexShader(nullptr, (IDirect3DVertexShader9**)&o), D3DERR_INVALIDCALL); CHECK_HR(d->CreateVertexShader((const DWORD*)v11, nullptr), D3DERR_INVALIDCALL);
    CHECK_HR(d->CreatePixelShader(nullptr, (IDirect3DPixelShader9**)&o), D3DERR_INVALIDCALL); CHECK(o == nullptr);
    TEnv ex = t_env(true); CHECK_HR(ex.devex->CreateVertexShader((const DWORD*)v11, (IDirect3DVertexShader9**)&o), D3D_OK); o->Release();
    t_part('d'); FILE* fi = t_open("iface.txt"); int ni = 0;   // python-encoded shaders with the expected interface (test_res.py)
    while (fi && fgets(line, sizeof line, fi)) {   // stage id in_mask cf_lo cf_hi sampler_mask ci_mask cb_mask def_f def_i def_b n_instr U r:usage:index.. S r=type.. H words..
        char id[100]; int stage, off = 0; unsigned inm, lo, hi, sm, cim, cbm, df, di, db, nin; if (sscanf(line, "%d %99s %x %u %u %x %x %x %u %u %u %u %n", &stage, id, &inm, &lo, &hi, &sm, &cim, &cbm, &df, &di, &db, &nin, &off) < 12 || !off) continue;
        uint8_t uu[16], ux[16], st[16]; bool hasu[16] = {}; memset(st, 0, 16); uint32_t n = 0; char mode = 0;
        for (char* w = strtok(line + off, " \n"); w; w = strtok(nullptr, " \n")) {
            unsigned r, u, x, t; if (!strcmp(w, "U") || !strcmp(w, "S") || !strcmp(w, "H")) { mode = w[0]; continue; }
            if (mode == 'U' && sscanf(w, "%u:%u:%u", &r, &u, &x) == 3) { uu[r] = u; ux[r] = x; hasu[r] = true; } else if (mode == 'S' && sscanf(w, "%u=%u", &r, &t) == 2) st[r] = t; else if (mode == 'H') buf[n++] = (uint32_t)strtoul(w, nullptr, 16);
        }
        NxSmIface I; CHECK_EQ(nx_sm_check(buf, n * 4, stage, &I), 0); ni++;
        bool ok = I.in_mask == inm && I.sampler_mask == sm && I.ci_mask == cim && I.cb_mask == cbm && I.def_f == df && I.def_i == di && I.def_b == db && I.n_instr == nin && (hi <= lo ? I.cf_lo >= I.cf_hi : I.cf_lo == lo && I.cf_hi == hi);
        for (int r = 0; r < 16; r++) { ok &= hasu[r] ? I.in_usage[r] == uu[r] && I.in_index[r] == ux[r] : I.in_usage[r] == 0xFF; ok &= I.sampler_type[r] == st[r]; }
        if (!CHECK(ok)) printf("  iface %s: in %x/%x s %x/%x c %u..%u/%u..%u i %x/%x b %x/%x n %u/%u defs %u %u %u\n", id, I.in_mask, inm, I.sampler_mask, sm, I.cf_lo, I.cf_hi, lo, hi, I.ci_mask, cim, I.cb_mask, cbm, I.n_instr, nin, I.def_f, I.def_i, I.def_b);
    }
    if (fi) fclose(fi); CHECK(ni >= 8);
}
