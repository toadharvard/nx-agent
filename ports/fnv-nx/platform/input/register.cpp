/* register.cpp - hooks S7's DLL replacements into the runtime (S4):
 *   COM      CLSID_DirectSound8, CLSID_DirectSound, CLSID_DirectInput8, CLSID_FilterGraph              -> CoCreateInstance
 *   Modules (dsound, dinput8, xinput*) are system modules of the generated export table (platform/exptable); nothing is registered per module.
 * Runs from a static constructor and is idempotent. */
#include <dinput.h>
#include <dsound.h>
#include <xinput.h>
#include "../include/fnvnx_win32_ext.h"

#include "input.h"
#include "../os/os.h"   /* FNVNX_INIT_MODULES */

extern "C" {
HRESULT fnvnx_dsound_com_factory(const GUID *iid, void **out);
HRESULT fnvnx_dinput_com_factory(const GUID *iid, void **out);
HRESULT fnvnx_dshow_com_factory(const GUID *iid, void **out);   /* platform/audio/dshow.cpp */
HRESULT fnvnx_dshow_mapper_factory(const GUID *iid, void **out);
}

namespace { int g_done; }

extern "C" void fnvnx_audio_input_register(void)
{
    if (__atomic_exchange_n(&g_done, 1, __ATOMIC_ACQ_REL)) return;
    fnvnx_register_com_class(&CLSID_DirectSound8, fnvnx_dsound_com_factory);
    fnvnx_register_com_class(&CLSID_DirectSound, fnvnx_dsound_com_factory);
    fnvnx_register_com_class(&CLSID_DirectInput8, fnvnx_dinput_com_factory);
    static const GUID clsid_filter_graph = {0xe436ebb3, 0x524f, 0x11ce, {0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
    fnvnx_register_com_class(&clsid_filter_graph, fnvnx_dshow_com_factory);   /* DirectShow: the game's music */
    static const GUID clsid_filter_mapper = {0xe436ebb2, 0x524f, 0x11ce, {0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};
    fnvnx_register_com_class(&clsid_filter_mapper, fnvnx_dshow_mapper_factory);   /* IFilterMapper: the installed filters */
}

__attribute__((constructor(FNVNX_INIT_MODULES))) static void fnvnx_audio_input_ctor(void) { fnvnx_audio_input_register(); }
