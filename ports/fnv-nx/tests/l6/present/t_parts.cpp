// tests/l6/present: the presentation hooks end to end. a Present rects / interval / gamma / front buffer / frame files against a CPU model   b software cursor   c back buffer rotation   d vblank, wait_idle, small swapchain.
// The model below is independent CPU code (point rule floor((2 i + 1) sw / (2 dw)), ramp >> 8, the cursor blend round(t / 255) written as (2 t + 255) / 510); the oracle of the backend is never used.
#include "t_pres.h"
#include <time.h>
#include <unistd.h>
const char* g_frames_dir = "";
static uint64_t hash(uint64_t a) { a ^= a >> 33; a *= 0xff51afd7ed558ccdull; a ^= a >> 33; a *= 0xc4ceb9fe1a85ec53ull; a ^= a >> 33; return a; }
uint32_t pix(uint32_t seed, uint32_t x, uint32_t y) { return (uint32_t)hash((uint64_t)seed << 40 ^ (uint64_t)y << 20 ^ x); }
IDirect3DDevice9* mk_dev(bool ex, void* win, uint32_t w, uint32_t h, uint32_t fmt, uint32_t count, uint32_t swap, uint32_t interval, D3DPRESENT_PARAMETERS* pp_out, HRESULT* hr_out) {
    D3DPRESENT_PARAMETERS pp; memset(&pp, 0, sizeof pp); pp.Windowed = TRUE; pp.SwapEffect = (D3DSWAPEFFECT)swap; pp.BackBufferCount = count; pp.hDeviceWindow = (HWND)win; pp.BackBufferWidth = w; pp.BackBufferHeight = h;
    pp.BackBufferFormat = (D3DFORMAT)fmt; pp.PresentationInterval = interval; IDirect3DDevice9* d = nullptr; HRESULT hr;
    if (ex) { IDirect3D9Ex* e = nullptr; nx_Direct3DCreate9Ex(0, &e); IDirect3DDevice9Ex* dx = nullptr; hr = e->CreateDeviceEx(0, D3DDEVTYPE_HAL, (HWND)win, D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, nullptr, &dx); d = dx; e->Release(); }
    else { IDirect3D9* e = nx_Direct3DCreate9(0); hr = e->CreateDevice(0, D3DDEVTYPE_HAL, (HWND)win, D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &d); e->Release(); }
    if (pp_out) *pp_out = pp; if (hr_out) *hr_out = hr; return hr == D3D_OK ? d : nullptr;
}
bool fill_bb(IDirect3DDevice9* d, IDirect3DSurface9* bb, uint32_t w, uint32_t h, uint32_t fmt, uint32_t seed) {
    IDirect3DSurface9* s = nullptr; if (d->CreateOffscreenPlainSurface(w, h, (D3DFORMAT)fmt, D3DPOOL_SYSTEMMEM, &s, nullptr) != D3D_OK) return false;
    D3DLOCKED_RECT lr; if (s->LockRect(&lr, nullptr, 0) != D3D_OK) { s->Release(); return false; }
    for (uint32_t y = 0; y < h; y++) for (uint32_t x = 0; x < w; x++) ((uint32_t*)((uint8_t*)lr.pBits + (size_t)y * lr.Pitch))[x] = pix(seed, x, y);
    s->UnlockRect(); HRESULT hr = d->UpdateSurface(s, nullptr, bb, nullptr); s->Release(); return hr == D3D_OK;
}
bool read_bb(IDirect3DDevice9* d, IDirect3DSurface9* bb, uint32_t w, uint32_t h, uint32_t fmt, uint32_t seed) {
    IDirect3DSurface9* s = nullptr; if (d->CreateOffscreenPlainSurface(w, h, (D3DFORMAT)fmt, D3DPOOL_SYSTEMMEM, &s, nullptr) != D3D_OK) return false;
    bool ok = d->GetRenderTargetData(bb, s) == D3D_OK; D3DLOCKED_RECT lr; ok = ok && s->LockRect(&lr, nullptr, D3DLOCK_READONLY) == D3D_OK; uint32_t mask = fmt == D3DFMT_X8R8G8B8 ? 0x00FFFFFFu : 0xFFFFFFFFu;
    for (uint32_t y = 0; ok && y < h; y++) for (uint32_t x = 0; x < w; x++) ok &= (((uint32_t*)((uint8_t*)lr.pBits + (size_t)y * lr.Pitch))[x] & mask) == (pix(seed, x, y) & mask);
    if (s) { s->UnlockRect(); s->Release(); } return ok;
}
static void rgba_of(uint32_t v, bool alpha, uint8_t* o) { o[0] = (uint8_t)(v >> 16); o[1] = (uint8_t)(v >> 8); o[2] = (uint8_t)v; o[3] = alpha ? (uint8_t)(v >> 24) : 255; }
void model_present(Model& m, uint32_t seed, bool alpha, uint32_t bw, uint32_t bh, const RECT& s, const RECT& d, const D3DGAMMARAMP* ramp) {
    (void)bw; (void)bh; int sw = s.right - s.left, sh = s.bottom - s.top, dw = d.right - d.left, dh = d.bottom - d.top;
    for (int j = 0; j < dh; j++) for (int i = 0; i < dw; i++) {
        int x = s.left + (2 * i + 1) * sw / (2 * dw), y = s.top + (2 * j + 1) * sh / (2 * dh); uint8_t* o = m.img + ((d.top + j) * SWW + d.left + i) * 4; rgba_of(pix(seed, (uint32_t)x, (uint32_t)y), alpha, o);
        if (ramp) { o[0] = (uint8_t)(ramp->red[o[0]] >> 8); o[1] = (uint8_t)(ramp->green[o[1]] >> 8); o[2] = (uint8_t)(ramp->blue[o[2]] >> 8); }
    }
}
void model_cursor(Model& m, const uint32_t* cur, int cw, int ch, int cx, int cy, const D3DGAMMARAMP* ramp) {
    for (int j = 0; j < ch; j++) for (int i = 0; i < cw; i++) {
        int x = cx + i, y = cy + j; if (x < 0 || y < 0 || x >= SWW || y >= SWH) continue;
        uint32_t v = cur[j * cw + i]; int a = (int)(v >> 24); int c3[3] = {(int)(v >> 16 & 255), (int)(v >> 8 & 255), (int)(v & 255)}; uint8_t* o = m.img + (y * SWW + x) * 4;
        for (int c = 0; c < 3; c++) { int s = c3[c]; if (ramp) s = (c == 0 ? ramp->red : c == 1 ? ramp->green : ramp->blue)[s] >> 8; o[c] = (uint8_t)((2 * (s * a + o[c] * (255 - a)) + 255) / 510); }
    }
}
static bool front_ok(IDirect3DDevice9* d, const Model& m, uint32_t ox, uint32_t oy, uint32_t w, uint32_t h) {
    IDirect3DSurface9* s = nullptr; if (d->CreateOffscreenPlainSurface(ox + w, oy + h, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &s, nullptr) != D3D_OK) return false;
    bool ok = d->GetFrontBufferData(0, s) == D3D_OK; D3DLOCKED_RECT lr; ok = ok && s->LockRect(&lr, nullptr, D3DLOCK_READONLY) == D3D_OK;
    for (uint32_t y = 0; ok && y < h; y++) for (uint32_t x = 0; x < w; x++) {
        const uint8_t* p = (const uint8_t*)lr.pBits + (size_t)(oy + y) * lr.Pitch + (size_t)(ox + x) * 4; uint8_t want[4] = {0, 0, 0, 0}; if (x < SWW && y < SWH) memcpy(want, m.img + (y * SWW + x) * 4, 4);
        ok &= p[2] == want[0] && p[1] == want[1] && p[0] == want[2] && p[3] == want[3];   // BGRA bytes of A8R8G8B8 against the RGBA model; outside the swapchain image: zero
    }
    if (s) { s->UnlockRect(); s->Release(); } return ok;
}
bool front_equals(IDirect3DDevice9* d, const Model& m) { return front_ok(d, m, 0, 0, SWW, SWH); }
bool frame_file_equals(uint32_t frame, const Model& m) {
    char p[1024]; snprintf(p, sizeof p, "%s/frame_%06u.rgba", g_frames_dir, frame); FILE* f = fopen(p, "rb"); if (!f) return false;
    uint8_t buf[SWW * SWH * 4 + 1]; size_t n = fread(buf, 1, sizeof buf, f); fclose(f); return n == SWW * SWH * 4 && !memcmp(buf, m.img, n);
}
static void rand_ramp(D3DGAMMARAMP* r, uint32_t seed) { for (int i = 0; i < 256; i++) { r->red[i] = (WORD)hash(seed * 3 + (uint64_t)i); r->green[i] = (WORD)hash(seed * 3 + 1000 + (uint64_t)i); r->blue[i] = (WORD)hash(seed * 3 + 2000 + (uint64_t)i); } }
static uint64_t now_ns() { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec; }
#define X8 D3DFMT_X8R8G8B8
#define A8 D3DFMT_A8R8G8B8
static bool spy_rects(const NxPresent& p, const RECT& s, const RECT& d) { return p.sx == s.left && p.sy == s.top && p.sw == s.right - s.left && p.sh == s.bottom - s.top && p.dx == d.left && p.dy == d.top && p.dw == d.right - d.left && p.dh == d.bottom - d.top; }

