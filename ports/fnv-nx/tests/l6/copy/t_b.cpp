// part b: pixel results on llvmpipe (through the RPC replay) against CPU references. Uses only the device methods under test + Lock / GetRenderTargetData for read-back.
#include "t_common.h"
#include "t_ref.h"
#include <initializer_list>
static uint32_t g_seed = 12345;
static IDirect3DSurface9* rd_surf(TDev& d, IDirect3DSurface9* s, uint32_t fmt, uint32_t w, uint32_t h) {   // read back a DEFAULT surface into a new SYSTEMMEM one
    IDirect3DSurface9* sys = t_surf(d, 2, w, h, fmt, D3DPOOL_SYSTEMMEM); if (!sys) return nullptr; if (d.i9->GetRenderTargetData(s, sys) != D3D_OK) { sys->Release(); return nullptr; } return sys;
}
static bool close4(uint32_t f, const double* a, const double* b, double scale = 1.0) { for (int c = 0; c < 4; c++) if (fabs(a[c] - b[c]) > tol_of(f, c) * scale) return false; return true; }
static void fill_random(const RF* f, uint8_t* px, uint32_t n) { for (uint32_t i = 0; i < n; i++) { double c[4]; for (int k = 0; k < 4; k++) c[k] = (double)(t_lcg(&g_seed) & 0xFFFF) / 65535.0 * 2.4 - 1.2; encode(f->fmt, c, px + i * f->bytes); } }
static void b_formats(TDev& d) {   // every render-target format: ColorFill, Clear, UpdateSurface -> StretchRect -> GetRenderTargetData
    FILE* fp = t_open("rtfmts.txt"); unsigned fm;
    while (fp && fscanf(fp, "%u", &fm) == 1) {
        const RF* f = rf_of(fm); if (!f) continue; const uint32_t W = 8;
        IDirect3DSurface9* rt = t_surf(d, 0, W, W, fm, D3DPOOL_DEFAULT, 0, false); if (!CHECK(rt != nullptr)) continue;
        double col[4] = {0x40 / 255.0, 0x80 / 255.0, 0xC0 / 255.0, 0x20 / 255.0};   // 0x2040 80C0 as ARGB: A 0x20, R 0x40, G 0x80, B 0xC0
        uint8_t want[16], got[16 * 64], px[16 * 64]; encode(fm, col, want); double wd[4]; decode(fm, want, wd);
        CHECK_HR(d.i9->ColorFill(rt, nullptr, 0x204080C0), D3D_OK);
        IDirect3DSurface9* sys = rd_surf(d, rt, fm, W, W); CHECK(sys != nullptr); if (!sys) continue; CHECK(t_get(sys, got, f->bytes * W * W));
        bool ok = true; for (uint32_t i = 0; i < W * W; i++) { double g[4]; decode(fm, got + i * f->bytes, g); ok = ok && close4(fm, g, wd); } CHECK(ok); if (!ok) printf("  ColorFill %s\n", f->name);
        // Clear with the target bound, then a partial ColorFill: inside the rect the new colour, outside the clear colour
        CHECK_HR(d.i9->SetRenderTarget(0, rt), D3D_OK); D3DVIEWPORT9 vp = {0, 0, W, W, 0, 1}; d.i9->SetViewport(&vp); d.i9->SetRenderState(D3DRS_SCISSORTESTENABLE, 0);
        CHECK_HR(d.i9->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF102030, 0, 0), D3D_OK); RECT r = {2, 2, 6, 5}; CHECK_HR(d.i9->ColorFill(rt, &r, 0x80FFFFFF), D3D_OK);
        double c1[4] = {0x10 / 255.0, 0x20 / 255.0, 0x30 / 255.0, 1.0}, c2[4] = {1, 1, 1, 0x80 / 255.0}; uint8_t e1[16], e2[16]; double d1[4], d2[4]; encode(fm, c1, e1); encode(fm, c2, e2); decode(fm, e1, d1); decode(fm, e2, d2);
        sys->Release(); sys = rd_surf(d, rt, fm, W, W); CHECK(sys && t_get(sys, got, f->bytes * W * W)); ok = true;
        for (uint32_t y = 0; y < W && sys; y++) for (uint32_t x = 0; x < W; x++) { double g[4]; decode(fm, got + (y * W + x) * f->bytes, g); bool in = x >= 2 && x < 6 && y >= 2 && y < 5; ok = ok && close4(fm, g, in ? d2 : d1); }
        CHECK(ok); if (!ok) printf("  Clear+ColorFill rect %s\n", f->name); if (sys) sys->Release();
        // same-format round trip: random texels -> UpdateSurface -> StretchRect (NONE) -> GetRenderTargetData is bit-exact under the data mask
        IDirect3DSurface9* src = t_surf(d, 2, W, W, fm, D3DPOOL_SYSTEMMEM); IDirect3DSurface9* a = t_surf(d, 0, W, W, fm, D3DPOOL_DEFAULT, 0, false); IDirect3DSurface9* b = t_surf(d, 0, W, W, fm, D3DPOOL_DEFAULT, 0, false);
        fill_random(f, px, W * W); t_put(src, px, f->bytes * W * W);
        CHECK_HR(d.i9->UpdateSurface(src, nullptr, a, nullptr), D3D_OK); CHECK_HR(d.i9->StretchRect(a, nullptr, b, nullptr, D3DTEXF_NONE), D3D_OK);
        sys = rd_surf(d, b, fm, W, W); ok = sys && t_get(sys, got, f->bytes * W * W);
        for (uint32_t i = 0; ok && i < W * W; i++) for (uint32_t k = 0; k < f->bytes && ok; k++) { uint32_t m = f->mask; uint8_t mb = k < 4 ? (uint8_t)(m >> (8 * k)) : 0xFF; ok = ((got[i * f->bytes + k] ^ px[i * f->bytes + k]) & mb) == 0; }
        CHECK(ok); if (!ok) printf("  roundtrip %s\n", f->name); if (sys) sys->Release(); for (auto* s : {src, a, b, rt}) if (s) s->Release();
    }
    if (fp) fclose(fp);
}
static void b_pairs(TDev& d) {   // StretchRect between every legal pair of render-target formats (env conversion table), unscaled and 2:1, POINT
    FILE* fp = t_open("pairs.txt"); unsigned fa, fb;
    while (fp && fscanf(fp, "%u %u", &fa, &fb) == 2) {
        const RF* A = rf_of(fa); const RF* B = rf_of(fb); if (!A || !B) continue; const uint32_t W = 8;
        IDirect3DSurface9* src = t_surf(d, 2, W, W, fa, D3DPOOL_SYSTEMMEM); IDirect3DSurface9* ra = t_surf(d, 0, W, W, fa, D3DPOOL_DEFAULT, 0, false); IDirect3DSurface9* rb = t_surf(d, 0, W, W, fb, D3DPOOL_DEFAULT, 0, false);
        if (!CHECK(src && ra && rb)) continue; uint8_t px[16 * 64], got[16 * 64];
        fill_random(A, px, W * W); t_put(src, px, A->bytes * W * W);
        CHECK_HR(d.i9->UpdateSurface(src, nullptr, ra, nullptr), D3D_OK); CHECK_HR(d.i9->StretchRect(ra, nullptr, rb, nullptr, D3DTEXF_POINT), D3D_OK);
        IDirect3DSurface9* sys = rd_surf(d, rb, fb, W, W); bool ok = sys && t_get(sys, got, B->bytes * W * W);
        for (uint32_t i = 0; ok && i < W * W; i++) {
            double s[4], w[4], g[4]; uint8_t e[16]; decode(fa, px + i * A->bytes, s); encode(fb, s, e); decode(fb, e, w); decode(fb, got + i * B->bytes, g);
            ok = close4(fb, g, w, 1.5); if (!ok) printf("  %s -> %s texel %u: got %g %g %g %g want %g %g %g %g\n", A->name, B->name, i, g[0], g[1], g[2], g[3], w[0], w[1], w[2], w[3]);
        }
        CHECK(ok); if (sys) sys->Release(); for (auto* s : {src, ra, rb}) s->Release();
    }
    if (fp) fclose(fp);
}
// Clear: viewport x scissor x rect list against the clipping rule (R-C3), 16x16 target pre-filled
static void b_clear(TDev& d) {
    const int W = 16; IDirect3DSurface9* rt = t_surf(d, 0, W, W, 21, D3DPOOL_DEFAULT, 0, false); d.i9->SetRenderTarget(0, rt); uint32_t seed = 77; int n = 0;
    for (int it = 0; it < 300; it++) {
        D3DVIEWPORT9 vp = {t_lcg(&seed) % 6, t_lcg(&seed) % 6, 1 + t_lcg(&seed) % 14, 1 + t_lcg(&seed) % 14, 0, 1}; RECT sc = {(LONG)(t_lcg(&seed) % 8), (LONG)(t_lcg(&seed) % 8), (LONG)(8 + t_lcg(&seed) % 8), (LONG)(8 + t_lcg(&seed) % 8)};
        int se = t_lcg(&seed) & 1, nr = t_lcg(&seed) % 3; D3DRECT rc[2]; for (int i = 0; i < nr; i++) rc[i] = {(LONG)(t_lcg(&seed) % 20) - 2, (LONG)(t_lcg(&seed) % 20) - 2, (LONG)(t_lcg(&seed) % 20) - 2, (LONG)(t_lcg(&seed) % 20) - 2};
        d.i9->SetViewport(&vp); d.i9->SetScissorRect(&sc); d.i9->SetRenderState(D3DRS_SCISSORTESTENABLE, se);
        CHECK_HR(d.i9->ColorFill(rt, nullptr, 0xFF000000), D3D_OK); CHECK_HR(d.i9->Clear(nr, nr ? rc : nullptr, D3DCLEAR_TARGET, 0xFFFFFFFF, 0, 0), D3D_OK);
        IDirect3DSurface9* sys = rd_surf(d, rt, 21, W, W); uint32_t px[W * W]; if (!CHECK(sys && t_get(sys, (uint8_t*)px, sizeof px))) continue; bool ok = true;
        LONG dl = vp.X, dt = vp.Y, dr = vp.X + vp.Width, db = vp.Y + vp.Height; if (se) { if (sc.left > dl) dl = sc.left; if (sc.top > dt) dt = sc.top; if (sc.right < dr) dr = sc.right; if (sc.bottom < db) db = sc.bottom; }
        for (int y = 0; y < W; y++) for (int x = 0; x < W; x++) {
            bool in = x >= dl && x < dr && y >= dt && y < db, cl = false;
            if (in) { if (!nr) cl = true; for (int i = 0; i < nr; i++) if (x >= rc[i].x1 && x < rc[i].x2 && y >= rc[i].y1 && y < rc[i].y2) cl = true; }
            ok = ok && px[y * W + x] == (cl ? 0xFFFFFFFFu : 0xFF000000u);
        }
        CHECK(ok); n += ok; sys->Release();
    }
    rt->Release();
}
// depth / stencil: INTZ (Z24S8, lockable): depth24 << 8 | stencil; D16_LOCKABLE; sizes smaller than the target; four simultaneous targets
static void b_depth(TDev& d) {
    const int W = 16; IDirect3DSurface9* ds = t_surf(d, 1, W, W, t_fcc('I', 'N', 'T', 'Z')); IDirect3DSurface9* rt = t_surf(d, 0, W, W, 21, D3DPOOL_DEFAULT, 0, false);
    if (!CHECK(ds && rt)) return; d.i9->SetRenderTarget(0, rt); d.i9->SetDepthStencilSurface(ds); D3DVIEWPORT9 vp = {0, 0, W, W, 0, 1}; d.i9->SetViewport(&vp); d.i9->SetRenderState(D3DRS_SCISSORTESTENABLE, 0);
    struct C { float z; uint32_t s, fl; D3DRECT r; bool hasr; } cs[] = {{0.5f, 0x55, D3DCLEAR_ZBUFFER | D3DCLEAR_STENCIL, {}, false}, {1.0f, 0xAA, D3DCLEAR_ZBUFFER, {2, 2, 8, 8}, true}, {0.25f, 0x77, D3DCLEAR_STENCIL, {4, 4, 12, 12}, true}, {7.0f, 0x1FF, D3DCLEAR_ZBUFFER | D3DCLEAR_STENCIL, {0, 0, 3, 3}, true}, {-1.0f, 0, D3DCLEAR_ZBUFFER, {13, 13, 16, 16}, true}};
    uint32_t model[W * W]; for (auto& m : model) m = 0;
    for (auto& c : cs) {
        CHECK_HR(d.i9->Clear(c.hasr ? 1 : 0, c.hasr ? &c.r : nullptr, c.fl, 0, c.z, c.s), D3D_OK);
        uint32_t zv = (uint32_t)nearbyintf((c.z < 0 ? 0 : c.z > 1 ? 1 : c.z) * 16777215.0f);
        for (int y = 0; y < W; y++) for (int x = 0; x < W; x++) if (!c.hasr || (x >= c.r.x1 && x < c.r.x2 && y >= c.r.y1 && y < c.r.y2)) { uint32_t& m = model[y * W + x]; if (c.fl & D3DCLEAR_ZBUFFER) m = (m & 0xFF) | zv << 8; if (c.fl & D3DCLEAR_STENCIL) m = (m & ~0xFFu) | (c.s & 255); }
    }
    uint32_t px[W * W]; bool ok = t_get(ds, (uint8_t*)px, sizeof px); for (int i = 0; ok && i < W * W; i++) ok = px[i] == model[i]; CHECK(ok); if (!ok) printf("  depth px0 %08x want %08x\n", px[0], model[0]);
    // TARGET with a depth surface smaller than render target 0: nothing is cleared (R-C2)
    IDirect3DSurface9* small = t_surf(d, 1, 8, 8, 75); d.i9->SetDepthStencilSurface(small); d.i9->ColorFill(rt, nullptr, 0xFF000000); CHECK_HR(d.i9->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER, 0xFFFFFFFF, 0, 0), D3D_OK);
    IDirect3DSurface9* sys = rd_surf(d, rt, 21, W, W); uint32_t cp[W * W]; CHECK(sys && t_get(sys, (uint8_t*)cp, sizeof cp)); CHECK_EQ(cp[0], 0xFF000000u); if (sys) sys->Release();
    d.i9->SetDepthStencilSurface(ds);
    // four simultaneous targets: one Clear writes all of them (D3D9: every bound target)
    IDirect3DSurface9* r4[4]; for (int i = 0; i < 4; i++) { r4[i] = t_surf(d, 0, W, W, i == 2 ? 23 : 21, D3DPOOL_DEFAULT, 0, false); d.i9->SetRenderTarget(i, r4[i]); }
    CHECK_HR(d.i9->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF336699, 0, 0), D3D_OK);
    for (int i = 0; i < 4; i++) { IDirect3DSurface9* sy = rd_surf(d, r4[i], i == 2 ? 23 : 21, W, W); uint8_t b[1024]; CHECK(sy && t_get(sy, b, W * W * (i == 2 ? 2 : 4))); if (sy) { if (i == 2) CHECK_EQ(*(uint16_t*)b, (0x33 >> 3) << 11 | (0x66 >> 2) << 5 | 0x99 >> 3); else CHECK_EQ(*(uint32_t*)b, 0xFF336699u); sy->Release(); } }
    for (int i = 1; i < 4; i++) d.i9->SetRenderTarget(i, nullptr);
}
// StretchRect rects: sub-rect copy, 2:1 and 1:2 point scaling by the backend's rule floor((d + 0.5) * sw / dw)
static void b_scale(TDev& d) {
    const int W = 16; IDirect3DSurface9* sys = t_surf(d, 2, W, W, 21, D3DPOOL_SYSTEMMEM); IDirect3DSurface9* a = t_surf(d, 0, W, W, 21, D3DPOOL_DEFAULT, 0, false); IDirect3DSurface9* b = t_surf(d, 0, W, W, 21, D3DPOOL_DEFAULT, 0, false);
    uint32_t px[W * W], got[W * W]; for (int i = 0; i < W * W; i++) px[i] = 0xFF000000u | (uint32_t)((i * 37) & 255) << 16 | (uint32_t)((i * 11) & 255) << 8 | (uint32_t)i; t_put(sys, (uint8_t*)px, sizeof px);
    d.i9->UpdateSurface(sys, nullptr, a, nullptr);
    struct S { RECT s, d; } tests[] = {{{2, 3, 10, 11}, {4, 5, 12, 13}}, {{0, 0, 16, 16}, {0, 0, 8, 8}}, {{4, 4, 8, 8}, {0, 0, 16, 16}}, {{1, 2, 14, 9}, {3, 3, 10, 15}}};
    for (auto& t : tests) {
        d.i9->ColorFill(b, nullptr, 0xFF000000); CHECK_HR(d.i9->StretchRect(a, &t.s, b, &t.d, D3DTEXF_POINT), D3D_OK);
        IDirect3DSurface9* rb = rd_surf(d, b, 21, W, W); CHECK(rb && t_get(rb, (uint8_t*)got, sizeof got)); bool ok = true;
        int sw = t.s.right - t.s.left, sh = t.s.bottom - t.s.top, dw = t.d.right - t.d.left, dh = t.d.bottom - t.d.top;
        for (int y = 0; y < W; y++) for (int x = 0; x < W; x++) {
            bool in = x >= t.d.left && x < t.d.right && y >= t.d.top && y < t.d.bottom; uint32_t want = 0xFF000000u;
            if (in) { int sx = t.s.left + ((2 * (x - t.d.left) + 1) * sw) / (2 * dw), sy = t.s.top + ((2 * (y - t.d.top) + 1) * sh) / (2 * dh); want = px[sy * W + sx]; }
            ok = ok && got[y * W + x] == want;
        }
        CHECK(ok); if (rb) rb->Release();
    }
    // UpdateSurface: source rect (3, 2)-(11, 9) of the system memory surface to the point (5, 6) of the DEFAULT surface
    d.i9->ColorFill(b, nullptr, 0xFF000000); RECT ur = {3, 2, 11, 9}; POINT up = {5, 6}; CHECK_HR(d.i9->UpdateSurface(sys, &ur, b, &up), D3D_OK);
    { IDirect3DSurface9* rb = rd_surf(d, b, 21, W, W); CHECK(rb && t_get(rb, (uint8_t*)got, sizeof got)); bool ok = true;
      for (int y = 0; y < W; y++) for (int x = 0; x < W; x++) { bool in = x >= 5 && x < 13 && y >= 6 && y < 13; uint32_t want = in ? px[(y - 6 + 2) * W + (x - 5 + 3)] : 0xFF000000u; ok = ok && got[y * W + x] == want; } CHECK(ok); if (rb) rb->Release(); }
    for (auto* s : {sys, a, b}) s->Release();
}
// UpdateTexture: dirty regions (level 0 box, shifted per level), clean source copies nothing, AddDirtyRect, NO_DIRTY_UPDATE, level skipping
static void b_tex(TDev& d) {
    IDirect3DTexture9* src = nullptr; IDirect3DTexture9* dst = nullptr; const int W = 16;
    CHECK_HR(d.i9->CreateTexture(W, W, 3, 0, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &src, nullptr), D3D_OK); CHECK_HR(d.i9->CreateTexture(W, W, 3, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &dst, nullptr), D3D_OK);
    if (!src || !dst) return; uint32_t model[3][W * W]; memset(model, 0, sizeof model);
    auto lockfill = [&](int lv, uint32_t v, DWORD fl) { D3DLOCKED_RECT lr; int w = W >> lv; src->LockRect(lv, &lr, nullptr, fl); for (int y = 0; y < w; y++) for (int x = 0; x < w; x++) ((uint32_t*)((uint8_t*)lr.pBits + y * lr.Pitch))[x] = v + lv * 0x10000 + y * 16 + x; src->UnlockRect(lv); };
    auto check = [&](const char* what) { bool ok = true; for (int lv = 0; lv < 3; lv++) { IDirect3DSurface9* s = nullptr; dst->GetSurfaceLevel(lv, &s); int w = W >> lv; IDirect3DSurface9* sys = t_surf(d, 2, w, w, 21, D3DPOOL_SYSTEMMEM);
            if (d.i9->GetRenderTargetData(s, sys) != D3D_OK) ok = false; else { uint32_t got[W * W]; t_get(sys, (uint8_t*)got, w * w * 4); for (int i = 0; i < w * w; i++) ok = ok && got[i] == model[lv][i]; } s->Release(); sys->Release(); }
        CHECK(ok); if (!ok) printf("  UpdateTexture: %s\n", what); };
    auto copybox = [&](int l0, int t0, int r0, int b0) { for (int lv = 0; lv < 3; lv++) { int w = W >> lv, l = l0 >> lv, t = t0 >> lv, r = ((r0 + (1 << lv) - 1) >> lv), b = ((b0 + (1 << lv) - 1) >> lv); if (r > w) r = w; if (b > w) b = w;
            for (int y = t; y < b; y++) for (int x = l; x < r; x++) model[lv][y * w + x] = 0x11000000u + lv * 0x10000 + y * 16 + x; } };
    for (int lv = 0; lv < 3; lv++) lockfill(lv, 0x11000000u, 0);          // all dirty at creation and by the locks: the first UpdateTexture copies everything
    CHECK_HR(d.i9->UpdateTexture(src, dst), D3D_OK); copybox(0, 0, W, W); check("initial");
    for (int lv = 0; lv < 3; lv++) lockfill(lv, 0x22000000u, D3DLOCK_NO_DIRTY_UPDATE); CHECK_HR(d.i9->UpdateTexture(src, dst), D3D_OK); check("no dirty update copies nothing");
    RECT dr = {3, 5, 9, 12}; CHECK_HR(src->AddDirtyRect(&dr), D3D_OK); CHECK_HR(d.i9->UpdateTexture(src, dst), D3D_OK);
    for (int lv = 0; lv < 3; lv++) { int w = W >> lv, l = 3 >> lv, t = 5 >> lv, r = (9 + (1 << lv) - 1) >> lv, b = (12 + (1 << lv) - 1) >> lv; if (r > w) r = w; if (b > w) b = w; for (int y = t; y < b; y++) for (int x = l; x < r; x++) model[lv][y * w + x] = 0x22000000u + lv * 0x10000 + y * 16 + x; }
    check("AddDirtyRect box shifted per level"); CHECK_HR(d.i9->UpdateTexture(src, dst), D3D_OK); check("clean again: nothing");
    src->Release(); dst->Release();
    // level skipping: 16 -> 8 (source level 1 maps to destination level 0)
    IDirect3DTexture9* s2 = nullptr; IDirect3DTexture9* d2 = nullptr; d.i9->CreateTexture(16, 16, 3, 0, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &s2, nullptr); d.i9->CreateTexture(8, 8, 2, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &d2, nullptr);
    for (int lv = 0; lv < 3; lv++) { D3DLOCKED_RECT lr; s2->LockRect(lv, &lr, nullptr, 0); for (int y = 0; y < (16 >> lv); y++) for (int x = 0; x < (16 >> lv); x++) ((uint32_t*)((uint8_t*)lr.pBits + y * lr.Pitch))[x] = 0xFF000000u | lv << 16 | y << 8 | x; s2->UnlockRect(lv); }
    CHECK_HR(d.i9->UpdateTexture(s2, d2), D3D_OK); bool ok = true;
    for (int lv = 0; lv < 2; lv++) { IDirect3DSurface9* s = nullptr; d2->GetSurfaceLevel(lv, &s); int w = 8 >> lv; IDirect3DSurface9* sys = t_surf(d, 2, w, w, 21, D3DPOOL_SYSTEMMEM); d.i9->GetRenderTargetData(s, sys); uint32_t got[64]; t_get(sys, (uint8_t*)got, w * w * 4);
        for (int y = 0; y < w; y++) for (int x = 0; x < w; x++) ok = ok && got[y * w + x] == (0xFF000000u | (uint32_t)(lv + 1) << 16 | (uint32_t)y << 8 | (uint32_t)x); s->Release(); sys->Release(); }
    CHECK(ok); s2->Release(); d2->Release();
}
void t_b() {
    t_part('b'); TDev d = t_dev(false, 64, 64); b_formats(d); b_pairs(d); b_clear(d); b_depth(d); b_scale(d); b_tex(d);
}
