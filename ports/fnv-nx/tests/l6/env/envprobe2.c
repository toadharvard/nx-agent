/* envprobe2.c: asks every `env` question of IDirect3D9 / IDirect3D9Ex over its whole finite domain (envq.py), then the device-level environment answers.
 * Built with mingw by make_env.py; run under Wine 9.0 with the recorder d3d9.dll as `envprobe2.exe OUT.tsv [windowed-run-id]`. Exit 0 iff all phases ran. */
#define COBJMACROS
#include <stdio.h>
#include <windows.h>
#include <d3d9.h>
static FILE* g_out;
#define ENV_CREATE9(sdk) Direct3DCreate9(sdk)
#define ENV_CREATE9EX(sdk, pp) Direct3DCreate9Ex(sdk, pp)
#include "envrun.h"
#include "envdev.h"
int main(int argc, char** argv) {
    if (argc < 2 || !(g_out = fopen(argv[1], "wb"))) return 3;
    g_d = Direct3DCreate9(D3D_SDK_VERSION); if (!g_d) return 1;
    if (FAILED(Direct3DCreate9Ex(D3D_SDK_VERSION, &g_dx))) g_dx = NULL;
    if (argc > 2) { fclose(g_out); return 0; }   /* `pin`: adapter creation only (GL strings are in the WINEDEBUG trace) */
    env_misc(); envq_all();
    env_dev();
    fclose(g_out); return 0;
}