void t_a() {
    t_part('a'); g_frames_dir = getenv("NXGL4_FRAMES") ? getenv("NXGL4_FRAMES") : "";
    // P3 / P4: the very first read of the swapchain image (nothing was ever presented) is all zero
    Model M; memset(&M, 0, sizeof M); int base = g_nps;
    IDirect3DDevice9* d = mk_dev(false, (void*)WIN0, 32, 24, X8, 1, D3DSWAPEFFECT_DISCARD, 0); if (!CHECK(d != nullptr)) return;
    CHECK(front_ok(d, M, 0, 0, 32, 24)); CHECK_EQ(g_nps, base);
    IDirect3DSurface9* bb = nullptr; d->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb); IDirect3DSwapChain9* sc = nullptr; d->GetSwapChain(0, &sc);
    D3DGAMMARAMP ramp; bool has_ramp = false; RECT full_s = {0, 0, 32, 24}, full_d = {0, 0, SWW, SWH};
    struct C { bool s, d, gamma, force; RECT sr, dr; } cs[] = {
        {0, 0, 0, 0, {}, {}}, {1, 0, 0, 0, {4, 2, 20, 18}, {}}, {0, 1, 0, 0, {}, {6, 5, 30, 29}}, {1, 1, 0, 0, {0, 0, 32, 24}, {0, 0, 32, 24}}, {1, 1, 0, 0, {8, 8, 16, 12}, {40, 30, 48, 40}},
        {0, 0, 1, 0, {}, {}}, {1, 1, 1, 0, {3, 3, 29, 21}, {2, 2, 47, 39}}, {1, 1, 0, 0, {5, 5, 6, 6}, {10, 10, 13, 13}}, {0, 0, 1, 1, {}, {}},
    };
    uint32_t seed = 1;
    for (C& c : cs) {
        seed++; if (c.gamma) { rand_ramp(&ramp, seed); d->SetGammaRamp(0, 0, &ramp); has_ramp = true; }
        CHECK(fill_bb(d, bb, 32, 24, X8, seed)); RECT s = c.s ? c.sr : full_s, dr = c.d ? c.dr : full_d; int n0 = g_nps;
        HRESULT hr = c.force ? sc->Present(c.s ? &c.sr : nullptr, c.d ? &c.dr : nullptr, nullptr, nullptr, D3DPRESENT_FORCEIMMEDIATE) : d->Present(c.s ? &c.sr : nullptr, c.d ? &c.dr : nullptr, nullptr, nullptr);
        CHECK_HR(hr, D3D_OK); CHECK_EQ(g_nps, n0 + 1); if (g_nps != n0 + 1) continue; const PSpy& sp = g_ps[g_nps - 1];
        model_present(M, seed, false, 32, 24, s, dr, has_ramp ? &ramp : nullptr);
        CHECK(spy_rects(sp.p, s, dr)); CHECK_EQ(sp.p.interval, c.force ? 0u : 1u); CHECK_EQ(sp.p.cursor, 0u); CHECK_EQ(sp.prev_fn, NXGPU_FN_SUBMIT);   // pending ops are submitted before the present (R2d, P1)
        CHECK_EQ(sp.has_ramp, has_ramp); if (has_ramp) CHECK(!memcmp(sp.ramp, &ramp, sizeof ramp));
        CHECK_EQ(sp.p.frame, (uint32_t)(g_nps - base)); CHECK(front_ok(d, M, 0, 0, 32, 24)); CHECK(frame_file_equals(sp.p.frame, M));
    }
    CHECK(read_bb(d, bb, 32, 24, X8, seed)); CHECK_EQ(g_calls[NXGPU_FN_PRESENT], g_nps);   // the back buffer is as the application left it; one backend present per Present
    bb->Release(); sc->Release(); d->Release();
    // intervals: the d3d9 value -> NxPresent.interval, per device (windowed: DEFAULT / ONE / IMMEDIATE), FORCEIMMEDIATE above
    static const uint32_t IV[3][2] = {{0, 1}, {D3DPRESENT_INTERVAL_ONE, 1}, {D3DPRESENT_INTERVAL_IMMEDIATE, 0}};
    for (auto& iv : IV) {
        IDirect3DDevice9* e = mk_dev(false, (void*)WIN0, 32, 24, X8, 1, D3DSWAPEFFECT_DISCARD, iv[0]); if (!CHECK(e != nullptr)) continue; int n0 = g_nps;
        CHECK_HR(e->Present(nullptr, nullptr, nullptr, nullptr), D3D_OK); CHECK_EQ(g_nps, n0 + 1); if (g_nps == n0 + 1) { CHECK_EQ(g_ps[n0].p.interval, iv[1]); CHECK_EQ(g_ps[n0].p.frame, 1u); } e->Release();
    }
    // A8R8G8B8 back buffer: the alpha of the pixel reaches the swapchain
    IDirect3DDevice9* e = mk_dev(false, (void*)WIN0, 32, 24, A8, 1, D3DSWAPEFFECT_DISCARD, 0);
    if (CHECK(e != nullptr)) { IDirect3DSurface9* b = nullptr; e->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &b); Model N = M; CHECK(fill_bb(e, b, 32, 24, A8, 77)); CHECK_HR(e->Present(nullptr, nullptr, nullptr, nullptr), D3D_OK);
        model_present(N, 77, true, 32, 24, full_s, full_d, nullptr); CHECK(front_ok(e, N, 0, 0, 32, 24)); CHECK(frame_file_equals(g_ps[g_nps - 1].p.frame, N)); b->Release(); e->Release(); }
}

