/* standins.c - boot stand-ins for functions whose native body is wrong (the decomp's body does not do what the
 * exe's does). Each one is switched on by a line '<symbol> standin' in tests/s5/known_faults.txt: emu_guest.py
 * compiles this file (aarch64-linux-gnu-gcc -mabi=ilp32 -O2 -c), maps it after the guest and points the guest's
 * function at the definition here. Only the listed symbols are redirected. The comment of each says what the exe
 * does, from the exe's own code, and who fixes it; remove a stand-in and its table line when the fix lands.
 * Our own code; it names exe addresses only. */
#include <stdint.h>

extern void *fnv_mm_allocate(void *mm, uint32_t size) __asm__("__fnv_00aa3e40");   /* MemoryManager::Allocate */
extern void *fnv_messagehandler_init(void *self, _Bool is_static)
    __asm__("_ZN14MessageHandler12FUN_00c3bcf0Eb");                                  /* 0x00c3bcf0 */

#define G_MEMORY_MANAGER ((void *)0x011f6238u)
#define MESSAGEHANDLER_PHANDLER (*(void *volatile *)0x01202d74u)

/* 0x00c3c460 (WinMain's first call, via 0x00c3bd30): the exe allocates 8 bytes from the memory manager, runs
 * MessageHandler's constructor 0x00c3bcf0(this, false) on it (vptr 0x010c14a8, bStatic, pHandler = this) and stores
 * the result in MessageHandler::pHandler (0x01202d74). The decomp's body is `new MH(false)` with an empty MH::MH, so
 * the handler has no vptr; 0x00c3bd30 copies it to the heap logger 0x011f6388, and the first AbstractHeap commit
 * calls slot 5 of the zeroed object (S1: the body should call 0x00c3bcf0). */
void fnv_standin_00c3c460(void) __asm__("_Z12FUN_00c3c460v");
void fnv_standin_00c3c460(void) {
    void *p = fnv_mm_allocate(G_MEMORY_MANAGER, 8);
    MESSAGEHANDLER_PHANDLER = p ? fnv_messagehandler_init(p, 0) : 0;
}
