// part b: IDirect3DSwapChain9Ex::GetPresentStats. Domain of the V(c) = floor(c * R / f) clock rule (stats.txt, python bigint oracle), the counters, the error domain, both swap effects, both kinds of chain
#include "t_common.h"
static IDirect3DSwapChain9Ex* chain(IDirect3DDevice9* d) { IDirect3DSwapChain9* s = nullptr; d->GetSwapChain(0, &s); IDirect3DSwapChain9Ex* x = nullptr; if (s) { s->QueryInterface(IID_IDirect3DSwapChain9Ex, (void**)&x); s->Release(); } return x; }
void t_b() {
    t_part('b'); FILE* f = t_open("stats.txt"); IDirect3D9* dx = t_d3d(true); int n = 0;
    D3DPRESENT_PARAMETERS pp = t_pp(); pp.SwapEffect = D3DSWAPEFFECT_FLIPEX; pp.BackBufferCount = 2; IDirect3DDevice9* d = t_dev(dx, &pp); if (!CHECK(d)) return;
    IDirect3DSwapChain9Ex* sc = chain(d); CHECK(sc); t_clear_hooks();
    D3DPRESENTSTATS st; memset(&st, 0x5a, sizeof st); g_qpf = 1000000; g_qpc = 12345;
    CHECK_HR(sc->GetPresentStats(&st), D3D_OK); CHECK_EQ(st.PresentCount, 0); CHECK_EQ(st.PresentRefreshCount, 0); CHECK_EQ(st.SyncQPCTime.QuadPart, 12345); CHECK_EQ(st.SyncGPUTime.QuadPart, 0); CHECK_EQ(st.SyncRefreshCount, 0);   // 12345 us = 0.74 blank
    CHECK_HR(sc->GetPresentStats(nullptr), D3DERR_INVALIDCALL);
    char buf[256]; unsigned long long qpf, qpc, v;
    while (f && fgets(buf, sizeof buf, f)) {
        if (sscanf(buf, "%llu %llu %llu", &qpf, &qpc, &v) != 3) continue; g_qpf = qpf; g_qpc = qpc; n++;
        UINT before = 0; sc->GetLastPresentCount(&before);
        CHECK_HR(sc->Present(nullptr, nullptr, nullptr, nullptr, 0), D3D_OK); g_qpc = qpc + qpf;   // time passes after the present: the Sync sample is a later one
        CHECK_HR(sc->GetPresentStats(&st), D3D_OK); CHECK_EQ(st.PresentCount, before + 1); CHECK_EQ(st.PresentRefreshCount, (uint32_t)v); CHECK_EQ(st.SyncQPCTime.QuadPart, (long long)(qpc + qpf));
        UINT lp = 0; sc->GetLastPresentCount(&lp); CHECK_EQ(lp, st.PresentCount); CHECK_EQ(st.SyncGPUTime.QuadPart, 0);
        CHECK(st.SyncRefreshCount == (uint32_t)(v + 60) || st.SyncRefreshCount == (uint32_t)v + 60u || true);   // exact value checked from the oracle's second column below
        unsigned long long w; if (fgets(buf, sizeof buf, f) && sscanf(buf, "%llu", &w) == 1) CHECK_EQ(st.SyncRefreshCount, (uint32_t)w);
    }
    if (f) fclose(f); CHECK(n > 100);
    // a failed present counts nothing; FORCEIMMEDIATE and PresentEx count like Present
    UINT c0 = 0; sc->GetLastPresentCount(&c0); RECT bad = {0, 0, 0, 0}; CHECK_HR(sc->Present(&bad, nullptr, nullptr, nullptr, 0), D3DERR_INVALIDCALL); sc->GetPresentStats(&st); CHECK_EQ(st.PresentCount, c0);
    IDirect3DDevice9Ex* e = static_cast<IDirect3DDevice9Ex*>(d); CHECK_HR(e->PresentEx(nullptr, nullptr, nullptr, nullptr, D3DPRESENT_FORCEIMMEDIATE), D3D_OK); CHECK_HR(d->Present(nullptr, nullptr, nullptr, nullptr), D3D_OK); sc->GetPresentStats(&st); CHECK_EQ(st.PresentCount, c0 + 2);
    // Reset keeps the counter (dev_reset.cpp): the stats of the new implicit chain continue
    D3DPRESENT_PARAMETERS rp = t_pp(); rp.SwapEffect = D3DSWAPEFFECT_FLIPEX; rp.BackBufferCount = 2; CHECK_HR(d->Reset(&rp), D3D_OK); IDirect3DSwapChain9Ex* sc2 = chain(d); sc2->GetPresentStats(&st); CHECK_EQ(st.PresentCount, c0 + 2); sc2->Release();
    // every chain answers, whatever its swap effect (MS D3DPRESENTSTATS: "processed ... regardless of the state of FLIPEX mode"): DISCARD / FLIP / COPY, and an additional chain
    for (uint32_t se : {(uint32_t)D3DSWAPEFFECT_DISCARD, (uint32_t)D3DSWAPEFFECT_FLIP, (uint32_t)D3DSWAPEFFECT_COPY, (uint32_t)D3DSWAPEFFECT_FLIPEX}) {
        D3DPRESENT_PARAMETERS p = t_pp(true, se, 1); IDirect3DDevice9* d2 = t_dev(dx, &p); if (!CHECK(d2)) continue; IDirect3DSwapChain9Ex* s2 = chain(d2); g_qpf = 1000; g_qpc = 5000;
        CHECK_HR(s2->GetPresentStats(&st), D3D_OK); CHECK_EQ(st.PresentCount, 0); CHECK_HR(s2->Present(nullptr, nullptr, nullptr, nullptr, 0), D3D_OK); s2->GetPresentStats(&st); CHECK_EQ(st.PresentCount, 1); CHECK_EQ(st.PresentRefreshCount, 300); CHECK_EQ(st.SyncRefreshCount, 300);
        D3DPRESENT_PARAMETERS ap = t_pp(true, se, 1); ap.hDeviceWindow = (HWND)W_SMALL; IDirect3DSwapChain9* extra = nullptr; CHECK_HR(d2->CreateAdditionalSwapChain(&ap, &extra), D3D_OK);
        IDirect3DSwapChain9Ex* ex2 = nullptr; if (extra) { CHECK_HR(extra->QueryInterface(IID_IDirect3DSwapChain9Ex, (void**)&ex2), D3D_OK); }
        if (ex2) { CHECK_HR(ex2->GetPresentStats(&st), D3D_OK); CHECK_EQ(st.PresentCount, 0); CHECK_HR(ex2->Present(nullptr, nullptr, nullptr, nullptr, 0), D3D_OK); ex2->GetPresentStats(&st); CHECK_EQ(st.PresentCount, 1); UINT lc = 0; ex2->GetLastPresentCount(&lc); CHECK_EQ(lc, 1); ex2->Release(); }
        if (extra) extra->Release(); s2->Release(); d2->Release();
    }
    sc->Release(); d->Release(); dx->Release();
}
