// part c: pitches (python oracle), lock flags / access, rect validation, double lock / unlock, content round trip, dirty regions -> exact sequence of recorded backend updates
#include "t_common.h"
static NxObj* O(IUnknown* p) { return nx_obj_of(p); }
static bool upd(int i, uint32_t lv, uint32_t ly, uint32_t l, uint32_t t, uint32_t r, uint32_t b, uint32_t row, int b0 = -1) {
    int n = 0; for (int k = 0; k < g_nrec; k++) if (g_rec[k].op == R_UPDATE) { if (n++ == i) { const Rec& x = g_rec[k]; return x.level == lv && x.layer == ly && x.box.l == l && x.box.t == t && x.box.f == 0 && x.box.r == r && x.box.b == b && x.box.k == 1 && x.row == row && (b0 < 0 || x.b0 == b0); } }
    return false;
}
void t_c() {
    t_part('c'); g_profile = 0; TEnv e = t_env(); NxDevice9* d = e.dev; char line[512];
    FILE* f = t_open("pitch.txt");                       // P fmt w h level row slice size
    while (f && fgets(line, sizeof line, f)) {
        unsigned fmt, w, h, lv, row, slice, size; if (sscanf(line, "P %u %u %u %u %u %u %u", &fmt, &w, &h, &lv, &row, &slice, &size) != 7) { CHECK(false); continue; }
        IDirect3DTexture9* t; if (!CHECK_HR(d->CreateTexture(w, h, 0, 0, (D3DFORMAT)fmt, D3DPOOL_SCRATCH, &t, nullptr), D3D_OK)) continue;
        D3DLOCKED_RECT lr; CHECK_HR(t->LockRect(lv, &lr, nullptr, 0), D3D_OK); CHECK_EQ(lr.Pitch, row); CHECK_EQ(static_cast<NxTexCore*>(O(t))->img->at(lv, 0)->slice, slice); CHECK_EQ(static_cast<NxTexCore*>(O(t))->img->at(lv, 0)->size, size); t->UnlockRect(lv); O(t)->release();
    }
    if (f) fclose(f);
    // ---- rect validation (Wine test_lockrect_invalid, Windows 7 column) + offsets
    static const struct { RECT r; bool ok; } rc[] = {{{60, 60, 68, 68}, true}, {{60, 60, 60, 68}, false}, {{60, 60, 68, 60}, false}, {{68, 60, 60, 68}, false}, {{60, 68, 68, 60}, false}, {{-8, 60, 0, 68}, false}, {{60, -8, 68, 0}, false},
        {{-16, 60, -8, 68}, false}, {{60, -16, 68, -8}, false}, {{60, 60, 136, 68}, false}, {{60, 60, 68, 136}, false}, {{136, 60, 144, 68}, false}, {{60, 136, 68, 144}, false}, {{0, 0, 128, 128}, true}, {{127, 127, 128, 128}, true}};
    for (int pool : {(int)D3DPOOL_SCRATCH, (int)D3DPOOL_MANAGED, (int)D3DPOOL_SYSTEMMEM}) {
        IDirect3DTexture9* t; d->CreateTexture(128, 128, 1, 0, D3DFMT_A8R8G8B8, (D3DPOOL)pool, &t, nullptr); D3DLOCKED_RECT base, lr; t->LockRect(0, &base, nullptr, 0); t->UnlockRect(0);
        for (auto& c : rc) {
            lr.pBits = (void*)0xdeadbeef; lr.Pitch = 0xdeadbeef; HRESULT hr = t->LockRect(0, &lr, &c.r, 0); CHECK_HR(hr, c.ok ? D3D_OK : D3DERR_INVALIDCALL);
            if (c.ok) { CHECK_EQ((uint8_t*)lr.pBits - (uint8_t*)base.pBits, c.r.top * 512 + c.r.left * 4); t->UnlockRect(0); } else CHECK(lr.pBits == (void*)0xdeadbeef && lr.Pitch == (INT)0xdeadbeef);   // untouched on failure
        }
        O(t)->release();
    }
    IDirect3DSurface9* s; d->CreateOffscreenPlainSurface(16, 16, (D3DFORMAT)MAKEFOURCC('D', 'X', 'T', '1'), D3DPOOL_SCRATCH, &s, nullptr); D3DLOCKED_RECT b0, lr; s->LockRect(&b0, nullptr, 0); s->UnlockRect(); CHECK_EQ(b0.Pitch, 32);
    static const struct { RECT r; bool ok; int off; } bc[] = {{{0, 0, 4, 4}, true, 0}, {{4, 8, 12, 16}, true, 2 * 32 + 8}, {{1, 0, 5, 4}, false, 0}, {{1, 0, 4, 4}, false, 0}, {{0, 2, 4, 4}, false, 0}, {{0, 0, 4, 3}, false, 0}, {{0, 1, 4, 5}, false, 0}, {{0, 0, 6, 4}, false, 0}, {{0, 0, 4, 6}, false, 0}, {{0, 0, 16, 16}, true, 0}, {{12, 12, 16, 16}, true, 3 * 32 + 24}};
    for (auto& c : bc) { HRESULT hr = s->LockRect(&lr, &c.r, 0); CHECK_HR(hr, c.ok ? D3D_OK : D3DERR_INVALIDCALL); if (c.ok) { CHECK_EQ((uint8_t*)lr.pBits - (uint8_t*)b0.pBits, c.off); s->UnlockRect(); } }
    D3DSURFACE_DESC sd; s->GetDesc(&sd); CHECK(sd.Pool == D3DPOOL_SCRATCH && sd.Usage == 0); O(s)->release();
    // ---- double lock / unlock rules
    IDirect3DTexture9* t; d->CreateTexture(8, 8, 2, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr); D3DLOCKED_RECT a;
    CHECK_HR(t->LockRect(0, &a, nullptr, 0), D3D_OK); CHECK_HR(t->LockRect(0, &lr, nullptr, 0), D3DERR_INVALIDCALL); CHECK_HR(t->LockRect(1, &lr, nullptr, 0), D3D_OK);   // other level: fine
    CHECK_HR(t->UnlockRect(0), D3D_OK); CHECK_HR(t->UnlockRect(1), D3D_OK); CHECK_HR(t->UnlockRect(0), D3D_OK); CHECK_HR(t->UnlockRect(2), D3DERR_INVALIDCALL); CHECK_HR(t->LockRect(2, &lr, nullptr, 0), D3DERR_INVALIDCALL); CHECK_HR(t->LockRect(0, nullptr, nullptr, 0), D3DERR_INVALIDCALL);
    IDirect3DSurface9* l0; t->GetSurfaceLevel(0, &l0); CHECK_HR(l0->UnlockRect(), D3D_OK); CHECK_HR(l0->LockRect(&a, nullptr, 0), D3D_OK); CHECK_HR(t->LockRect(0, &lr, nullptr, 0), D3DERR_INVALIDCALL); CHECK_HR(l0->UnlockRect(), D3D_OK); l0->Release(); O(t)->release();
    IDirect3DCubeTexture9* c; d->CreateCubeTexture(8, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &c, nullptr); CHECK_HR(c->UnlockRect(D3DCUBEMAP_FACE_POSITIVE_Y, 0), D3DERR_INVALIDCALL); CHECK_HR(c->LockRect(D3DCUBEMAP_FACE_POSITIVE_Y, 0, &a, nullptr, 0), D3D_OK); uint8_t* py = (uint8_t*)a.pBits; memset(py, 0x11, 4);
    CHECK_HR(c->LockRect(D3DCUBEMAP_FACE_NEGATIVE_Y, 0, &a, nullptr, 0), D3D_OK); CHECK(a.pBits != py && ((uint8_t*)a.pBits)[0] == 0); c->UnlockRect(D3DCUBEMAP_FACE_NEGATIVE_Y, 0); c->UnlockRect(D3DCUBEMAP_FACE_POSITIVE_Y, 0); CHECK_HR(c->LockRect((D3DCUBEMAP_FACES)6, 0, &a, nullptr, 0), D3DERR_INVALIDCALL);
    CHECK_HR(c->LockRect(D3DCUBEMAP_FACE_POSITIVE_Y, 0, &a, nullptr, D3DLOCK_READONLY), D3D_OK); CHECK(a.pBits == py && py[0] == 0x11); c->UnlockRect(D3DCUBEMAP_FACE_POSITIVE_Y, 0); O(c)->release();
    d->CreateOffscreenPlainSurface(4, 4, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &s, nullptr); CHECK_HR(s->UnlockRect(), D3DERR_INVALIDCALL); CHECK_HR(s->LockRect(&a, nullptr, D3DLOCK_NOSYSLOCK | D3DLOCK_DONOTWAIT), D3D_OK); CHECK_HR(s->LockRect(&lr, nullptr, 0), D3DERR_INVALIDCALL); CHECK_HR(s->UnlockRect(), D3D_OK); O(s)->release();
    // ---- access: DEFAULT is not lockable unless DYNAMIC / mappable format / lockable RT; DISCARD acts only without READ on a DYNAMIC GPU resource
    d->CreateTexture(8, 8, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &t, nullptr); for (DWORD fl : std::initializer_list<DWORD>{(DWORD)0, (DWORD)D3DLOCK_READONLY, (DWORD)D3DLOCK_DISCARD}) CHECK_HR(t->LockRect(0, &a, nullptr, fl), D3DERR_INVALIDCALL); O(t)->release();
    d->CreateTexture(8, 8, 1, D3DUSAGE_DYNAMIC, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &t, nullptr);
    for (DWORD fl : std::initializer_list<DWORD>{(DWORD)0, (DWORD)D3DLOCK_READONLY, (DWORD)D3DLOCK_DISCARD, (DWORD)D3DLOCK_NOOVERWRITE, (DWORD)(D3DLOCK_DISCARD | D3DLOCK_NOOVERWRITE), (DWORD)(D3DLOCK_DISCARD | D3DLOCK_READONLY), (DWORD)D3DLOCK_NO_DIRTY_UPDATE, (DWORD)0xdeadbeefu}) { CHECK_HR(t->LockRect(0, &a, nullptr, fl), D3D_OK); t->UnlockRect(0); }
    NxImg* im = static_cast<NxTexCore*>(O(t))->img; nx_img_gpu_wrote(im, 0, 0); t_clear(); t->LockRect(0, &a, nullptr, D3DLOCK_DISCARD); CHECK_EQ(t_count(R_READ), 0); t->UnlockRect(0);             // discard: no readback
    nx_img_gpu_wrote(im, 0, 0); t->LockRect(0, &a, nullptr, D3DLOCK_READONLY); CHECK_EQ(t_count(R_READ), 1); CHECK_EQ(((uint8_t*)a.pBits)[0], 0xC0); t->UnlockRect(0); t_clear(); t->LockRect(0, &a, nullptr, D3DLOCK_READONLY); CHECK_EQ(t_count(R_READ), 0); t->UnlockRect(0); O(t)->release();
    IDirect3DSurface9* rt; CHECK_HR(d->CreateRenderTarget(8, 8, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_NONE, 0, FALSE, &rt, nullptr), D3D_OK); CHECK_HR(rt->LockRect(&a, nullptr, 0), D3DERR_INVALIDCALL); O(rt)->release();
    CHECK_HR(d->CreateRenderTarget(8, 8, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_NONE, 0, TRUE, &rt, nullptr), D3D_OK); nx_img_gpu_wrote(static_cast<NxSurfCore*>(O(rt))->img, 0, 0); t_clear();
    CHECK_HR(rt->LockRect(&a, nullptr, 0), D3D_OK); CHECK_EQ(t_count(R_READ), 1); rt->UnlockRect(); O(rt)->release();
    CHECK_HR(d->CreateRenderTarget(8, 8, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_4_SAMPLES, 0, TRUE, &rt, nullptr), D3DERR_INVALIDCALL);       // lockable + multisampled
    // ---- dirty regions -> recorded updates
    d->CreateTexture(16, 16, 3, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr); t_clear(); O(t)->preLoad();
    CHECK_EQ(t_count(R_UPDATE), 3); CHECK(upd(0, 0, 0, 0, 0, 16, 16, 64)); CHECK(upd(1, 1, 0, 0, 0, 8, 8, 32)); CHECK(upd(2, 2, 0, 0, 0, 4, 4, 16));                    // initial: the whole image (wined3d_texture_init)
    t_clear(); O(t)->preLoad(); CHECK_EQ(g_nrec, 0);                                                                                                            // clean
    RECT r1 = {2, 2, 6, 6}, r2 = {10, 4, 14, 12}; t->LockRect(0, &a, &r1, 0); *(uint8_t*)a.pBits = 0x77; t->UnlockRect(0); t->LockRect(0, &a, &r2, 0); t->UnlockRect(0);
    t_clear(); O(t)->preLoad(); CHECK_EQ(t_count(R_UPDATE), 1); CHECK(upd(0, 0, 0, 2, 2, 14, 12, 64, 0x77));                                                    // union box of both locks, data from its first texel
    t->LockRect(0, &a, &r1, D3DLOCK_NO_DIRTY_UPDATE); t->UnlockRect(0); t->LockRect(0, &a, &r1, D3DLOCK_READONLY); t->UnlockRect(0); t_clear(); O(t)->preLoad(); CHECK_EQ(g_nrec, 0);
    t->LockRect(1, &a, &r1 /* invalid for 8x8? no: 2..6 fits */, 0); t->UnlockRect(1); t_clear(); O(t)->preLoad(); CHECK_EQ(t_count(R_UPDATE), 1); CHECK(upd(0, 1, 0, 0, 0, 8, 8, 32));  // levels > 0: the whole level
    t->LockRect(1, &a, &r1, D3DLOCK_NO_DIRTY_UPDATE); t->UnlockRect(1); t_clear(); O(t)->preLoad(); CHECK_EQ(t_count(R_UPDATE), 1);                               // NO_DIRTY_UPDATE does not apply below level 0
    RECT r3 = {1, 1, 3, 3}, bad1 = {0, 0, 20, 20}, bad2 = {4, 4, 4, 8}; CHECK_HR(t->AddDirtyRect(&r3), D3D_OK); CHECK_HR(t->AddDirtyRect(&bad1), D3DERR_INVALIDCALL); CHECK_HR(t->AddDirtyRect(&bad2), D3DERR_INVALIDCALL);
    t_clear(); O(t)->preLoad(); CHECK_EQ(t_count(R_UPDATE), 1); CHECK(upd(0, 0, 0, 1, 1, 3, 3, 64)); CHECK_HR(t->AddDirtyRect(nullptr), D3D_OK); t_clear(); O(t)->preLoad(); CHECK(upd(0, 0, 0, 0, 0, 16, 16, 64));
    O(t)->release();
    d->CreateTexture(16, 16, 1, D3DUSAGE_DYNAMIC, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &t, nullptr); im = static_cast<NxTexCore*>(O(t))->img; t->LockRect(0, &a, &r1, 0); t->UnlockRect(0); t_clear(); im->upload(); CHECK_EQ(t_count(R_UPDATE), 1); CHECK(upd(0, 0, 0, 0, 0, 16, 16, 64)); O(t)->release();   // DEFAULT: whole level
    d->CreateTexture(16, 16, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &t, nullptr); im = static_cast<NxTexCore*>(O(t))->img; t->LockRect(0, &a, &r1, 0); t->UnlockRect(0); t_clear(); im->upload(); CHECK_EQ(g_nrec, 0); CHECK(im->at(0, 0)->dirty && im->at(0, 0)->dbox.r == 16); O(t)->release();
    // converted / broken formats: native texels
    d->CreateTexture(8, 8, 1, 0, (D3DFORMAT)50 /* L8 */, D3DPOOL_MANAGED, &t, nullptr); O(t)->preLoad(); RECT r4 = {0, 0, 4, 4}; t->LockRect(0, &a, &r4, 0); CHECK_EQ(a.Pitch, 8); *(uint8_t*)a.pBits = 0x40; t->UnlockRect(0);
    t_clear(); O(t)->preLoad(); CHECK(upd(0, 0, 0, 0, 0, 4, 4, 16, 0x40)); O(t)->release();                                                                      // L8 -> RGBA8: (L,L,L,255), tight rows of 4 texels
    d->CreateTexture(8, 8, 1, 0, (D3DFORMAT)MAKEFOURCC('A', 'T', 'I', '2'), D3DPOOL_MANAGED, &t, nullptr); t_clear(); O(t)->preLoad(); CHECK(upd(0, 0, 0, 0, 0, 8, 8, 32)); t->LockRect(0, &a, nullptr, 0); CHECK_EQ(a.Pitch, 8); t->UnlockRect(0); O(t)->release();   // BROKEN_PITCH: blocks contiguous
    // ---- content round trip, levels independent
    d->CreateTexture(8, 8, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr);
    for (int l = 0; l < 4; l++) { t->LockRect(l, &a, nullptr, 0); for (int i = 0; i < (8 >> l) * (8 >> l) * 4; i++) ((uint8_t*)a.pBits)[i] = (uint8_t)(l * 16 + i); t->UnlockRect(l); }
    for (int l = 0; l < 4; l++) { t->LockRect(l, &a, nullptr, D3DLOCK_READONLY); bool ok = true; for (int i = 0; i < (8 >> l) * (8 >> l) * 4; i++) ok &= ((uint8_t*)a.pBits)[i] == (uint8_t)(l * 16 + i); CHECK(ok); t->UnlockRect(l); }
    O(t)->release();
    IDirect3DVolumeTexture9* v; d->CreateVolumeTexture(4, 4, 4, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &v, nullptr); D3DLOCKED_BOX lb; lb.pBits = (void*)1; lb.RowPitch = 5; D3DBOX bx = {0, 0, 9, 4, 0, 4};
    CHECK_HR(v->LockBox(0, &lb, &bx, 0), D3DERR_INVALIDCALL); CHECK(lb.pBits == nullptr && lb.RowPitch == 0 && lb.SlicePitch == 0); CHECK_HR(v->UnlockBox(0), D3DERR_INVALIDCALL); O(v)->release();      // LockBox writes its outputs on failure
}
