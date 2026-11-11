// part d: the glue. Flush rule (a read observes earlier ops; nothing is submitted before something observes), destroy of a referenced image, a pass / payload larger than a stream segment, the ring of segments,
// the buffer mirror + preload paths, queries end to end through the device (Issue / GetData), query ids.
#include "t_common.h"
#include <initializer_list>
static int submits() { return g_calls[NXGPU_FN_SUBMIT]; }
static void d_order(TDev& d) {
    IDirect3DSurface9* rt = t_surf(d, 0, 16, 16, 21, D3DPOOL_DEFAULT, 0, false); IDirect3DSurface9* b = t_surf(d, 0, 16, 16, 21, D3DPOOL_DEFAULT, 0, false); IDirect3DSurface9* sys = t_surf(d, 2, 16, 16, 21, D3DPOOL_SYSTEMMEM);
    d.i9->SetRenderTarget(0, rt); D3DVIEWPORT9 vp = {0, 0, 16, 16, 0, 1}; d.i9->SetViewport(&vp); d.i9->SetRenderState(D3DRS_SCISSORTESTENABLE, 0);
    nxs_finish(); int s0 = submits(), r0 = g_calls[NXGPU_FN_IMAGE_READ];
    d.i9->ColorFill(rt, nullptr, 0xFFFF0000); d.i9->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF00FF00, 0, 0); RECT r = {0, 0, 8, 16}; d.i9->ColorFill(b, nullptr, 0xFF0000FF); d.i9->StretchRect(b, &r, rt, &r, D3DTEXF_POINT);
    CHECK_EQ(submits(), s0); CHECK_EQ(g_calls[NXGPU_FN_IMAGE_READ], r0);                      // R2: nothing observed yet, nothing submitted
    CHECK_HR(d.i9->GetRenderTargetData(rt, sys), D3D_OK); CHECK_EQ(submits(), s0 + 1); CHECK_EQ(g_calls[NXGPU_FN_IMAGE_READ], r0 + 1);   // R3: one submit, then the read
    uint32_t px[256]; CHECK(t_get(sys, (uint8_t*)px, sizeof px)); CHECK_EQ(px[0], 0xFF0000FFu); CHECK_EQ(px[8], 0xFF00FF00u); CHECK_EQ(px[15 * 16 + 7], 0xFF0000FFu); CHECK_EQ(px[15 * 16 + 8], 0xFF00FF00u);   // the order of the ops decided
    // R2c: a referenced image that is destroyed flushes; an image nothing references does not
    IDirect3DSurface9* t1 = t_surf(d, 0, 16, 16, 21, D3DPOOL_DEFAULT, 0, false); nxs_finish(); int s1 = submits(); d.i9->ColorFill(t1, nullptr, 0xFF123456); CHECK_EQ(submits(), s1); t1->Release(); CHECK_EQ(submits(), s1 + 1);
    IDirect3DSurface9* t2 = t_surf(d, 2, 16, 16, 21, D3DPOOL_DEFAULT); nxs_finish(); s1 = submits(); t2->Release(); CHECK_EQ(submits(), s1);
    for (auto* s : {rt, b, sys}) s->Release();
}
static void d_big(TDev& d) {
    const int W = 256; IDirect3DSurface9* rt = t_surf(d, 0, W, W, 21, D3DPOOL_DEFAULT, 0, false); d.i9->SetRenderTarget(0, rt); D3DVIEWPORT9 vp = {0, 0, W, W, 0, 1}; d.i9->SetViewport(&vp); d.i9->SetRenderState(D3DRS_SCISSORTESTENABLE, 0);
    d.i9->ColorFill(rt, nullptr, 0xFF000000); nxs_finish(); int s0 = submits(); const int N = 40000; D3DRECT* rc = (D3DRECT*)malloc(sizeof(D3DRECT) * N);
    for (int i = 0; i < N; i++) { int x = i % 200, y = i / 200; rc[i] = {x, y, x + 1, y + 1}; }   // 40000 one-pixel rects: 40000 clear ops = 1.4 MB: the pass is split across segments
    CHECK_HR(d.i9->Clear(N, rc, D3DCLEAR_TARGET, 0xFFFFFFFF, 0, 0), D3D_OK); IDirect3DSurface9* sys = t_surf(d, 2, W, W, 21, D3DPOOL_SYSTEMMEM); CHECK_HR(d.i9->GetRenderTargetData(rt, sys), D3D_OK);
    CHECK(submits() >= s0 + 2); static uint32_t px[W * W]; CHECK(t_get(sys, (uint8_t*)px, sizeof px)); bool ok = true;
    for (int y = 0; y < W; y++) for (int x = 0; x < W; x++) ok = ok && px[y * W + x] == ((x < 200 && y < 200) ? 0xFFFFFFFFu : 0xFF000000u); CHECK(ok); free(rc);
    // a payload larger than half a segment: UpdateSurface of 4 MiB (split into sub-boxes, E4), then many small flushes (the ring of segments)
    const int B = 1024; IDirect3DSurface9* big = t_surf(d, 2, B, B, 21, D3DPOOL_SYSTEMMEM); IDirect3DSurface9* g = t_surf(d, 2, B, B, 21, D3DPOOL_DEFAULT); uint32_t* data = (uint32_t*)malloc(B * B * 4);
    for (int i = 0; i < B * B; i++) data[i] = 0xFF000000u ^ (uint32_t)(i * 2654435761u); t_put(big, (uint8_t*)data, B * B * 4); int m0 = g_calls[NXGPU_FN_MEM_CREATE];
    CHECK_HR(d.i9->UpdateSurface(big, nullptr, g, nullptr), D3D_OK); IDirect3DSurface9* back = t_surf(d, 2, B, B, 21, D3DPOOL_SYSTEMMEM); CHECK_HR(d.i9->GetRenderTargetData(g, back), D3D_OK);
    uint32_t* out = (uint32_t*)malloc(B * B * 4); CHECK(t_get(back, (uint8_t*)out, B * B * 4)); CHECK(!memcmp(out, data, B * B * 4)); CHECK_EQ(g_calls[NXGPU_FN_MEM_CREATE], m0);   // glue_stream.h E4: a 4 MiB box is split into ops of at most half a segment, no block of its own (an op never allocates)
    for (int i = 0; i < 12; i++) { d.i9->ColorFill(rt, nullptr, 0xFF000000 | i); CHECK_HR(d.i9->GetRenderTargetData(rt, sys), D3D_OK); } CHECK(t_get(sys, (uint8_t*)px, sizeof px)); CHECK_EQ(px[0], 0xFF00000Bu);
    CHECK_EQ(nxs_in_flight() <= NXS_RING, 1); free(data); free(out); for (auto* s : {rt, sys, big, g, back}) s->Release();
}
static void d_buffers(TDev& d) {
    IDirect3DVertexBuffer9* vb = nullptr; CHECK_HR(d.i9->CreateVertexBuffer(8192, 0, 0, D3DPOOL_MANAGED, &vb, nullptr), D3D_OK); if (!vb) return;
    void* p; CHECK_HR(vb->Lock(0, 0, &p, 0), D3D_OK); for (int i = 0; i < 8192; i++) ((uint8_t*)p)[i] = (uint8_t)(i * 7); vb->Unlock();
    NxBufCore* bc = static_cast<NxBufCore*>(nx_obj_of(vb)); CHECK(bc->gblock == 0); int m0 = g_calls[NXGPU_FN_MEM_CREATE];
    vb->PreLoad(); CHECK(bc->gblock != 0); CHECK_EQ(g_calls[NXGPU_FN_MEM_CREATE], m0 + 1); CHECK(!memcmp(bc->gmem, bc->mem, 8192));                // PreLoad reaches the buffer path
    CHECK_HR(vb->Lock(100, 20, &p, 0), D3D_OK); memset(p, 0xAB, 20); vb->Unlock(); CHECK(memcmp(bc->gmem, bc->mem, 8192) != 0); vb->PreLoad(); CHECK(!memcmp(bc->gmem, bc->mem, 8192)); CHECK_EQ(g_calls[NXGPU_FN_MEM_CREATE], m0 + 1);
    int md = g_calls[NXGPU_FN_MEM_DESTROY]; vb->Release(); CHECK_EQ(g_calls[NXGPU_FN_MEM_DESTROY], md + 1);
    // PreLoad of a managed texture reaches nx_tex_preload: the dirty regions are uploaded (image_read of the GPU image returns the texels)
    IDirect3DTexture9* t = nullptr; CHECK_HR(d.i9->CreateTexture(8, 8, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr), D3D_OK); if (!t) return;
    D3DLOCKED_RECT lr; t->LockRect(0, &lr, nullptr, 0); for (int y = 0; y < 8; y++) for (int x = 0; x < 8; x++) ((uint32_t*)((uint8_t*)lr.pBits + y * lr.Pitch))[x] = 0xFF000000u | y << 8 | x; t->UnlockRect(0);
    t->PreLoad(); NxImg* im = nx_img_of(nx_obj_of(t)); uint32_t px[64]; nxd3d_image_read(im->owner, im->gpu, 0, 0, px, 32, 0); bool ok = true; for (int i = 0; i < 64; i++) ok = ok && px[i] == (0xFF000000u | (i / 8) << 8 | (i % 8)); CHECK(ok); t->Release();
}
static bool poll(IDirect3DQuery9* q, void* out, DWORD sz) { for (int i = 0; i < 200; i++) { HRESULT hr = q->GetData(out, sz, D3DGETDATA_FLUSH); if (hr == S_OK) return true; } return false; }
static void d_queries(TDev& d) {
    IDirect3DQuery9* ev = nullptr; CHECK_HR(d.i9->CreateQuery(D3DQUERYTYPE_EVENT, &ev), D3D_OK); BOOL b = 0; CHECK_HR(ev->GetData(&b, 4, 0), S_OK);   // never issued: 0xdd bytes, S_OK (res_query.cpp)
    IDirect3DSurface9* rt = t_surf(d, 0, 16, 16, 21, D3DPOOL_DEFAULT, 0, false); d.i9->ColorFill(rt, nullptr, 0xFF112233); CHECK_HR(ev->Issue(D3DISSUE_END), D3D_OK); CHECK(poll(ev, &b, 4)); CHECK_EQ(b, 1);
    IDirect3DQuery9* ts = nullptr; CHECK_HR(d.i9->CreateQuery(D3DQUERYTYPE_TIMESTAMP, &ts), D3D_OK); IDirect3DQuery9* ts2 = nullptr; d.i9->CreateQuery(D3DQUERYTYPE_TIMESTAMP, &ts2);
    uint64_t t1 = 0, t2 = 0; ts->Issue(D3DISSUE_END); d.i9->ColorFill(rt, nullptr, 0xFF445566); ts2->Issue(D3DISSUE_END); CHECK(poll(ts, &t1, 8)); CHECK(poll(ts2, &t2, 8)); CHECK(t1 != 0 && t2 >= t1);
    IDirect3DQuery9* fr = nullptr; IDirect3DQuery9* dj = nullptr; d.i9->CreateQuery(D3DQUERYTYPE_TIMESTAMPFREQ, &fr); d.i9->CreateQuery(D3DQUERYTYPE_TIMESTAMPDISJOINT, &dj);
    uint64_t fq = 0; BOOL dis = 1; fr->Issue(D3DISSUE_END); dj->Issue(D3DISSUE_END); CHECK(poll(fr, &fq, 8)); CHECK_EQ(fq, 1000000000ull); CHECK(poll(dj, &dis, 4)); CHECK_EQ(dis, 0);
    IDirect3DQuery9* oc = nullptr; CHECK_HR(d.i9->CreateQuery(D3DQUERYTYPE_OCCLUSION, &oc), D3D_OK); DWORD px = 77;
    oc->Issue(D3DISSUE_BEGIN); oc->Issue(D3DISSUE_END); CHECK(poll(oc, &px, 4)); CHECK_EQ(px, 0);                 // an interval with no draw counts 0
    px = 77; oc->Issue(D3DISSUE_END); CHECK(poll(oc, &px, 4)); CHECK_EQ(px, 0);                                      // END without BEGIN: an empty interval
    CHECK_HR(d.i9->CreateQuery(D3DQUERYTYPE_VCACHE, &oc), D3DERR_NOTAVAILABLE); CHECK_HR(d.i9->CreateQuery((D3DQUERYTYPE)7, nullptr), D3DERR_NOTAVAILABLE); CHECK_HR(d.i9->CreateQuery(D3DQUERYTYPE_OCCLUSION, nullptr), D3D_OK);
    // ids 1..4096: the 4097th live id-holding query is refused, a released id is reused
    static IDirect3DQuery9* q[4200]; int n = 0; for (; n < 4200; n++) { HRESULT hr = d.i9->CreateQuery(D3DQUERYTYPE_EVENT, &q[n]); if (hr != D3D_OK) { CHECK_HR(hr, D3DERR_OUTOFVIDEOMEMORY); break; } }
    CHECK(n >= 4000 && n < 4200); q[0]->Release(); IDirect3DQuery9* again = nullptr; CHECK_HR(d.i9->CreateQuery(D3DQUERYTYPE_EVENT, &again), D3D_OK); for (int i = 1; i < n; i++) q[i]->Release();
    for (auto* s : {ev, ts, ts2, fr, dj, oc, again}) if (s) s->Release(); rt->Release();
}
void t_d() { t_part('d'); TDev d = t_dev(false, 64, 64); d_order(d); d_big(d); d_buffers(d); d_queries(d); }
