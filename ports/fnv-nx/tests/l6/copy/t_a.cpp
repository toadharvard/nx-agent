// part a: legality of every method over its parameter domain against the python oracle (cases.py): zoo.txt creates the objects, ops.txt lists every call with the expected HRESULT.
#include "t_common.h"
static IDirect3DSurface9* g_z[256]; static IDirect3DBaseTexture9* g_t[16]; static int g_nz = 0;
static bool rect(char** p, RECT* r) {   // "-" or four ints
    while (**p == ' ') (*p)++; if (**p == '-' && ((*p)[1] == ' ' || (*p)[1] == 0)) { (*p)++; return false; }
    r->left = strtol(*p, p, 10); r->top = strtol(*p, p, 10); r->right = strtol(*p, p, 10); r->bottom = strtol(*p, p, 10); return true;
}
static bool point(char** p, POINT* q) { while (**p == ' ') (*p)++; if (**p == '-' && ((*p)[1] == ' ' || (*p)[1] == 0)) { (*p)++; return false; } q->x = strtol(*p, p, 10); q->y = strtol(*p, p, 10); return true; }
static void zoo(TDev& d) {
    FILE* f = t_open("zoo.txt"); char line[256]; unsigned id, fmt, w, h, pool, rt, ds, ms, locked, tx, lv; int tex; char kind[8];
    while (f && fgets(line, sizeof line, f)) {
        if (line[0] == 'T') {
            unsigned ti, fm, ww, hh, lvs, pl, r; sscanf(line + 2, "%u %u %u %u %u %u %u", &ti, &fm, &ww, &hh, &lvs, &pl, &r); IDirect3DTexture9* t = nullptr;
            HRESULT hr = d.i9->CreateTexture(ww, hh, lvs, r ? D3DUSAGE_RENDERTARGET : 0, (D3DFORMAT)fm, (D3DPOOL)pl, &t, nullptr); CHECK_HR(hr, D3D_OK); g_t[ti] = t; continue;
        }
        if (sscanf(line + 2, "%u %7s %u %u %u %u %u %u %u %u %d %u", &id, kind, &fmt, &w, &h, &pool, &rt, &ds, &ms, &locked, &tex, &lv) != 12) { CHECK(false); continue; }
        IDirect3DSurface9* s = nullptr;
        if (kind[0] == 't') { IDirect3DTexture9* t = (IDirect3DTexture9*)g_t[tex]; if (t) t->GetSurfaceLevel(lv, &s); }
        else if (kind[0] == 'p') s = t_surf(d, 2, w, h, fmt, (D3DPOOL)pool);
        else if (kind[0] == 'r') s = t_surf(d, 0, w, h, fmt, D3DPOOL_DEFAULT, ms, ms == 0 && id != 23);
        else s = t_surf(d, 1, w, h, fmt);
        CHECK(s != nullptr); g_z[id] = s; if (id >= (unsigned)g_nz) g_nz = id + 1;
        if (locked && s) { D3DLOCKED_RECT lr; CHECK_HR(s->LockRect(&lr, nullptr, 0), D3D_OK); }
    }
    if (f) fclose(f);
}
static void ops(TDev& d, bool ex) {
    FILE* f = t_open("ops.txt"); static char line[512]; unsigned a, b, c, e; RECT r1, r2; POINT pt; int n = 0;
    while (f && fgets(line, sizeof line, f)) {
        char* p = line + 3; HRESULT hr, want; char op[4] = {line[0], line[1], line[2] == ' ' ? 0 : line[2], 0}; if (op[2]) p = line + 4;
        if (!strcmp(op, "SR") || !strcmp(op, "SRS")) {
            bool sc = op[2] == 'S'; unsigned fl = 1; bool hs = false, hd = false; char* q = p;
            a = strtoul(q, &q, 10);
            if (!sc) { hs = rect(&q, &r1); b = strtoul(q, &q, 10); hd = rect(&q, &r2); fl = strtoul(q, &q, 10); } else b = strtoul(q, &q, 10);
            want = (HRESULT)strtoul(q, &q, 16); if (sc) t_scene(d, true);
            hr = d.i9->StretchRect(g_z[a], hs ? &r1 : nullptr, g_z[b], hd ? &r2 : nullptr, (D3DTEXTUREFILTERTYPE)fl); if (sc) t_scene(d, false);
        } else if (!strcmp(op, "US")) { char* q = p; a = strtoul(q, &q, 10); bool hs = rect(&q, &r1); b = strtoul(q, &q, 10); bool hp = point(&q, &pt); want = (HRESULT)strtoul(q, &q, 16); hr = d.i9->UpdateSurface(g_z[a], hs ? &r1 : nullptr, g_z[b], hp ? &pt : nullptr); }
        else if (!strcmp(op, "RD")) { char* q = p; a = strtoul(q, &q, 10); b = strtoul(q, &q, 10); want = (HRESULT)strtoul(q, &q, 16); hr = d.i9->GetRenderTargetData(g_z[a], g_z[b]); }
        else if (!strcmp(op, "CF")) { char* q = p; a = strtoul(q, &q, 10); bool hs = rect(&q, &r1); want = (HRESULT)strtoul(q, &q, 16); hr = d.i9->ColorFill(g_z[a], hs ? &r1 : nullptr, 0x80402010); }
        else if (!strcmp(op, "UT")) { char* q = p; a = strtoul(q, &q, 10); b = strtoul(q, &q, 10); want = (HRESULT)strtoul(q, &q, 16); hr = d.i9->UpdateTexture(g_t[a], g_t[b]); }
        else if (!strcmp(op, "GD")) {
            char* q = p; a = strtoul(q, &q, 10); want = (HRESULT)strtoul(q, &q, 16); HDC dc = nullptr; hr = g_z[a]->GetDC(&dc);
            if (hr == D3D_OK) { CHECK_HR(g_z[a]->GetDC(&dc), D3DERR_INVALIDCALL); CHECK_HR(g_z[a]->ReleaseDC((HDC)0x1234), D3DERR_INVALIDCALL); HDC h2 = nullptr; (void)h2;
                D3DLOCKED_RECT lr; CHECK_HR(g_z[a]->LockRect(&lr, nullptr, 0), D3DERR_INVALIDCALL); }
            if (hr == D3D_OK) { HDC h = nullptr; g_z[a]->GetDC(&h); } if (hr == D3D_OK) { CHECK_HR(g_z[a]->ReleaseDC(dc), D3D_OK); CHECK_HR(g_z[a]->ReleaseDC(dc), D3DERR_INVALIDCALL); }
            else CHECK_HR(g_z[a]->ReleaseDC((HDC)0), D3DERR_INVALIDCALL);
        } else if (!strcmp(op, "CL")) {
            char* q = p; unsigned rtid = strtoul(q, &q, 10); int dsid = strtol(q, &q, 10); unsigned fl = strtoul(q, &q, 10), nr = strtoul(q, &q, 10), hasr = strtoul(q, &q, 10); want = (HRESULT)strtoul(q, &q, 16);
            d.i9->SetRenderTarget(0, g_z[rtid]); d.i9->SetDepthStencilSurface(dsid < 0 ? nullptr : g_z[dsid]); D3DRECT rc[2] = {{4, 4, 12, 12}, {8, 8, 40, 40}};
            hr = d.i9->Clear(nr, hasr ? rc : nullptr, fl, 0xff102030, 1.0f, 0);
        } else if (!strcmp(op, "VD")) {
            char* q = p; unsigned minf = strtoul(q, &q, 10), magf = strtoul(q, &q, 10), mipf = strtoul(q, &q, 10), tx = strtoul(q, &q, 10), z = strtoul(q, &q, 10), zw = strtoul(q, &q, 10), st = strtoul(q, &q, 10), dsid = strtoul(q, &q, 10);
            want = (HRESULT)strtoul(q, &q, 16); static IDirect3DTexture9* tx1 = nullptr; static IDirect3DTexture9* tx2 = nullptr;
            if (!tx1) { d.i9->CreateTexture(8, 8, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &tx1, nullptr); d.i9->CreateTexture(8, 8, 1, 0, (D3DFORMAT)116, D3DPOOL_DEFAULT, &tx2, nullptr); }
            d.i9->SetTexture(0, tx == 1 ? (IDirect3DBaseTexture9*)tx1 : tx == 2 ? (IDirect3DBaseTexture9*)tx2 : nullptr);
            d.i9->SetSamplerState(0, D3DSAMP_MINFILTER, minf); d.i9->SetSamplerState(0, D3DSAMP_MAGFILTER, magf); d.i9->SetSamplerState(0, D3DSAMP_MIPFILTER, mipf);
            d.i9->SetRenderState(D3DRS_ZENABLE, z); d.i9->SetRenderState(D3DRS_ZWRITEENABLE, zw); d.i9->SetRenderState(D3DRS_STENCILENABLE, st);
            d.i9->SetRenderTarget(0, g_z[15]); d.i9->SetDepthStencilSurface(g_z[dsid]); DWORD np = 0; hr = d.i9->ValidateDevice(&np); if (hr == D3D_OK) CHECK_EQ(np, 1);
            d.i9->SetRenderState(D3DRS_ZENABLE, 0); d.i9->SetRenderState(D3DRS_ZWRITEENABLE, 0); d.i9->SetRenderState(D3DRS_STENCILENABLE, 0);
        } else if (!strcmp(op, "PT")) {
            char* q = p; a = strtoul(q, &q, 10); want = (HRESULT)strtoul(q, &q, 16); float seg[4] = {1, 1, 1, 1}; D3DRECTPATCH_INFO ri = {}; D3DTRIPATCH_INFO ti = {};
            hr = a == 0 ? d.i9->DrawRectPatch(1, seg, &ri) : a == 1 ? d.i9->DrawTriPatch(1, seg, &ti) : d.i9->DeletePatch(1);
        } else if (!strcmp(op, "CE")) {
            char* q = p; unsigned w = strtoul(line + 3, &q, 10); unsigned h = strtoul(q, &q, 10), fmt = strtoul(q, &q, 10), pool = strtoul(q, &q, 10), usage = strtoul(q, &q, 10);
            IDirect3DSurface9* s1 = nullptr; IDirect3DSurface9* s2 = nullptr; HRESULT h1 = d.i9->CreateOffscreenPlainSurface(w, h, (D3DFORMAT)fmt, (D3DPOOL)pool, &s1, nullptr);
            if (!ex) { CHECK(true); if (s1) s1->Release(); continue; }
            HRESULT h2 = d.devex->CreateOffscreenPlainSurfaceEx(w, h, (D3DFORMAT)fmt, (D3DPOOL)pool, &s2, nullptr, usage);
            CHECK_HR(h2, (usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL)) ? D3DERR_INVALIDCALL : h1);   // shares the validation of CreateOffscreenPlainSurface
            CHECK((h2 == D3D_OK) == (s2 != nullptr)); if (s2) { D3DSURFACE_DESC dd; s2->GetDesc(&dd); CHECK(dd.Format == (D3DFORMAT)fmt && dd.Pool == (D3DPOOL)pool && dd.Width == w); s2->Release(); } if (s1) s1->Release(); continue;
        } else { CHECK(false); continue; }
        if (!t_check(hr == want, "legality", "ops.txt", n) ) { static int shown = 0; if (shown++ < 8) printf("  case %d: %.*s -> %08x want %08x\n", n, (int)strcspn(line, "\n"), line, (unsigned)hr, (unsigned)want); }
        n++;
    }
    if (f) fclose(f);
}
void t_a() {
    t_part('a');
    for (int ex = 0; ex < 2; ex++) {
        for (int i = 0; i < 256; i++) g_z[i] = nullptr; for (int i = 0; i < 16; i++) g_t[i] = nullptr; g_nz = 0;
        TDev d = t_dev(ex != 0, 64, 64); zoo(d); ops(d, ex != 0);
    }
}