void t_b() {
    t_part('b'); Model M; memset(&M, 0, sizeof M); RECT full = {0, 0, SWW, SWH};
    IDirect3DDevice9* d = mk_dev(false, (void*)WIN1, SWW, SWH, X8, 1, D3DSWAPEFFECT_DISCARD, 0); if (!CHECK(d != nullptr)) return;   // window at (10, 20) on the screen: cursor positions are screen coordinates
    IDirect3DSurface9* bb = nullptr; d->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb); uint32_t cur[64]; IDirect3DSurface9* cs = nullptr;
    CHECK_HR(d->CreateOffscreenPlainSurface(8, 8, A8, D3DPOOL_SYSTEMMEM, &cs, nullptr), D3D_OK); D3DLOCKED_RECT lr; CHECK_HR(cs->LockRect(&lr, nullptr, 0), D3D_OK);
    for (int i = 0; i < 64; i++) { uint32_t v = pix(500, (uint32_t)i, 3), al = i % 5 == 0 ? 0 : i % 7 == 0 ? 255 : (v >> 24); cur[i] = (v & 0xFFFFFF) | al << 24; ((uint32_t*)((uint8_t*)lr.pBits + (i / 8) * lr.Pitch))[i % 8] = cur[i]; }
    cs->UnlockRect(); const int hx = 2, hy = 3; CHECK_HR(d->SetCursorProperties(hx, hy, cs), D3D_OK); cs->Release();
    struct P { int cx, cy; bool gamma; } ps[] = {{5, 5, 0}, {0, 0, 0}, {-5, -6, 0}, {44, 36, 0}, {47, 39, 0}, {-7, -7, 0}, {48, 10, 0}, {10, 40, 0}, {100000, -100000, 0}, {20, 12, 1}, {-3, 30, 1}, {41, -2, 1}};
    D3DGAMMARAMP ramp; bool has_ramp = false; uint32_t seed = 40; g_ospos[0] = 11; g_ospos[1] = 22; CHECK_EQ(d->ShowCursor(TRUE), FALSE);
    for (P& p : ps) {
        seed++; if (p.gamma && !has_ramp) { rand_ramp(&ramp, 9); d->SetGammaRamp(0, 0, &ramp); has_ramp = true; }
        d->SetCursorPosition(p.cx + hx + 10, p.cy + hy + 20, 0); CHECK(fill_bb(d, bb, SWW, SWH, X8, seed)); int n0 = g_nps; CHECK_HR(d->Present(nullptr, nullptr, nullptr, nullptr), D3D_OK); CHECK_EQ(g_nps, n0 + 1); if (g_nps != n0 + 1) continue;
        const PSpy& sp = g_ps[g_nps - 1]; model_present(M, seed, false, SWW, SWH, full, full, has_ramp ? &ramp : nullptr); model_cursor(M, cur, 8, 8, p.cx, p.cy, has_ramp ? &ramp : nullptr);
        CHECK(sp.p.cursor != 0 && sp.p.cx == p.cx && sp.p.cy == p.cy && sp.p.cw == 8 && sp.p.ch == 8); CHECK(frame_file_equals(sp.p.frame, M)); CHECK(front_ok(d, M, 10, 20, SWW, SWH));
        CHECK(read_bb(d, bb, SWW, SWH, X8, seed));   // the cursor never touches the back buffer
    }
    // hidden: no cursor in the presented image
    CHECK_EQ(d->ShowCursor(FALSE), TRUE); CHECK(fill_bb(d, bb, SWW, SWH, X8, 99)); int n0 = g_nps; CHECK_HR(d->Present(nullptr, nullptr, nullptr, nullptr), D3D_OK); model_present(M, 99, false, SWW, SWH, full, full, has_ramp ? &ramp : nullptr);
    CHECK_EQ(g_nps, n0 + 1); CHECK_EQ(g_ps[g_nps - 1].p.cursor, 0u); CHECK(frame_file_equals(g_ps[g_nps - 1].p.frame, M));
    // exhaustion: the cursor block cannot be allocated -> D3DERR_OUTOFVIDEOMEMORY, nothing presented, the image and the back buffer are as before; the next present works
    d->ShowCursor(TRUE); d->SetCursorPosition(20 + hx + 10, 12 + hy + 20, 0); CHECK(fill_bb(d, bb, SWW, SWH, X8, 100)); n0 = g_nps; g_fail_alloc = true; CHECK_HR(d->Present(nullptr, nullptr, nullptr, nullptr), D3DERR_OUTOFVIDEOMEMORY); g_fail_alloc = false;
    CHECK_EQ(g_nps, n0); CHECK(front_ok(d, M, 10, 20, SWW, SWH)); CHECK(read_bb(d, bb, SWW, SWH, X8, 100)); CHECK_HR(d->Present(nullptr, nullptr, nullptr, nullptr), D3D_OK); CHECK_EQ(g_nps, n0 + 1);
    model_present(M, 100, false, SWW, SWH, full, full, has_ramp ? &ramp : nullptr); model_cursor(M, cur, 8, 8, 20, 12, has_ramp ? &ramp : nullptr); CHECK(frame_file_equals(g_ps[g_nps - 1].p.frame, M));
    bb->Release(); d->Release();
}

