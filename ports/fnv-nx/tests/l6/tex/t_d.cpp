// part d: AUTOGENMIPMAP, GenerateMipSubLevels, SetAutoGenFilterType (python oracle for which formats generate)
#include "t_common.h"
static NxObj* O(IUnknown* p) { return nx_obj_of(p); }
void t_d() {
    t_part('d'); g_profile = 0; TEnv e = t_env(); NxDevice9* d = e.dev; char line[256];
    FILE* f = t_open("ag.txt");                          // G fmt genok
    while (f && fgets(line, sizeof line, f)) {
        unsigned fmt, ok; if (sscanf(line, "G %u %u", &fmt, &ok) != 2) { CHECK(false); continue; }
        IDirect3DTexture9* t; t_clear(); if (!CHECK_HR(d->CreateTexture(16, 16, 0, D3DUSAGE_AUTOGENMIPMAP, (D3DFORMAT)fmt, D3DPOOL_MANAGED, &t, nullptr), D3D_OK)) continue;
        CHECK_EQ(t->GetLevelCount(), 1); CHECK_EQ(t_count(R_CREATE), 1); if (t_count(R_CREATE)) CHECK_EQ(g_rec[0].desc.mips, ok ? 5 : 1); O(t)->release();   // full GPU chain iff the env says GENMIPMAP is generatable (not NOAUTOGEN)
    }
    if (f) fclose(f);
    IDirect3DTexture9* t; D3DLOCKED_RECT a; D3DSURFACE_DESC sd; IDirect3DSurface9* s = (IDirect3DSurface9*)3;
    d->CreateTexture(16, 16, 0, D3DUSAGE_AUTOGENMIPMAP, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr); CHECK_EQ(t->GetLevelCount(), 1);
    CHECK_HR(t->GetLevelDesc(1, &sd), D3DERR_INVALIDCALL); CHECK_HR(t->GetSurfaceLevel(1, &s), D3DERR_INVALIDCALL); CHECK(s == (IDirect3DSurface9*)3); CHECK_HR(t->LockRect(1, &a, nullptr, 0), D3DERR_INVALIDCALL); CHECK_HR(t->UnlockRect(1), D3DERR_INVALIDCALL);
    CHECK_HR(t->GetLevelDesc(0, &sd), D3D_OK); CHECK_EQ(sd.Usage, D3DUSAGE_AUTOGENMIPMAP); t->GetSurfaceLevel(0, &s); s->GetDesc(&sd); CHECK_EQ(sd.Usage, 0); s->Release();   // d3d9_surface_GetDesc: usage & WINED3DUSAGE_MASK
    CHECK_EQ(t->GetAutoGenFilterType(), D3DTEXF_LINEAR); CHECK_HR(t->SetAutoGenFilterType(D3DTEXF_NONE), D3DERR_INVALIDCALL); CHECK_EQ(t->GetAutoGenFilterType(), D3DTEXF_LINEAR); CHECK_HR(t->SetAutoGenFilterType(D3DTEXF_POINT), D3D_OK); CHECK_EQ(t->GetAutoGenFilterType(), D3DTEXF_POINT);
    CHECK_HR(t->SetAutoGenFilterType(D3DTEXF_LINEAR), D3D_OK); O(t)->preLoad();
    t_clear(); t->GenerateMipSubLevels(); CHECK_EQ(g_nrec, 0);                                                                  // nothing changed: no generation
    t->LockRect(0, &a, nullptr, 0); t->UnlockRect(0); t_clear(); t->GenerateMipSubLevels(); CHECK_EQ(t_count(R_UPDATE), 1); CHECK_EQ(t_count(R_GEN), 1); CHECK(g_rec[g_nrec - 1].op == R_GEN && g_rec[g_nrec - 1].row == 1);   // upload, then generate (linear)
    t_clear(); t->GenerateMipSubLevels(); CHECK_EQ(g_nrec, 0);
    t->LockRect(0, &a, nullptr, D3DLOCK_READONLY); t->UnlockRect(0); t->SetAutoGenFilterType(D3DTEXF_POINT); t_clear(); t->GenerateMipSubLevels(); CHECK_EQ(t_count(R_GEN), 1); CHECK_EQ(g_rec[g_nrec - 1].row, 0);   // any successful unlock marks the chain; POINT -> 0
    O(t)->release();
    d->CreateTexture(16, 16, 1, D3DUSAGE_AUTOGENMIPMAP, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &t, nullptr); NxImg* im = static_cast<NxTexCore*>(O(t))->img; nx_img_gpu_wrote(im, 0, 0); t_clear(); t->GenerateMipSubLevels(); CHECK_EQ(t_count(R_GEN), 1); O(t)->release();   // rendered into: regenerate
    CHECK_HR(d->CreateTexture(16, 16, 2, D3DUSAGE_AUTOGENMIPMAP, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr), D3DERR_INVALIDCALL); CHECK_HR(d->CreateTexture(16, 16, 1, D3DUSAGE_AUTOGENMIPMAP, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &t, nullptr), D3DERR_INVALIDCALL);
    // plain texture: the filter is only stored, GenerateMipSubLevels does nothing
    d->CreateTexture(16, 16, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr); CHECK_EQ(t->GetAutoGenFilterType(), D3DTEXF_NONE); CHECK_HR(t->SetAutoGenFilterType(D3DTEXF_LINEAR), D3D_OK); CHECK_EQ(t->GetAutoGenFilterType(), D3DTEXF_LINEAR);
    O(t)->preLoad(); t->LockRect(0, &a, nullptr, 0); t->UnlockRect(0); t_clear(); t->GenerateMipSubLevels(); CHECK_EQ(g_nrec, 0); O(t)->release();
    IDirect3DCubeTexture9* c; CHECK_HR(d->CreateCubeTexture(8, 0, D3DUSAGE_AUTOGENMIPMAP, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &c, nullptr), D3D_OK); CHECK_EQ(c->GetLevelCount(), 1); CHECK_HR(c->GetCubeMapSurface(D3DCUBEMAP_FACE_NEGATIVE_Z, 1, &s), D3DERR_INVALIDCALL);
    O(c)->preLoad(); c->LockRect(D3DCUBEMAP_FACE_NEGATIVE_Z, 0, &a, nullptr, 0); c->UnlockRect(D3DCUBEMAP_FACE_NEGATIVE_Z, 0); t_clear(); c->GenerateMipSubLevels(); CHECK_EQ(t_count(R_GEN), 1); CHECK_EQ(t_count(R_UPDATE), 1); CHECK_EQ(g_rec[0].layer, 5); O(c)->release();
    IDirect3DVolumeTexture9* v; d->CreateVolumeTexture(4, 4, 4, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &v, nullptr); CHECK_HR(v->SetAutoGenFilterType(D3DTEXF_LINEAR), D3DERR_INVALIDCALL); CHECK_EQ(v->GetAutoGenFilterType(), D3DTEXF_NONE); t_clear(); v->GenerateMipSubLevels(); CHECK_EQ(g_nrec, 0); O(v)->release();
    CHECK_HR(d->CreateVolumeTexture(4, 4, 4, 1, D3DUSAGE_AUTOGENMIPMAP, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &v, nullptr), D3DERR_INVALIDCALL);
}
