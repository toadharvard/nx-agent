/* linkprobe.c: creates every shader of probe_in.tsv under Wine 9.0 d3d9, pairs it with the minimal shader of the other stage and draws one triangle, so that wined3d
 * generates, compiles and links the GLSL. usage: linkprobe.exe probe_in.tsv out.tsv
 * probe_in.tsv line: n <TAB> v|p <TAB> hex(shader) <TAB> hex(partner).  out.tsv line: n <TAB> create hr <TAB> partner create hr <TAB> draw hr (hex).
 * Wine's own trace goes to stderr; this program brackets every item there with "@@ROW n" / "@@END n" (one write each, unbuffered), so the parent attributes
 * the trace lines (make_link.py) to the item. */
#include <windows.h>
#include <d3d9.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static DWORD *unhex(const char *s, size_t *nbytes) {
    size_t n = strlen(s) / 2; DWORD *b = (DWORD *)malloc(n + 4);
    for (size_t i = 0; i < n; i++) { unsigned v; sscanf(s + 2 * i, "%2x", &v); ((unsigned char *)b)[i] = (unsigned char)v; }
    *nbytes = n; return b;
}
static void mark(const char *k, int n) { char b[64]; int l = sprintf(b, "@@%s %d\n", k, n); DWORD w; WriteFile(GetStdHandle(STD_ERROR_HANDLE), b, l, &w, NULL); }