void t_c() {
    t_part('c'); RECT full = {0, 0, SWW, SWH};
    static const uint32_t SE[2] = {D3DSWAPEFFECT_DISCARD, D3DSWAPEFFECT_FLIP};
    for (uint32_t cnt = 1; cnt <= 3; cnt++) for (uint32_t se : SE) {
        if (cnt == 1 && se == D3DSWAPEFFECT_FLIP) continue;
        IDirect3DDevice9* d = mk_dev(false, (void*)WIN0, SWW, SWH, X8, cnt, se, 0); if (!CHECK(d != nullptr)) continue; IDirect3DSurface9* b[3]; uint32_t cur[3]; Model M;
        for (uint32_t i = 0; i < cnt; i++) { d->GetBackBuffer(0, i, D3DBACKBUFFER_TYPE_MONO, &b[i]); cur[i] = 200 + cnt * 10 + i; CHECK(fill_bb(d, b[i], SWW, SWH, X8, cur[i])); }
        for (uint32_t k = 0; k < cnt + 2; k++) {   // presents show buffer 0; afterwards buffer i holds the former contents of buffer i + 1 (mod count) when count >= 2, else nothing moves
            int n0 = g_nps; memset(&M, 0, sizeof M); CHECK_HR(d->Present(nullptr, nullptr, nullptr, nullptr), D3D_OK); CHECK_EQ(g_nps, n0 + 1); if (g_nps != n0 + 1) break;
            model_present(M, cur[0], false, SWW, SWH, full, full, nullptr); CHECK(frame_file_equals(g_ps[g_nps - 1].p.frame, M)); CHECK(front_equals(d, M)); CHECK_EQ(g_ps[g_nps - 1].p.frame, k + 1);
            if (cnt >= 2) { uint32_t f = cur[0]; for (uint32_t i = 0; i + 1 < cnt; i++) cur[i] = cur[i + 1]; cur[cnt - 1] = f; }
            for (uint32_t i = 0; i < cnt; i++) CHECK(read_bb(d, b[i], SWW, SWH, X8, cur[i]));
        }
        for (uint32_t i = 0; i < cnt; i++) b[i]->Release(); d->Release();
    }
}

