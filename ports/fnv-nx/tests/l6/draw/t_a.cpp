// part a: validation of the four draw calls over their parameter domain against the python oracle (oracle.py oracle_a: Wine 9.0 d3d9/device.c + the Windows rows of d3d9/tests/device.c test_draw_primitive).
// Each line: M prim count stride decl vb ib data idata ifmt | hr draws post_vb post_stride post_ib itype
#include "t_common.h"
static void run_a(bool ex) {
    TDev d = t_dev(ex); t_target(d); IDirect3DVertexBuffer9* vb = t_vb(d, 4096); IDirect3DIndexBuffer9* ib = t_ib(d, 4096); static uint8_t vdata[4096], idata[4096]; memset(vdata, 7, sizeof vdata);
    FILE* f = t_open("a.txt"); char line[512]; int n = 0;
    while (f && fgets(line, sizeof line, f)) {
        int M, prim, count, stride, decl, hvb, hib, data, idt, ifmt, ehr, edraws, epvb, epst, epib, eit;
        if (sscanf(line, "%d %d %d %d %d %d %d %d %d %d %x %d %d %d %d %d", &M, &prim, &count, &stride, &decl, &hvb, &hib, &data, &idt, &ifmt, &ehr, &edraws, &epvb, &epst, &epib, &eit) != 16) { CHECK(false); continue; }
        if (decl) d.i9->SetFVF(D3DFVF_XYZ); else d.i9->SetVertexDeclaration(nullptr);
        d.i9->SetStreamSource(0, vb, 0, hvb ? 16 : 0); if (!hvb) d.i9->SetStreamSource(0, nullptr, 0, 0); d.i9->SetIndices(hib ? ib : nullptr); t_clear(); HRESULT hr = D3D_OK;
        switch (M) {
        case 0: hr = d.i9->DrawPrimitive((D3DPRIMITIVETYPE)prim, 0, count); break;
        case 1: hr = d.i9->DrawIndexedPrimitive((D3DPRIMITIVETYPE)prim, 0, 0, 4, 0, count); break;
        case 2: hr = d.i9->DrawPrimitiveUP((D3DPRIMITIVETYPE)prim, count, data ? vdata : nullptr, stride); break;
        default: hr = d.i9->DrawIndexedPrimitiveUP((D3DPRIMITIVETYPE)prim, 0, 4, count, idt ? idata : nullptr, (D3DFORMAT)ifmt, data ? vdata : nullptr, stride); break;
        }
        t_flush(); int draws0 = 0; (void)draws0; int draws = t_count(NxOp_Draw) + t_count(NxOp_DrawIndexed); IDirect3DVertexBuffer9* ov = nullptr; UINT off = 9, st = 9; IDirect3DIndexBuffer9* oi = nullptr;
        d.i9->GetStreamSource(0, &ov, &off, &st); d.i9->GetIndices(&oi);
        bool ok = (uint32_t)hr == (uint32_t)ehr && draws == edraws && (ov != nullptr) == (epvb != 0) && st == (UINT)epst && (oi != nullptr) == (epib != 0) && off == 0;
        
        if (ok && draws && M == 1) ok = t_count(NxOp_DrawIndexed) == 1;
        if (ok && draws && M == 3) { const Ev* b = t_nth(NxOp_BindIdx, 0); ok = b && b->a[3] == (uint32_t)(ifmt == D3DFMT_INDEX16 ? 0 : 1) && b->blob.size() == (size_t)nx_prim_vertices(prim, count) * (ifmt == D3DFMT_INDEX16 ? 2 : 4); }
        if (!CHECK(ok) && n < 5) printf("  case: %s  got hr %x draws %d vb %d st %u ib %d\n", line, (unsigned)hr, draws, ov != nullptr, st, oi != nullptr);
        n++;
    }
    if (f) fclose(f); CHECK(n > 1000);
}
void t_a() { t_part('a'); run_a(false); run_a(true); }   // NxDevice9 and NxDevice9Ex