struct pcache { char *hex; IDirect3DVertexShader9 *vs; IDirect3DPixelShader9 *ps; HRESULT hr; } pc[16]; int npc;

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    FILE *in = fopen(argv[1], "rb"); if (!in) return 3;
    FILE *out = fopen(argv[2], "wb"); if (!out) return 4;
    HWND wnd = CreateWindowA("STATIC", "linkprobe", WS_OVERLAPPEDWINDOW, 0, 0, 64, 64, NULL, NULL, NULL, NULL);
    IDirect3D9 *d3d = Direct3DCreate9(D3D_SDK_VERSION); if (!d3d) return 5;
    D3DPRESENT_PARAMETERS pp; memset(&pp, 0, sizeof pp);
    pp.Windowed = TRUE; pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.BackBufferFormat = D3DFMT_UNKNOWN; pp.BackBufferWidth = 64; pp.BackBufferHeight = 64; pp.hDeviceWindow = wnd;
    IDirect3DDevice9 *dev = NULL;
    HRESULT hr = IDirect3D9_CreateDevice(d3d, 0, D3DDEVTYPE_HAL, wnd, D3DCREATE_HARDWARE_VERTEXPROCESSING, &pp, &dev);
    if (FAILED(hr)) { fprintf(stderr, "CreateDevice %08lx\n", (unsigned long)hr); return 6; }
    /* one vertex layout for every row: position, normal, colour, eight texcoords (4 floats each) */
    D3DVERTEXELEMENT9 el[12]; int ne = 0; WORD off = 0;
    #define EL(t, u, i, sz) do { el[ne].Stream = 0; el[ne].Offset = off; el[ne].Type = t; el[ne].Method = 0; el[ne].Usage = u; el[ne].UsageIndex = i; ne++; off += sz; } while (0)
    EL(D3DDECLTYPE_FLOAT4, D3DDECLUSAGE_POSITION, 0, 16); EL(D3DDECLTYPE_FLOAT3, D3DDECLUSAGE_NORMAL, 0, 12); EL(D3DDECLTYPE_FLOAT4, D3DDECLUSAGE_COLOR, 0, 16);
    for (int i = 0; i < 8; i++) EL(D3DDECLTYPE_FLOAT4, D3DDECLUSAGE_TEXCOORD, i, 16);
    D3DVERTEXELEMENT9 end = D3DDECL_END(); el[ne++] = end;
    IDirect3DVertexDeclaration9 *decl = NULL; hr = IDirect3DDevice9_CreateVertexDeclaration(dev, el, &decl); if (FAILED(hr)) return 7;
    IDirect3DDevice9_SetVertexDeclaration(dev, decl);
    float vtx[3][43]; memset(vtx, 0, sizeof vtx);
    for (int i = 0; i < 3; i++) { vtx[i][0] = i == 1 ? 1.f : -1.f; vtx[i][1] = i == 2 ? 1.f : -1.f; vtx[i][2] = .5f; vtx[i][3] = 1.f; }
    size_t cap = 1 << 20; char *line = (char *)malloc(cap);
    while (fgets(line, (int)cap, in)) {
        char *f[4]; int nf = 0; char *p = line;
        for (; nf < 4; nf++) { f[nf] = p; char *t = strchr(p, '\t'); if (!t) { t = strchr(p, '\n'); if (t) *t = 0; break; } *t = 0; p = t + 1; }
        char *nl = strchr(f[3], '\n'); if (nl) *nl = 0; char *cr = strchr(f[3], '\r'); if (cr) *cr = 0;
        int n = atoi(f[0]); int isv = f[1][0] == 'v';
        size_t nb, pb; DWORD *sh = unhex(f[2], &nb), *pt = unhex(f[3], &pb);
        mark("ROW", n);
        IDirect3DVertexShader9 *vs = NULL; IDirect3DPixelShader9 *ps = NULL; HRESULT hc, hp = S_OK, hd = S_OK;
        if (isv) hc = IDirect3DDevice9_CreateVertexShader(dev, sh, &vs); else hc = IDirect3DDevice9_CreatePixelShader(dev, sh, &ps);
        if (SUCCEEDED(hc)) {                         /* the partner is created once per distinct text */
            struct pcache *c = NULL; for (int i = 0; i < npc; i++) if (!strcmp(pc[i].hex, f[3]) && (pc[i].vs != NULL) == !isv) c = &pc[i];
            if (!c) { c = &pc[npc++]; c->hex = _strdup(f[3]); c->vs = NULL; c->ps = NULL;
                      if (isv) c->hr = IDirect3DDevice9_CreatePixelShader(dev, pt, &c->ps); else c->hr = IDirect3DDevice9_CreateVertexShader(dev, pt, &c->vs); }
            hp = c->hr;
            if (SUCCEEDED(hp)) {
                IDirect3DDevice9_SetVertexShader(dev, isv ? vs : c->vs); IDirect3DDevice9_SetPixelShader(dev, isv ? c->ps : ps);
                IDirect3DDevice9_BeginScene(dev);
                hd = IDirect3DDevice9_DrawPrimitiveUP(dev, D3DPT_TRIANGLELIST, 1, vtx, sizeof vtx[0]);
                IDirect3DDevice9_EndScene(dev);
                { IDirect3DQuery9 *q = NULL;           /* wined3d runs the draw on its command-stream thread: wait for it (an event query) so that its GLSL trace lies inside the row's window */
                  if (SUCCEEDED(IDirect3DDevice9_CreateQuery(dev, D3DQUERYTYPE_EVENT, &q))) {
                      IDirect3DQuery9_Issue(q, D3DISSUE_END); while (IDirect3DQuery9_GetData(q, NULL, 0, D3DGETDATA_FLUSH) == S_FALSE) Sleep(1);
                      IDirect3DQuery9_Release(q); } else hd = E_FAIL; }
                IDirect3DDevice9_SetVertexShader(dev, NULL); IDirect3DDevice9_SetPixelShader(dev, NULL);
            }
        }
        mark("END", n);
        fprintf(out, "%d\t%08lx\t%08lx\t%08lx\n", n, (unsigned long)hc, (unsigned long)hp, (unsigned long)hd); fflush(out);
        if (vs) IDirect3DVertexShader9_Release(vs); if (ps) IDirect3DPixelShader9_Release(ps); free(sh); free(pt);
    }
    fclose(out); return 0;
}
