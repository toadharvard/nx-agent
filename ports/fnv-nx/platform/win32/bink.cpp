/* bink.cpp - binkw32.dll: movies are skipped (decision of S4, journal: inbox s4-001).
 *
 * BinkOpen returns NULL. The engine's caller then logs "Could not open %s for playback" and returns false: the
 * path a deleted .bik file takes on the PC, which the game handles (intro and menu movies are skipped). Every other
 * entry point is a no-op that is safe on a NULL handle. The units call the plain names (and `_BinkPause_8`, the
 * decomp's spelling of _BinkPause@8); the exe's IAT imports the stdcall-decorated names, which exports.cpp maps
 * here. Argument lists follow the Bink 1 SDK (the decorations give their sizes: _BinkCopyToBufferRect@44 has 11). */
#include "w32.h"

typedef void *HBINK;

extern "C" {
HBINK BinkOpen(const char *name, unsigned flags)
{
    (void)name;
    (void)flags;
    /* a documented skip, not a missing implementation (the platform lane counts it as noop, D25 #3 leaves it out):
     * NULL is what a missing .bik gives on the PC, and every call site of the exe's BinkOpen slot null-tests the
     * handle before use (V1 BINK-1, tests/v1/test_s2c_s4_batch.py), so the intro and menu movies are skipped. No
     * decoder (owner decision). */
    FNVNX_NOOP();
    SetLastError(ERROR_FILE_NOT_FOUND);
    return NULL;
}
void BinkClose(HBINK) {}
int BinkDoFrame(HBINK) { return 0; }
void BinkNextFrame(HBINK) {}
int BinkWait(HBINK) { return 0; }      /* 0: "show the next frame now", so no caller spins on a NULL movie */
int BinkPause(HBINK, int) { return 0; }
int BinkCopyToBufferRect(HBINK, void *, int, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned,
                         unsigned)
{
    return 0;
}
/* BinkOpenDirectSound(param) returns the sound system's open function, passed on to BinkSetSoundSystem: NULL
 * (no Bink audio). BinkSetSoundSystem reports success so the engine's audio setup does not log a failure. */
void *BinkOpenDirectSound(unsigned long) { return NULL; }
int BinkSetSoundSystem(void *, unsigned long) { return 1; }
int _BinkPause_8(HBINK h, int pause) { return BinkPause(h, pause); }
}
