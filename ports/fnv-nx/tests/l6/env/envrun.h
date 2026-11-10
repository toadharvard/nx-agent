/* envrun.h: the environment query driver shared by envprobe2.c (mingw, real D3D9 under Wine) and hostenv.cpp (our NxDirect3D9).
 * Needs from the includer: ENV_CREATE9(sdk) -> IDirect3D9*, ENV_CREATE9EX(sdk, &p) -> HRESULT, FILE* g_out, and envq.gen.h / envfmt.gen.h on the include path.
 * One output row per query: Name(a0,a1,..)<TAB>ret<TAB>outs   (args and ret in hex; outs `-` = untouched out parameter, else field=value ...). */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#ifdef __cplusplus
#define C9(o, m, ...) (o)->m(__VA_ARGS__)
#define CX(o, m, ...) (o)->m(__VA_ARGS__)
#define RIID(g) (g)
#define UQI(u, g, pp) (u)->QueryInterface(g, pp)
#define UADD(u) (u)->AddRef()
#define UREL(u) (u)->Release()
#else
#define UQI(u, g, pp) (u)->lpVtbl->QueryInterface(u, g, pp)
#define UADD(u) (u)->lpVtbl->AddRef(u)
#define UREL(u) (u)->lpVtbl->Release(u)
#define C9(o, m, ...) IDirect3D9_##m(o, ##__VA_ARGS__)
#define CX(o, m, ...) IDirect3D9Ex_##m(o, ##__VA_ARGS__)
#define RIID(g) (&(g))
#endif
typedef struct { const char* name; unsigned off, size; char kind; } EnvLeaf;
#include "envfmt.gen.h"
#define SENT 0xCD
static IDirect3D9* g_d; static IDirect3D9Ex* g_dx; static unsigned long g_rows;

static void pr_struct(char* o, const EnvLeaf* l, int n, const unsigned char* p, unsigned total) {
    unsigned i, k; int untouched = 1;
    for (i = 0; i < total; i++) if (p[i] != SENT) { untouched = 0; break; }
    if (untouched) { strcpy(o, "-"); return; }
    for (i = 0; i < (unsigned)n; i++) {
        o += strlen(o); o += sprintf(o, "%s%s=", i ? " " : "", l[i].name);
        if (l[i].kind == 'c') { for (k = 0; k < l[i].size && p[l[i].off + k]; k++) { unsigned char c = p[l[i].off + k]; if (c >= 32 && c < 127 && c != '\\' && c != ' ') *o++ = (char)c; else o += sprintf(o, "\\x%02x", c); } *o = 0; }
        else if (l[i].kind == 'b') { for (k = 0; k < l[i].size; k++) o += sprintf(o, "%02x", p[l[i].off + k]); }
        else { unsigned v = 0; for (k = l[i].size; k-- > 0;) v = (v << 8) | p[l[i].off + k]; o += sprintf(o, "%x", v); }
    }
}
#define PRS(buf, T, x) pr_struct(buf, LEAVES_##T, sizeof(LEAVES_##T) / sizeof(EnvLeaf), (const unsigned char*)&(x), sizeof(x))
static void row(int op, int n, const unsigned* a, unsigned ret, const char* outs) {
    int i; fprintf(g_out, "%s(", ENVQ_NAMES[op]); for (i = 0; i < n; i++) fprintf(g_out, "%s%x", i ? "," : "", a[i]); fprintf(g_out, ")\t%x\t%s\n", ret, outs); g_rows++;
}
static void one(int op, int n, unsigned a0, unsigned a1, unsigned a2, unsigned a3, unsigned a4, unsigned a5) {
    unsigned a[6] = { a0, a1, a2, a3, a4, a5 }; static char ob[16384]; unsigned ret = 0; DWORD q; ob[0] = 0;
    D3DCAPS9 caps; D3DADAPTER_IDENTIFIER9 id; D3DDISPLAYMODE dm; D3DDISPLAYMODEEX dx; D3DDISPLAYMODEFILTER fl; D3DDISPLAYROTATION rot; LUID luid; IDirect3D9Ex* x = g_dx; IDirect3D9Ex* p = NULL;
    memset(&caps, SENT, sizeof caps); memset(&id, SENT, sizeof id); memset(&dm, SENT, sizeof dm); memset(&dx, SENT, sizeof dx); memset(&luid, SENT, sizeof luid); q = 0xCDCDCDCDu; rot = (D3DDISPLAYROTATION)0xCDCDCDCDu;
    if (op >= 12 && op <= 15 && !x) { row(op, n, a, 0xdeadbeefu, "NOEX"); return; }
    switch (op) {
    case 0: ret = C9(g_d, GetAdapterCount); break;
    case 1: ret = C9(g_d, GetAdapterIdentifier, a[0], a[1], &id); PRS(ob, D3DADAPTER_IDENTIFIER9, id); break;
    case 2: ret = (unsigned)(size_t)C9(g_d, GetAdapterMonitor, a[0]); break;
    case 3: ret = C9(g_d, GetAdapterDisplayMode, a[0], &dm); PRS(ob, D3DDISPLAYMODE, dm); break;
    case 4: ret = C9(g_d, GetAdapterModeCount, a[0], (D3DFORMAT)a[1]); break;
    case 5: ret = C9(g_d, EnumAdapterModes, a[0], (D3DFORMAT)a[1], a[2], &dm); PRS(ob, D3DDISPLAYMODE, dm); break;
    case 6: ret = C9(g_d, GetDeviceCaps, a[0], (D3DDEVTYPE)a[1], &caps); PRS(ob, D3DCAPS9, caps); break;
    case 7: ret = C9(g_d, CheckDeviceType, a[0], (D3DDEVTYPE)a[1], (D3DFORMAT)a[2], (D3DFORMAT)a[3], a[4]); break;
    case 8: ret = C9(g_d, CheckDeviceFormat, a[0], (D3DDEVTYPE)a[1], (D3DFORMAT)a[2], a[3], (D3DRESOURCETYPE)a[4], (D3DFORMAT)a[5]); break;
    case 9: ret = C9(g_d, CheckDepthStencilMatch, a[0], (D3DDEVTYPE)a[1], (D3DFORMAT)a[2], (D3DFORMAT)a[3], (D3DFORMAT)a[4]); break;
    case 10: ret = C9(g_d, CheckDeviceMultiSampleType, a[0], (D3DDEVTYPE)a[1], (D3DFORMAT)a[2], a[3], (D3DMULTISAMPLE_TYPE)a[4], &q); if (q != 0xCDCDCDCDu) sprintf(ob, "q=%x", q); else strcpy(ob, "-"); break;
    case 11: ret = C9(g_d, CheckDeviceFormatConversion, a[0], (D3DDEVTYPE)a[1], (D3DFORMAT)a[2], (D3DFORMAT)a[3]); break;
    case 12: fl.Size = sizeof fl; fl.Format = (D3DFORMAT)a[1]; fl.ScanLineOrdering = (D3DSCANLINEORDERING)a[2]; ret = CX(x, GetAdapterModeCountEx, a[0], &fl); break;
    case 13: fl.Size = sizeof fl; fl.Format = (D3DFORMAT)a[1]; fl.ScanLineOrdering = (D3DSCANLINEORDERING)a[2]; ret = CX(x, EnumAdapterModesEx, a[0], &fl, a[3], &dx); PRS(ob, D3DDISPLAYMODEEX, dx); break;
    case 14: dx.Size = a[1]; ret = CX(x, GetAdapterDisplayModeEx, a[0], &dx, &rot);
        { char t[64]; PRS(ob, D3DDISPLAYMODEEX, dx); sprintf(t, " rot=%x", (unsigned)rot); if (rot != (D3DDISPLAYROTATION)0xCDCDCDCDu) strcat(ob, t); } break;
    case 15: ret = CX(x, GetAdapterLUID, a[0], &luid); PRS(ob, LUID, luid); break;
    case 16: { IDirect3D9* t = ENV_CREATE9(a[0]); ret = t != NULL; if (t) C9(t, Release); } break;
    case 17: ret = ENV_CREATE9EX(a[0], &p); sprintf(ob, "p=%d", p != NULL); if (p) CX(p, Release); break;
    }
    row(op, n, a, ret, ob);
}
#define ENVQ(op, n, a0, a1, a2, a3, a4, a5) one(op, n, a0, a1, a2, a3, a4, a5)
#include "envq.gen.h"