void t_d() {
    t_part('d'); RECT full = {0, 0, SWW, SWH};
    // WaitForVBlank (Ex): the backend call, on the modelled 60 Hz grid, strictly after the call
    IDirect3DDevice9* dx = mk_dev(true, (void*)WIN0, 32, 24, X8, 1, D3DSWAPEFFECT_DISCARD, 0);
    if (CHECK(dx != nullptr)) {
        IDirect3DDevice9Ex* e = static_cast<IDirect3DDevice9Ex*>(dx); int v0 = g_nvbl; uint64_t prev = 0;
        for (int k = 0; k < 3; k++) { uint64_t t0 = now_ns(); CHECK_HR(e->WaitForVBlank(0), D3D_OK); uint64_t t1 = now_ns(), at = (t1 * 60 / 1000000000ull) * 1000000000ull / 60; CHECK(t1 > t0 && t1 - t0 < 40000000ull && t1 - at < 12000000ull); if (prev) CHECK(t1 - prev >= 10000000ull); prev = t1; }
        CHECK_EQ(g_nvbl, v0 + 3); CHECK_HR(e->WaitForVBlank(1), D3DERR_INVALIDCALL); CHECK_EQ(g_nvbl, v0 + 3); CHECK_EQ(g_calls[NXGPU_FN_WAIT_VBLANK], g_nvbl); dx->Release();
    }
    // wait_idle: Reset waits for every submitted frame
    D3DPRESENT_PARAMETERS pp; IDirect3DDevice9* d = mk_dev(false, (void*)WIN0, 32, 24, X8, 1, D3DSWAPEFFECT_DISCARD, 0, &pp);
    if (CHECK(d != nullptr)) {
        IDirect3DSurface9* b = nullptr; d->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &b); CHECK(fill_bb(d, b, 32, 24, X8, 5)); b->Release(); CHECK_HR(d->Present(nullptr, nullptr, nullptr, nullptr), D3D_OK);
        CHECK(nxs_in_flight() > 0); CHECK_HR(d->Reset(&pp), D3D_OK); CHECK_EQ(nxs_in_flight(), 0u); d->Release();
    }
    // a chain larger than the window's client area: the front buffer part inside the swapchain image is the image, the rest reads zero
    d = mk_dev(false, (void*)WIN0, 64, 56, X8, 1, D3DSWAPEFFECT_DISCARD, 0);
    if (CHECK(d != nullptr)) {
        Model M; memset(&M, 0, sizeof M); IDirect3DSurface9* b = nullptr; d->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &b); CHECK(fill_bb(d, b, 64, 56, X8, 6)); RECT s = {0, 0, 64, 56}; CHECK_HR(d->Present(nullptr, nullptr, nullptr, nullptr), D3D_OK);
        model_present(M, 6, false, 64, 56, s, full, nullptr); CHECK(front_ok(d, M, 0, 0, 64, 56)); CHECK(frame_file_equals(g_ps[g_nps - 1].p.frame, M)); b->Release(); d->Release();
    }
    CHECK_EQ(nxs_errors(), 0u);
}
