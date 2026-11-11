// part c: vertex layouts: declarations x vertex shader signatures x stream frequencies x bound streams against the python oracle (oracle.py write_c: location rule, unbound input rule, last element wins, instancing).
// Case lines: vs <hex> / el stream offset type usage index / freq s word / bound b0 b1 .. / draw 0|1 / imask m / div s n / attr offset stream type loc / zero s / end
#include "t_common.h"
void t_c() {
    t_part('c'); TDev d = t_dev(); t_target(d); d.i9->SetPixelShader(nullptr); IDirect3DVertexBuffer9* vb[4]; for (int i = 0; i < 4; i++) vb[i] = t_vb(d, 4096);
    FILE* f = t_open("c.txt"); char line[4096]; int n = 0;
    while (f && fgets(line, sizeof line, f)) {
        if (strncmp(line, "case", 4)) continue;
        std::vector<D3DVERTEXELEMENT9> el; std::vector<uint32_t> freq; int bound[4] = {0, 0, 0, 0}, draw = 0, zero = -1; uint32_t imask = 0, div[16] = {}; std::vector<NxVtxAttr4> attrs; std::vector<uint32_t> vs;
        while (fgets(line, sizeof line, f) && strncmp(line, "end", 3)) {
            int a, b, c, e, g; unsigned u;
            if (!strncmp(line, "vs ", 3)) vs = t_hex(line + 3);
            else if (sscanf(line, "el %d %d %d %d %d", &a, &b, &c, &e, &g) == 5) { D3DVERTEXELEMENT9 x = {(WORD)a, (WORD)b, (BYTE)c, 0, (BYTE)e, (BYTE)g}; el.push_back(x); }
            else if (sscanf(line, "freq %d %u", &a, &u) == 2) freq.push_back(u);
            else if (!strncmp(line, "bound", 5)) sscanf(line + 5, "%d %d %d %d", &bound[0], &bound[1], &bound[2], &bound[3]);
            else if (sscanf(line, "draw %d", &draw) == 1) {}
            else if (sscanf(line, "imask %u", &imask) == 1) {}
            else if (sscanf(line, "div %d %d", &a, &b) == 2) div[a] = b;
            else if (sscanf(line, "attr %d %d %d %d", &a, &b, &c, &e) == 4) { NxVtxAttr4 x; memset(&x, 0, sizeof x); x.offset = a; x.stream = b; x.type = c; x.location = e; attrs.push_back(x); }
            else if (sscanf(line, "zero %d", &a) == 1) zero = a;
        }
        D3DVERTEXELEMENT9 end = D3DDECL_END(); el.push_back(end);
        IDirect3DVertexDeclaration9* decl = nullptr; IDirect3DVertexShader9* sh = nullptr;
        HRESULT h1 = d.i9->CreateVertexDeclaration(el.data(), &decl), h2 = d.i9->CreateVertexShader((const DWORD*)vs.data(), &sh); if (!CHECK(h1 == D3D_OK && h2 == D3D_OK)) { if (n < 40) { printf("  create case %d: decl %x shader %x:", n, (unsigned)h1, (unsigned)h2); for (auto& x : el) printf(" t%d", x.Type); printf("\n"); } n++; continue; }
        d.i9->SetVertexDeclaration(decl); d.i9->SetVertexShader(sh);
        for (int s = 0; s < 4; s++) { d.i9->SetStreamSource(s, vb[s], 0, 16); d.i9->SetStreamSourceFreq(s, s < (int)freq.size() ? freq[s] : 1); if (!bound[s]) d.i9->SetStreamSource(s, nullptr, 0, 0); }
        t_clear(); CHECK_HR(d.i9->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1), D3D_OK); t_flush(); const Ev* e = t_nth(NxOp_Draw, 0); bool ok = (e != nullptr) == (draw != 0);
        if (ok && e) {
            const NxVtxLayout4* L = (const NxVtxLayout4*)e->lay; ok = L->attrCount == attrs.size() && L->instanceStreams == imask;
            for (uint32_t k = 0; ok && k < attrs.size(); k++) ok = L->attr[k].offset == attrs[k].offset && L->attr[k].stream == attrs[k].stream && L->attr[k].type == attrs[k].type && L->attr[k].location == attrs[k].location;
            for (int s = 0; ok && s < 16; s++) ok = L->divisor[s] == (imask >> s & 1 ? div[s] : 0);
            if (ok && zero >= 0) { bool found = false; for (auto& b : g_ev) if (b.op == NxOp_BindVtxN && (int)b.a[0] == zero) found = b.a[3] == 16 && b.a[4] == 0 && b.blob.size() == 16 && b.blob[0] == 0 && b.blob[15] == 0; ok = found; }
        }
        if (!CHECK(ok) && n < 4) printf("  case %d draw %d got %d attrs %u\n", n, draw, e != nullptr, e ? ((const NxVtxLayout4*)e->lay)->attrCount : 0u);
        d.i9->SetVertexShader(nullptr); sh->Release(); decl->Release(); n++;
    }
    if (f) fclose(f); CHECK(n >= 400);
}
