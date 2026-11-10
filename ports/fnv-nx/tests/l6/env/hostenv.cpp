// hostenv.cpp: the query driver of envprobe2 (envrun.h, same query list) run against OUR NxDirect3D9 / NxDirect3D9Ex. usage: hostenv OUT.tsv (profile from $NXENV_PROFILE = handheld | docked)
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "d3d9_adapter.h"
static FILE* g_out;
NxEnvProfile nx_env_profile() { const char* e = getenv("NXENV_PROFILE"); return e && !strcmp(e, "docked") ? NX_ENV_DOCKED : NX_ENV_HANDHELD; }   // the test's stand-in for the platform decision
#define ENV_CREATE9(sdk) nx_Direct3DCreate9(sdk)
#define ENV_CREATE9EX(sdk, pp) nx_Direct3DCreate9Ex(sdk, pp)
#include "envrun.h"
int main(int argc, char** argv) {
    if (argc < 2 || !(g_out = fopen(argv[1], "wb"))) return 3;
    g_d = nx_Direct3DCreate9(0x20); if (!g_d) return 1;
    if (FAILED(nx_Direct3DCreate9Ex(0x20, &g_dx))) g_dx = NULL;
    env_misc(); envq_all();
    { unsigned z = 0, r; static char o2[256]; D3DDISPLAYMODE dm; memset(&dm, SENT, sizeof dm); r = g_d->GetAdapterDisplayMode(0, &dm); o2[0] = 0; PRS(o2, D3DDISPLAYMODE, dm); row(3, 1, &z, r, o2); }   // the one extra query envdev.h asks
    fclose(g_out); return 0;
}
