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

namespace {
struct exp { const char *dll, *name; FARPROC fn; };
#define X(dll, name, fn) { dll, name, (FARPROC)(void *)&fn }
const exp g_exports[] = {
    X("dsound", "DirectSoundCreate8", DirectSoundCreate8),
    X("dsound", "DirectSoundCreate", DirectSoundCreate),
    X("dsound", "DirectSoundEnumerateA", DirectSoundEnumerateA),
    X("dinput8", "DirectInput8Create", DirectInput8Create),
    X("xinput1_3", "XInputGetState", XInputGetState),
    X("xinput1_3", "XInputSetState", XInputSetState),
    X("xinput1_3", "XInputGetCapabilities", XInputGetCapabilities),
    X("xinput1_3", "XInputEnable", XInputEnable),
    X("xinput1_3", "XInputGetDSoundAudioDeviceGuids", XInputGetDSoundAudioDeviceGuids),
    X("xinput1_3", "XInputGetBatteryInformation", XInputGetBatteryInformation),
    X("xinput1_3", "XInputGetKeystroke", XInputGetKeystroke),
};

const char *base(const char *p)
{
    const char *b = p;
    for (; *p; p++)
        if (*p == '\\' || *p == '/' || *p == ':') b = p + 1;
    return b;
}

/* "XINPUT1_3.DLL" == "xinput1_3"; xinput1_4 and xinput9_1_0 resolve to the same functions */
bool dll_eq(const char *a, const char *want)
{
    a = base(a);
    size_t la = strlen(a);
    if (la > 4 && !strcasecmp(a + la - 4, ".dll")) la -= 4;
    auto is = [&](const char *n) { return la == strlen(n) && !strncasecmp(a, n, la); };
    if (!strcmp(want, "xinput1_3")) return is("xinput1_3") || is("xinput1_4") || is("xinput9_1_0");
    return is(want);
}

FARPROC find(const char *dll, const char *name)
{
    for (const exp &e : g_exports)
        if (!strcmp(e.name, name) && (!dll || dll_eq(dll, e.dll))) return e.fn;
    return nullptr;
}

FARPROC r_dsound(const char *n) { return find("dsound", n); }
FARPROC r_dinput8(const char *n) { return find("dinput8", n); }
FARPROC r_xinput(const char *n) { return find("xinput1_3", n); }

int g_done;
} // namespace

extern "C" void *fnvnx_audio_input_lookup_export(const char *dll, const char *name)
{
    return name ? (void *)find(dll, name) : nullptr;
}

extern "C" void fnvnx_audio_input_register(void)
{
    if (__atomic_exchange_n(&g_done, 1, __ATOMIC_ACQ_REL)) return;
    fnvnx_register_module("dsound.dll", r_dsound);
    fnvnx_register_module("dinput8.dll", r_dinput8);
    fnvnx_register_module("xinput1_3.dll", r_xinput);
    fnvnx_register_module("xinput1_4.dll", r_xinput);
    fnvnx_register_module("xinput9_1_0.dll", r_xinput);
    fnvnx_register_com_class(&CLSID_DirectSound8, fnvnx_dsound_com_factory);
    fnvnx_register_com_class(&CLSID_DirectSound, fnvnx_dsound_com_factory);
    fnvnx_register_com_class(&CLSID_DirectInput8, fnvnx_dinput_com_factory);
    static const GUID clsid_filter_graph = {0xe436ebb3, 0x524f, 0x11ce, {0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
    fnvnx_register_com_class(&clsid_filter_graph, fnvnx_dshow_com_factory);   /* DirectShow: the game's music */
    static const GUID clsid_filter_mapper = {0xe436ebb2, 0x524f, 0x11ce, {0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
    fnvnx_register_com_class(&clsid_filter_mapper, fnvnx_dshow_mapper_factory);   /* IFilterMapper: the installed filters */
}

__attribute__((constructor(FNVNX_INIT_MODULES))) static void fnvnx_audio_input_ctor(void) { fnvnx_audio_input_register(); }
