// part e: volumes (LockBox / dirty boxes / levels) and cube faces (dirty per face)
#include "t_common.h"
static NxObj* O(IUnknown* p) { return nx_obj_of(p); }
static const Rec* U(int i) { int n = 0; for (int k = 0; k < g_nrec; k++) if (g_rec[k].op == R_UPDATE && n++ == i) return &g_rec[k]; return nullptr; }
void t_e() {
    t_part('e'); g_profile = 1; TEnv e = t_env(); NxDevice9* d = e.dev; IDirect3DVolumeTexture9* v; D3DLOCKED_BOX lb;
    CHECK_HR(d->CreateVolumeTexture(8, 8, 4, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &v, nullptr), D3D_OK); CHECK_EQ(v->GetLevelCount(), 4); CHECK_EQ(O(v)->pub, 1); CHECK_EQ(e.obj->pub, 2); CHECK_EQ(O(v)->getType(), D3DRTYPE_VOLUMETEXTURE);
    D3DVOLUME_DESC vd; CHECK_HR(v->GetLevelDesc(1, &vd), D3D_OK); CHECK(vd.Width == 4 && vd.Height == 4 && vd.Depth == 2 && vd.Pool == D3DPOOL_MANAGED && vd.Type == D3DRTYPE_VOLUME && vd.Format == D3DFMT_A8R8G8B8); CHECK_HR(v->GetLevelDesc(4, &vd), D3DERR_INVALIDCALL);
    IDirect3DVolume9 *l0 = nullptr, *l0b = nullptr, *l3 = (IDirect3DVolume9*)9; CHECK_HR(v->GetVolumeLevel(0, &l0), D3D_OK); CHECK_HR(v->GetVolumeLevel(0, &l0b), D3D_OK); CHECK(l0 == l0b); CHECK_EQ(O(v)->pub, 3); CHECK_EQ(e.obj->pub, 2); CHECK_HR(v->GetVolumeLevel(4, &l3), D3DERR_INVALIDCALL); CHECK(l3 == (IDirect3DVolume9*)9);
    CHECK_HR(l0->GetDesc(&vd), D3D_OK); CHECK(vd.Width == 8 && vd.Depth == 4 && vd.Usage == 0); void* cont; CHECK_HR(l0->GetContainer(IID_IDirect3DVolumeTexture9, &cont), D3D_OK); CHECK(cont == (void*)v); CHECK_EQ(O(v)->pub, 4); ((IUnknown*)cont)->Release();
    IDirect3DDevice9* gd; CHECK_HR(l0->GetDevice(&gd), D3D_OK); gd->Release(); CHECK_EQ(l0b->Release(), 2); CHECK_EQ(l0->Release(), 1);
    O(v)->preLoad(); CHECK_HR(v->LockBox(0, &lb, nullptr, D3DLOCK_READONLY), D3D_OK); CHECK(lb.RowPitch == 32 && lb.SlicePitch == 256); uint8_t* base = (uint8_t*)lb.pBits; CHECK_HR(v->LockBox(0, &lb, nullptr, 0), D3DERR_INVALIDCALL); CHECK_HR(v->UnlockBox(0), D3D_OK); CHECK_HR(v->UnlockBox(0), D3DERR_INVALIDCALL);
    D3DBOX ok = {1, 2, 3, 4, 1, 3};   // l t r b f k
    CHECK_HR(v->LockBox(0, &lb, &ok, 0), D3D_OK); CHECK_EQ((uint8_t*)lb.pBits - base, 1 * 256 + 2 * 32 + 1 * 4); *(uint8_t*)lb.pBits = 0x66; v->UnlockBox(0);
    static const D3DBOX bad[] = {{0, 0, 0, 4, 0, 4}, {4, 0, 4, 4, 0, 4}, {0, 4, 4, 4, 0, 4}, {0, 0, 4, 4, 2, 2}, {0, 0, 9, 4, 0, 4}, {0, 0, 4, 9, 0, 4}, {0, 0, 4, 4, 0, 5}, {5, 0, 3, 4, 0, 4}, {0, 0, 4, 4, 3, 1}};
    for (auto& b : bad) { CHECK_HR(v->LockBox(0, &lb, &b, 0), D3DERR_INVALIDCALL); CHECK(lb.pBits == nullptr); }
    CHECK_HR(v->LockBox(0, nullptr, nullptr, 0), D3DERR_INVALIDCALL);
    t_clear(); O(v)->preLoad(); CHECK_EQ(t_count(R_UPDATE), 1); const Rec* r = U(0); CHECK(r && r->level == 0 && r->layer == 0 && r->box.l == 1 && r->box.t == 2 && r->box.f == 1 && r->box.r == 3 && r->box.b == 4 && r->box.k == 3 && r->row == 32 && r->slice == 256 && r->b0 == 0x66);
    D3DBOX a1 = {0, 0, 2, 2, 0, 1}, a2 = {6, 6, 8, 8, 2, 4}, bb = {0, 0, 9, 8, 0, 4}; CHECK_HR(v->AddDirtyBox(&a1), D3D_OK); CHECK_HR(v->AddDirtyBox(&a2), D3D_OK); CHECK_HR(v->AddDirtyBox(&bb), D3DERR_INVALIDCALL);
    t_clear(); O(v)->preLoad(); r = U(0); CHECK(t_count(R_UPDATE) == 1 && r && r->box.l == 0 && r->box.t == 0 && r->box.f == 0 && r->box.r == 8 && r->box.b == 8 && r->box.k == 4);   // union over x, y and z
    CHECK_HR(v->AddDirtyBox(nullptr), D3D_OK); t_clear(); O(v)->preLoad(); CHECK_EQ(t_count(R_UPDATE), 1); CHECK_EQ(O(v)->release(), 0);
    // block formats in volumes: DXT1 4x4x2 (Wine test_volume_blocks: scratch volumes enforce block locks)
    CHECK_HR(d->CreateVolumeTexture(8, 8, 2, 1, 0, (D3DFORMAT)MAKEFOURCC('D', 'X', 'T', '1'), D3DPOOL_SCRATCH, &v, nullptr), D3D_OK); CHECK_HR(v->LockBox(0, &lb, nullptr, 0), D3D_OK); CHECK(lb.RowPitch == 16 && lb.SlicePitch == 32); base = (uint8_t*)lb.pBits; v->UnlockBox(0);
    D3DBOX g1 = {4, 4, 8, 8, 1, 2}, g2 = {1, 0, 4, 4, 0, 1}; CHECK_HR(v->LockBox(0, &lb, &g1, 0), D3D_OK); CHECK_EQ((uint8_t*)lb.pBits - base, 32 + 16 + 8); v->UnlockBox(0); CHECK_HR(v->LockBox(0, &lb, &g2, 0), D3DERR_INVALIDCALL); O(v)->release();
    CHECK_HR(d->CreateVolumeTexture(6, 8, 2, 1, 0, (D3DFORMAT)MAKEFOURCC('D', 'X', 'T', '1'), D3DPOOL_SCRATCH, &v, nullptr), D3DERR_INVALIDCALL);       // DXTn base size must be block aligned
    CHECK_HR(d->CreateVolumeTexture(4, 4, 4, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &v, nullptr), D3DERR_INVALIDCALL);
    // cubes: dirty per face
    IDirect3DCubeTexture9* c; CHECK_HR(d->CreateCubeTexture(8, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &c, nullptr), D3D_OK); t_clear(); O(c)->preLoad(); CHECK_EQ(t_count(R_UPDATE), 6);
    for (int i = 0; i < 6; i++) { r = U(i); CHECK(r && r->layer == (uint32_t)i && r->box.r == 8); }
    RECT ra = {1, 1, 3, 3}, rb = {2, 2, 9, 9}; CHECK_HR(c->AddDirtyRect(D3DCUBEMAP_FACE_NEGATIVE_Y, &ra), D3D_OK); CHECK_HR(c->AddDirtyRect((D3DCUBEMAP_FACES)6, &ra), D3DERR_INVALIDCALL); CHECK_HR(c->AddDirtyRect(D3DCUBEMAP_FACE_NEGATIVE_Y, &rb), D3DERR_INVALIDCALL);
    t_clear(); O(c)->preLoad(); r = U(0); CHECK(t_count(R_UPDATE) == 1 && r->layer == 3 && r->box.l == 1 && r->box.r == 3);
    D3DLOCKED_RECT a; c->LockRect(D3DCUBEMAP_FACE_POSITIVE_Z, 0, &a, &ra, 0); c->UnlockRect(D3DCUBEMAP_FACE_POSITIVE_Z, 0); t_clear(); O(c)->preLoad(); r = U(0); CHECK(t_count(R_UPDATE) == 1 && r->layer == 4); O(c)->release(); CHECK_EQ(e.obj->pub, 1);
}