/* IUnknown-level and creation answers of the factory objects (QueryInterface lattice, reference counts, RegisterSoftwareDevice) */
static const GUID G_IID[] = { { 0x00000000, 0, 0, { 0xC0, 0, 0, 0, 0, 0, 0, 0x46 } }, { 0x81BDCBCA, 0x64D4, 0x426d, { 0xAE, 0x8D, 0xAD, 0x01, 0x47, 0xF4, 0x27, 0x5C } },
    { 0x02177241, 0x69FC, 0x400C, { 0x8F, 0xF1, 0x93, 0xA4, 0x4D, 0xF6, 0x86, 0x1D } }, { 0xD0223B96, 0xBF7A, 0x43fd, { 0x92, 0xBD, 0xA4, 0x3B, 0x0D, 0x82, 0xB9, 0xEB } },
    { 0x12345678, 0x1234, 0x5678, { 1, 2, 3, 4, 5, 6, 7, 8 } } };
static void misc_row(const char* name, unsigned a0, unsigned a1, unsigned ret, const char* outs) { fprintf(g_out, "%s(%x,%x)\t%x\t%s\n", name, a0, a1, ret, outs); g_rows++; }
static void env_misc(void) {
    int w, i; static char b[64];
    for (w = 0; w < 2; w++) {
        IUnknown* u = w ? (IUnknown*)g_dx : (IUnknown*)g_d; if (!u) continue;
        for (i = 0; i < 5; i++) {
            void* o = (void*)0x1; HRESULT hr = UQI(u, RIID(G_IID[i]), &o);
            sprintf(b, "out=%s", o == NULL ? "null" : o == (void*)u ? "same" : o == (void*)1 ? "untouched" : "other");
            misc_row("QueryInterface", w, i, (unsigned)hr, b); if (SUCCEEDED(hr) && o) UREL((IUnknown*)o);
        }
        { unsigned r1 = UADD(u), r2 = UREL(u);
          misc_row("AddRef", w, 0, r1, ""); misc_row("Release", w, 0, r2, ""); }
    }
    { HRESULT hr = C9(g_d, RegisterSoftwareDevice, NULL); misc_row("RegisterSoftwareDevice", 0, 0, (unsigned)hr, ""); hr = C9(g_d, RegisterSoftwareDevice, (void*)(size_t)0x1000); misc_row("RegisterSoftwareDevice", 1, 0, (unsigned)hr, ""); }
}
