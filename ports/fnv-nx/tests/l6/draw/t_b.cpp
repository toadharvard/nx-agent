// part b: vertex / index counts of the six primitive types (oracle.py vcount), BaseVertexIndex, StartVertex / StartIndex, instancing (frequency word -> instance count), index widths, stream offsets.
#include "t_common.h"
static void run_b(bool ex) {
    TDev d = t_dev(ex); t_target(d); IDirect3DVertexBuffer9* vb = t_vb(d, 65536); IDirect3DIndexBuffer9* ib16 = t_ib(d, 65536, false); IDirect3DIndexBuffer9* ib32 = t_ib(d, 65536, true);
    static const uint32_t nxprim[7] = {99, NxPrim_Points, NxPrim_Lines, NxPrim_LineStrip, NxPrim_Triangles, NxPrim_TriStrip, NxPrim_TriFan};   // D3DPT_* -> NxPrim (nx/gpu_abi.h)
    d.i9->SetFVF(D3DFVF_XYZ); d.i9->SetStreamSource(0, vb, 0, 16); d.i9->SetIndices(ib16);
    FILE* f = t_open("b.txt"); char line[256]; int n = 0;
    while (f && fgets(line, sizeof line, f)) {
        int indexed, prim, base; unsigned count, first, freq, evc, einst;
        if (sscanf(line, "%d %d %u %u %d %u %u %u", &indexed, &prim, &count, &first, &base, &freq, &evc, &einst) != 8) { CHECK(false); continue; }
        CHECK_HR(d.i9->SetStreamSourceFreq(0, freq), D3D_OK); t_clear();
        HRESULT hr = indexed ? d.i9->DrawIndexedPrimitive((D3DPRIMITIVETYPE)prim, base, 0, 0, first, count) : d.i9->DrawPrimitive((D3DPRIMITIVETYPE)prim, first, count);
        t_flush(); const Ev* e = t_nth(indexed ? NxOp_DrawIndexed : NxOp_Draw, 0); bool ok = hr == D3D_OK && e && t_count(indexed ? NxOp_Draw : NxOp_DrawIndexed) == 0;
        if (ok) ok = e->a[0] == nxprim[prim] && e->a[1] == evc && e->a[2] == einst && e->a[3] == first && (indexed ? e->a[4] == (uint32_t)base && e->a[5] == 0 : e->a[4] == 0);
        if (!CHECK(ok) && n < 5) printf("  case: %s", line);
        n++;
    }
    if (f) fclose(f); CHECK(n > 1000); d.i9->SetStreamSourceFreq(0, 1);
    // index width and byte ranges of the binds
    d.i9->SetIndices(ib32); t_clear(); d.i9->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 3, 0, 1); t_flush(); const Ev* bi = t_nth(NxOp_BindIdx, 0);
    CHECK(bi && bi->a[1] == 0 && bi->a[2] == 65536 && bi->a[3] == 1); d.i9->SetIndices(ib16); t_clear(); d.i9->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 3, 0, 1); t_flush(); bi = t_nth(NxOp_BindIdx, 0);
    CHECK(bi && bi->a[2] == 65536 && bi->a[3] == 0);
    d.i9->SetStreamSource(0, vb, 256, 24); t_clear(); d.i9->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1); t_flush(); const Ev* bv = t_nth(NxOp_BindVtxN, 0);
    CHECK(bv && bv->a[0] == 0 && bv->a[2] == 256 && bv->a[3] == 65536 - 256 && bv->a[4] == 24);
    // a second identical draw in the same pass binds nothing again
    t_clear(); d.i9->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1); d.i9->DrawPrimitive(D3DPT_TRIANGLELIST, 3, 1); t_flush(); CHECK(t_count(NxOp_BindVtxN) == 2 && t_count(NxOp_Draw) == 2 && t_count(NxOp_BindState) == 1 && t_count(NxOp_BeginPass4) == 1);
}
void t_b() { t_part('b'); run_b(false); run_b(true); }   // NxDevice9 and NxDevice9Ex
