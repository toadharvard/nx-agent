// part d: SetConvolutionMonoKernel: validation (kern.txt rows from the python oracle: width / height 1..7, rows / columns NULL), stored state, state blocks (D3DSBT_PIXELSTATE / ALL record it, VERTEXSTATE not), Reset
#include "t_common.h"
static uint32_t kw(IDirect3DDevice9* d) { return static_cast<NxDeviceCore*>(static_cast<NxDevice9*>(d))->cur.convk[0]; }
static uint32_t kh(IDirect3DDevice9* d) { return static_cast<NxDeviceCore*>(static_cast<NxDevice9*>(d))->cur.convk[1]; }
void t_d() {
    t_part('d'); g_profile = 1; IDirect3D9* dx = t_d3d(true); D3DPRESENT_PARAMETERS pp = t_pp(); IDirect3DDevice9Ex* e = static_cast<IDirect3DDevice9Ex*>(t_dev(dx, &pp)); if (!CHECK(e)) return; IDirect3DDevice9* d = e;
    CHECK_EQ(kw(d), 1); CHECK_EQ(kh(d), 1);                                                                      // MS: "The default value is 1"
    FILE* f = t_open("kern.txt"); char buf[256]; int n = 0; static float arr[8] = {1, 1, 1, 1, 1, 1, 1, 1};
    while (f && fgets(buf, sizeof buf, f)) {
        unsigned w, h, r, c, exp; if (sscanf(buf, "%u %u %u %u %x", &w, &h, &r, &c, &exp) != 5) continue; n++;
        uint32_t w0 = kw(d), h0 = kh(d); HRESULT hr = e->SetConvolutionMonoKernel(w, h, r ? arr : nullptr, c ? arr : nullptr); CHECK_HR(hr, exp);
        if (hr == D3D_OK) { CHECK_EQ(kw(d), w); CHECK_EQ(kh(d), h); } else { CHECK_EQ(kw(d), w0); CHECK_EQ(kh(d), h0); }   // a refused call changes nothing
    }
    if (f) fclose(f); CHECK(n > 100);
    // state blocks
    CHECK_HR(e->SetConvolutionMonoKernel(2, 3, nullptr, nullptr), D3D_OK); IDirect3DStateBlock9 *sp = nullptr, *sa = nullptr, *sv = nullptr;
    CHECK_HR(d->CreateStateBlock(D3DSBT_PIXELSTATE, &sp), D3D_OK); CHECK_HR(d->CreateStateBlock(D3DSBT_ALL, &sa), D3D_OK); CHECK_HR(d->CreateStateBlock(D3DSBT_VERTEXSTATE, &sv), D3D_OK);
    e->SetConvolutionMonoKernel(4, 5, nullptr, nullptr);
    sv->Apply(); CHECK_EQ(kw(d), 4); CHECK_EQ(kh(d), 5); sp->Apply(); CHECK_EQ(kw(d), 2); CHECK_EQ(kh(d), 3); e->SetConvolutionMonoKernel(6, 7, nullptr, nullptr); sa->Apply(); CHECK_EQ(kw(d), 2); CHECK_EQ(kh(d), 3);
    e->SetConvolutionMonoKernel(4, 4, nullptr, nullptr); sp->Capture(); e->SetConvolutionMonoKernel(1, 1, nullptr, nullptr); sp->Apply(); CHECK_EQ(kw(d), 4); CHECK_EQ(kh(d), 4);   // Capture re-reads the device
    // recording: the call goes to the block, the device keeps its kernel
    e->SetConvolutionMonoKernel(1, 1, nullptr, nullptr); CHECK_HR(d->BeginStateBlock(), D3D_OK); CHECK_HR(e->SetConvolutionMonoKernel(7, 6, nullptr, nullptr), D3D_OK); CHECK_EQ(kw(d), 1); CHECK_EQ(kh(d), 1);
    IDirect3DStateBlock9* rec = nullptr; CHECK_HR(d->EndStateBlock(&rec), D3D_OK); rec->Apply(); CHECK_EQ(kw(d), 7); CHECK_EQ(kh(d), 6); rec->Release();
    sp->Release(); sa->Release(); sv->Release();
    // Reset: non-Ex Reset resets all state (MS Reset: "all state information to be lost") to the default; Ex Reset keeps the device state (dev_reset.cpp)
    e->SetConvolutionMonoKernel(3, 3, nullptr, nullptr); D3DPRESENT_PARAMETERS rp = t_pp(); CHECK_HR(e->ResetEx(&rp, nullptr), D3D_OK); CHECK_EQ(kw(d), 3); CHECK_EQ(kh(d), 3);
    IDirect3D9* d9 = t_d3d(false); IDirect3DDevice9* n9 = t_dev(d9, &pp); NxDeviceCore* core = static_cast<NxDeviceCore*>(static_cast<NxDevice9*>(n9)); uint32_t five = 5; core->put(NX_S_convk, &five); CHECK_EQ(core->cur.convk[0], 5);
    CHECK_HR(n9->Reset(&rp), D3D_OK); CHECK_EQ(core->cur.convk[0], 1); CHECK_EQ(core->cur.convk[1], 1); n9->Release(); d9->Release();
    e->Release(); dx->Release();
}
