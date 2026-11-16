/* register.cpp - hooks S7's DLL replacements into the runtime (S4):
 *   modules  dsound, dinput8, xinput1_3 (also xinput1_4/9_1_0 aliases)  -> LoadLibrary/GetProcAddress
 *   COM      CLSID_DirectSound8, CLSID_DirectSound, CLSID_DirectInput8, CLSID_FilterGraph              -> CoCreateInstance
 *   IAT      fnvnx_audio_input_lookup_export(dll, name) for the loader's import resolution (the S4 export
 *            table, fnvnx_win32_lookup_export, does not consult registered modules; see AUDIO_INPUT.md).
 * Runs from a static constructor and is idempotent. */
#include <dinput.h>
#include <dsound.h>
#include <xinput.h>
#include "../include/fnvnx_win32_ext.h"

#include "input.h"
#include "../os/os.h"   /* FNVNX_INIT_MODULES */

#include <string.h>
#include <strings.h>

extern "C" {
HRESULT fnvnx_dsound_com_factory(const GUID *iid, void **out);
HRESULT fnvnx_dinput_com_factory(const GUID *iid, void **out);
HRESULT fnvnx_dshow_com_factory(const GUID *iid, void **out);   /* platform/audio/dshow.cpp */
HRESULT fnvnx_dshow_mapper_factory(const GUID *iid, void **out);
}

/* the rows of the generated export table (platform/exptable: exports.tsv and the bridge table), see win32/exports.cpp */
extern "C" {
struct fnvnx_export_row { const char *dll, *name; void *fn; };
extern const fnvnx_export_row fnvnx_export_rows[];
extern const unsigned fnvnx_export_nrows;
}

namespace {
/* the modules this file serves: the DLL replacements of S7 (audio/input). Each is registered with its own resolver, whose rows are the module's rows of the generated table. */
const char *const g_served[] = { "dsound.dll", "dinput8.dll", "xinput1_3.dll", "xinput1_4.dll", "xinput9_1_0.dll" };
constexpr unsigned N_SERVED = sizeof g_served / sizeof g_served[0];

const char *base(const char *p)
{
    const char *b = p;
    for (; *p; p++)
        if (*p == '\\' || *p == '/' || *p == ':') b = p + 1;
    return b;
}

/* "XINPUT1_3.DLL" == "xinput1_3.dll": the base name without a ".dll", ignoring case */
bool dll_eq(const char *a, const char *want)
{
    a = base(a);
    size_t la = strlen(a), lw = strlen(want);
    if (la > 4 && !strcasecmp(a + la - 4, ".dll")) la -= 4;
    if (lw > 4 && !strcasecmp(want + lw - 4, ".dll")) lw -= 4;
    return la == lw && !strncasecmp(a, want, la);
}

FARPROC find_in(const char *dll, const char *name)
{
    for (unsigned i = 0; i < fnvnx_export_nrows; i++) {
        const fnvnx_export_row &e = fnvnx_export_rows[i];
        if (!strcmp(e.name, name) && dll_eq(dll, e.dll)) return (FARPROC)e.fn;
    }
    return nullptr;
}

/* dll named: its rows; dll NULL: the rows of any served module */
FARPROC find(const char *dll, const char *name)
{
    if (dll) return find_in(dll, name);
    for (const char *m : g_served)
        if (FARPROC f = find_in(m, name)) return f;
    return nullptr;
}

template <unsigned I> FARPROC resolve(const char *n) { return find_in(g_served[I], n); }
const fnvnx_module_resolver g_resolvers[N_SERVED] = { resolve<0>, resolve<1>, resolve<2>, resolve<3>, resolve<4> };

int g_done;
} // namespace

extern "C" void *fnvnx_audio_input_lookup_export(const char *dll, const char *name)
{
    return name ? (void *)find(dll, name) : nullptr;
}

extern "C" void fnvnx_audio_input_register(void)
{
    if (__atomic_exchange_n(&g_done, 1, __ATOMIC_ACQ_REL)) return;
    for (unsigned i = 0; i < N_SERVED; i++)
        fnvnx_register_module(g_served[i], g_resolvers[i]);
    fnvnx_register_com_class(&CLSID_DirectSound8, fnvnx_dsound_com_factory);
    fnvnx_register_com_class(&CLSID_DirectSound, fnvnx_dsound_com_factory);
    fnvnx_register_com_class(&CLSID_DirectInput8, fnvnx_dinput_com_factory);
    static const GUID clsid_filter_graph = {0xe436ebb3, 0x524f, 0x11ce, {0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
    fnvnx_register_com_class(&clsid_filter_graph, fnvnx_dshow_com_factory);   /* DirectShow: the game's music */
    static const GUID clsid_filter_mapper = {0xe436ebb2, 0x524f, 0x11ce, {0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
    fnvnx_register_com_class(&clsid_filter_mapper, fnvnx_dshow_mapper_factory);   /* IFilterMapper: the installed filters */
}

__attribute__((constructor(FNVNX_INIT_MODULES))) static void fnvnx_audio_input_ctor(void) { fnvnx_audio_input_register(); }
