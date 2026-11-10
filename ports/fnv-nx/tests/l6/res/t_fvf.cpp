// part c: nx_fvf_to_decl against the python oracle (fvf.txt), SetFVF / GetFVF / GetVertexDeclaration read-back, device-cached implicit declarations
#include "t_common.h"
#include "res_core.h"
typedef D3DVERTEXELEMENT9 E;
IDirect3DVertexDeclaration9* nx_device_fvf_decl(NxDeviceCore* dev, DWORD fvf);
void t_fvf() {
    t_part('c'); FILE* f = t_open("fvf.txt"); char line[1024]; int nl = 0, nvalid = 0, ninvalid = 0;
    while (f && fgets(line, sizeof line, f)) {
        unsigned fvf, valid, n, stride; int pos = 0; if (sscanf(line, "%x %u %u %u%n", &fvf, &valid, &n, &stride, &pos) < 4) { CHECK(false); continue; }
        NxFvfLayout L; memset(&L, 0x77, sizeof L); bool ok = nx_fvf_to_decl(fvf, &L); nl++;
        if (!CHECK_EQ(ok, valid) || !valid) { ninvalid++; if (!valid) CHECK(L.n == 0x77777777u); continue; }                    // invalid: out untouched
        nvalid++; bool same = L.n == n && L.stride == stride;
        for (unsigned i = 0; i < n && same; i++) { unsigned o, t, u, x; int c = 0; if (sscanf(line + pos, " %u,%u,%u,%u%n", &o, &t, &u, &x, &c) != 4) { same = false; break; } pos += c;
            same &= L.el[i].Stream == 0 && L.el[i].Method == 0 && L.el[i].Offset == o && L.el[i].Type == t && L.el[i].Usage == u && L.el[i].UsageIndex == x; }
        same &= L.el[n].Stream == 0xFF && L.el[n].Type == D3DDECLTYPE_UNUSED && L.el[n].Offset == 0 && L.el[n].Usage == 0;
        if (!CHECK(same)) printf("  fvf %08x\n", fvf);
    }
    if (f) fclose(f); CHECK(nl > 40000 && nvalid > 30000 && ninvalid > 5000); printf("fvf cases %d (valid %d, invalid %d)\n", nl, nvalid, ninvalid);

    // ---- read-back: SetFVF / GetFVF / GetVertexDeclaration / SetVertexDeclaration (Wine device.c d3d9_device_SetFVF / GetFVF; Wine tests test_fvf_decl_conversion / test_fvf_decl_management)
    TEnv env = t_env(); NxDevice9* d = env.dev; DWORD fvf = 0xdeadbeef; IDirect3DVertexDeclaration9 *g1, *g2, *g3, *cur;
    CHECK_HR(d->GetFVF(&fvf), D3D_OK); CHECK_EQ(fvf, 0); CHECK_HR(d->GetVertexDeclaration(&g1), D3D_OK); CHECK(g1 == nullptr);                         // device default: no declaration
    CHECK_HR(d->SetFVF(D3DFVF_XYZRHW), D3D_OK); CHECK_HR(d->GetFVF(&fvf), D3D_OK); CHECK_EQ(fvf, D3DFVF_XYZRHW);
    CHECK_HR(d->GetVertexDeclaration(&g1), D3D_OK); CHECK(g1 != nullptr); NxResDecl* r1 = static_cast<NxResDecl*>(nx_obj_of(g1)); NxFvfLayout L; nx_fvf_to_decl(D3DFVF_XYZRHW, &L);
    CHECK(r1->n == 2 && !memcmp(r1->el, L.el, 2 * sizeof(E)) && r1->fvf == D3DFVF_XYZRHW);
    CHECK_HR(d->GetVertexDeclaration(&g2), D3D_OK); CHECK(g2 == g1);                                                                                  // converted decl shared (Wine test_fvf_decl_management)
    CHECK_HR(d->SetFVF(D3DFVF_NORMAL), D3D_OK); CHECK_HR(d->GetVertexDeclaration(&g3), D3D_OK); CHECK(g3 != g1);
    CHECK_HR(d->SetFVF(D3DFVF_XYZRHW), D3D_OK); CHECK_HR(d->GetVertexDeclaration(&cur), D3D_OK); CHECK(cur == g1);                                  // one declaration per distinct FVF
    CHECK_EQ(nx_obj_of(g1)->pub, 3); g1->Release(); g1->Release(); g1->Release(); CHECK_EQ(nx_obj_of(g1)->pub, 0); g3->Release(); cur = nullptr;      // implicit declarations: public refs only from Get
    IDirect3DDevice9* gd = nullptr; CHECK_HR(g1->GetDevice(&gd), D3D_OK); CHECK(gd == static_cast<IDirect3DDevice9*>(d)); gd->Release();
    // SetFVF(0): no change, D3D_OK (Wine); invalid FVFs create no declaration
    CHECK_HR(d->SetFVF(0), D3D_OK); CHECK_HR(d->GetFVF(&fvf), D3D_OK); CHECK_EQ(fvf, D3DFVF_XYZRHW);
    CHECK(nx_device_fvf_decl(env.core, 0) == nullptr && nx_device_fvf_decl(env.core, 1) == nullptr && nx_device_fvf_decl(env.core, 0x400E | 0x4000) == nullptr && nx_device_fvf_decl(env.core, 9 << 8) == nullptr);
    HRESULT hr = d->SetFVF(0x6000); CHECK(hr != D3D_OK); CHECK_HR(d->GetFVF(&fvf), D3D_OK); CHECK_EQ(fvf, D3DFVF_XYZRHW); printf("SetFVF(invalid) = %08x (state_ext maps a null hook result to DRIVERINTERNALERROR)\n", (unsigned)hr);
    // SetVertexDeclaration with an element declaration: GetFVF = nx_decl_to_fvf (Windows rule: XYZ / XYZRHW only), the object is returned as set
    E xyz[2] = {{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0}, D3DDECL_END()}, cl[2] = {{0, 0, D3DDECLTYPE_D3DCOLOR, 0, D3DDECLUSAGE_COLOR, 0}, D3DDECL_END()}; IDirect3DVertexDeclaration9 *dx, *dc;
    d->CreateVertexDeclaration(xyz, &dx); d->CreateVertexDeclaration(cl, &dc);
    CHECK_HR(d->SetVertexDeclaration(dx), D3D_OK); CHECK_HR(d->GetFVF(&fvf), D3D_OK); CHECK_EQ(fvf, D3DFVF_XYZ); CHECK_HR(d->GetVertexDeclaration(&cur), D3D_OK); CHECK(cur == dx); cur->Release();
    CHECK_HR(d->SetFVF(D3DFVF_XYZ), D3D_OK); CHECK_HR(d->GetVertexDeclaration(&cur), D3D_OK); CHECK(cur != dx && cur != nullptr); cur->Release();   // SetFVF binds the implicit one, not the explicit equal one
    CHECK_HR(d->SetVertexDeclaration(dc), D3D_OK); CHECK_HR(d->GetFVF(&fvf), D3D_OK); CHECK_EQ(fvf, 0);
    CHECK_HR(d->SetFVF(D3DFVF_DIFFUSE | D3DFVF_SPECULAR), D3D_OK); CHECK_HR(d->SetVertexDeclaration(dc), D3D_OK); CHECK_HR(d->GetFVF(&fvf), D3D_OK); CHECK_EQ(fvf, 0);    // an element declaration resets the FVF (Wine test)
    CHECK_HR(d->SetFVF(D3DFVF_XYZ), D3D_OK); CHECK_HR(d->SetVertexDeclaration(nullptr), D3D_OK); CHECK_HR(d->GetFVF(&fvf), D3D_OK); CHECK_EQ(fvf, 0); CHECK_HR(d->GetVertexDeclaration(&cur), D3D_OK); CHECK(cur == nullptr);
    // the same FVF on two devices: two declarations; the cache is per device and dies with it (device destruction deletes the implicit declarations)
    TEnv e2 = t_env(); IDirect3DVertexDeclaration9 *a = nx_device_fvf_decl(env.core, D3DFVF_XYZ | D3DFVF_TEX1), *b = nx_device_fvf_decl(e2.core, D3DFVF_XYZ | D3DFVF_TEX1);
    CHECK(a && b && a != b && a == nx_device_fvf_decl(env.core, D3DFVF_XYZ | D3DFVF_TEX1));
    CHECK_EQ(e2.dev->Release(), 0);   // device gone with its cache
    dx->Release(); dc->Release();
}
