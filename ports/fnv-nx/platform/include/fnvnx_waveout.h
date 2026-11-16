/* fnvnx_waveout.h - the winmm waveOut family, declared once (platform/audio/waveout.cpp defines it, win32/exports.cpp
 * names it in the export table; both take these declarations by #include). Types as Windows' mmeapi.h at ILP32:
 * HWAVEOUT = void *, UINT = unsigned int, DWORD_PTR = uintptr_t, DWORD = unsigned long. */
#ifndef FNVNX_WAVEOUT_H
#define FNVNX_WAVEOUT_H

#include "fnvnx_win32.h"

#ifdef __cplusplus
extern "C" {
#endif

UINT waveOutGetNumDevs(void);
UINT waveOutGetDevCapsA(uintptr_t, void *, UINT);
UINT waveOutGetErrorTextA(UINT, char *, UINT);
UINT waveOutOpen(void **, UINT, const void *, uintptr_t, uintptr_t, DWORD);
UINT waveOutClose(void *);
UINT waveOutPrepareHeader(void *, void *, UINT);
UINT waveOutUnprepareHeader(void *, void *, UINT);
UINT waveOutWrite(void *, void *, UINT);
UINT waveOutPause(void *);
UINT waveOutRestart(void *);
UINT waveOutReset(void *);
UINT waveOutGetPosition(void *, void *, UINT);
UINT waveOutSetVolume(void *, DWORD);
UINT waveOutGetVolume(void *, DWORD *);

#ifdef __cplusplus
}
#endif
#endif
