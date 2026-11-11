// part f: UP draws: the transient vertex / index data as the backend sees it (BindVtxN / BindIdx snapshots), base vertex, widths, the stream-0 / index reset (and that it is not recorded into a state block), data
// of any size (a block of its own above 128 KiB), two UP draws of one pass keep their own data.
#include "t_common.h"
static std::vector<uint8_t> pat(uint32_t n, uint32_t seed) { std::vector<uint8_t> v(n); for (uint32_t i = 0; i < n; i++) v[i] = (uint8_t)(t_lcg(&seed) >> 3); return v; }
static void run_f(bool ex) {
    TDev d = t_dev(ex); t_target(d); d.i9->SetFVF(D3DFVF_XYZ);
    auto vd = pat(21 * 24, 1); t_clear(); CHECK_HR(d.i9->DrawPrimitiveUP(D3DPT_TRIANGLELIST, 7, vd.data(), 24), D3D_OK); t_flush();
    const Ev* b = t_nth(NxOp_BindVtxN, 0); const Ev* dr = t_nth(NxOp_Draw, 0);
    CHECK(b && b->a[0] == 0 && b->a[3] == 21 * 24 && b->a[4] == 24 && b->blob == vd && (b->a[2] & 255) == 0);
    CHECK(dr && dr->a[0] == NxPrim_Triangles && dr->a[1] == 21 && dr->a[2] == 1 && dr->a[3] == 0 && dr->a[4] == 0);
    IDirect3DVertexBuffer9* ov = nullptr; UINT off = 5, st = 5; CHECK_HR(d.i9->GetStreamSource(0, &ov, &off, &st), D3D_OK); CHECK(!ov && off == 0 && st == 0);
    // indexed: vertices [min, min + count) are uploaded, the base vertex is -min; 16 and 32 bit indices
    for (int wide = 0; wide < 2; wide++) {
        uint32_t min = 3, nv = 10, tri = 4, nidx = tri * 3, isz = wide ? 4 : 2; auto vd2 = pat(16 * 12, 2 + wide), id = pat(nidx * isz, 9 + wide);
        t_clear(); CHECK_HR(d.i9->DrawIndexedPrimitiveUP(D3DPT_TRIANGLELIST, min, nv, tri, id.data(), wide ? D3DFMT_INDEX32 : D3DFMT_INDEX16, vd2.data(), 12), D3D_OK); t_flush();
        b = t_nth(NxOp_BindVtxN, 0); const Ev* bi = t_nth(NxOp_BindIdx, 0); dr = t_nth(NxOp_DrawIndexed, 0);
        CHECK(b && b->a[3] == nv * 12 && b->a[4] == 12 && b->blob == std::vector<uint8_t>(vd2.begin() + min * 12, vd2.begin() + (min + nv) * 12));
        CHECK(bi && bi->a[3] == (uint32_t)wide && bi->a[2] == nidx * isz && bi->blob == id);
        CHECK(dr && dr->a[1] == nidx && dr->a[2] == 1 && dr->a[3] == 0 && (int32_t)dr->a[4] == -(int32_t)min);
        IDirect3DIndexBuffer9* oi = nullptr; d.i9->GetIndices(&oi); CHECK(!oi); d.i9->GetStreamSource(0, &ov, &off, &st); CHECK(!ov && st == 0);
    }
    // two UP draws of one pass: each binds its own copy
    auto a1 = pat(3 * 16, 21), a2 = pat(3 * 16, 22); t_clear(); d.i9->DrawPrimitiveUP(D3DPT_TRIANGLELIST, 1, a1.data(), 16); d.i9->DrawPrimitiveUP(D3DPT_TRIANGLELIST, 1, a2.data(), 16); t_flush();
    const Ev* b1 = nullptr; const Ev* b2 = nullptr; for (auto& e : g_ev) if (e.op == NxOp_BindVtxN && e.a[0] == 0) (b1 ? b2 : b1) = &e;   // stream 0 (the constant stream of the unbound inputs is another stream) CHECK(b1 && b2 && b1->blob == a1 && b2->blob == a2 && t_count(NxOp_BeginPass4) == 1);
    // the reset is not recorded into a state block (Windows rows of test_draw_primitive)
    IDirect3DVertexBuffer9* vb = t_vb(d, 4096); IDirect3DIndexBuffer9* ib = t_ib(d, 4096); d.i9->SetStreamSource(0, vb, 0, 16); d.i9->SetIndices(ib); IDirect3DStateBlock9* sb = nullptr;
    CHECK_HR(d.i9->BeginStateBlock(), D3D_OK); auto id = pat(12, 3), vv = pat(64, 4); CHECK_HR(d.i9->DrawIndexedPrimitiveUP(D3DPT_TRIANGLELIST, 0, 4, 2, id.data(), D3DFMT_INDEX16, vv.data(), 16), D3D_OK); CHECK_HR(d.i9->EndStateBlock(&sb), D3D_OK);
    d.i9->SetStreamSource(0, vb, 0, 16); d.i9->SetIndices(ib); CHECK_HR(sb->Apply(), D3D_OK);
    IDirect3DIndexBuffer9* oi = nullptr; d.i9->GetStreamSource(0, &ov, &off, &st); d.i9->GetIndices(&oi); CHECK(ov == vb && st == 16 && oi == ib);
    if (ov) ov->Release(); if (oi) oi->Release();
    // data of any size: above 128 KiB the data goes into a block of its own that is destroyed after the draw
    auto big = pat(3000 * 3 * 32, 31); t_clear(); CHECK_HR(d.i9->DrawPrimitiveUP(D3DPT_TRIANGLELIST, 3000, big.data(), 32), D3D_OK); t_flush(); b = t_nth(NxOp_BindVtxN, 0);
    CHECK(b && b->blob == big && b->a[2] == 0 && t_ncalls(NXGPU_FN_MEM_DESTROY) >= 1);
    auto bidx = pat(70000 * 2, 32), smallv = pat(16 * 4, 33); t_clear(); CHECK_HR(d.i9->DrawIndexedPrimitiveUP(D3DPT_TRIANGLELIST, 0, 4, 23333, bidx.data(), D3DFMT_INDEX16, smallv.data(), 16), D3D_OK); t_flush();
    const Ev* bi = t_nth(NxOp_BindIdx, 0); dr = t_nth(NxOp_DrawIndexed, 0); CHECK(dr && dr->a[1] == 69999 && bi && bi->blob == std::vector<uint8_t>(bidx.begin(), bidx.begin() + 69999 * 2));
}
void t_f() { t_part('f'); run_f(false); run_f(true); }   // NxDevice9 and NxDevice9Ex
