/* fnvnx_fls.h - kernel32 fiber local storage, declared once (win32/fls.cpp defines it, win32/exports.cpp names it in the
 * export table). FlsAlloc's parameter is the game's callback: one guest word, fnvnx_guestfn (fnvnx_guestfn.h). */
#ifndef FNVNX_FLS_H
#define FNVNX_FLS_H

#include "fnvnx_win32.h"
#include "fnvnx_guestfn.h"

extern "C" {
DWORD FlsAlloc(fnvnx_guestfn<void(void *)>);
BOOL FlsFree(DWORD);
LPVOID FlsGetValue(DWORD);
BOOL FlsSetValue(DWORD, LPVOID);
}
#endif
