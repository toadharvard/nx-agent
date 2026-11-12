/* os_world.h - internal interface between os_world.c (the world registry, the world lock, and the combination of the
 * two pause reasons) and the three OS backends (os_posix.c, os_horizon.c, platform/guest/os_guest.c). Not for the
 * platform above platform/os. */
#ifndef FNVNX_OS_WORLD_H
#define FNVNX_OS_WORLD_H
#include "os.h"

#ifdef __cplusplus
extern "C" {
#endif

/* one registry entry, embedded in the backend's struct os_thread. Fields are guarded by the world lock. */
typedef struct os_wslot {
    struct os_wslot *next, *prev;
    struct os_thread *t;
    int linked;                    /* in the registry */
    int dead;                      /* unregistered for good: the thread executes only its exit code */
    int user;                      /* paused by os_thread_suspend (the Win32 SuspendThread layer) */
} os_wslot;

/* called by the thread itself, with no platform lock held: first thing before its function runs / last thing after it.
 * Both take the world lock, so they block while the world is stopped. */
void os_world_register(os_wslot *s, struct os_thread *t);
void os_world_unregister(os_wslot *s);

/* backend side */
os_wslot *os_thread_wslot(os_thread *t);
/* The pause primitive: a boolean per thread. os_raw_pause returns 0 only when the target is synchronously stopped (it
 * executes no instruction until os_raw_resume), -1 for the calling thread or a thread that cannot be paused. Callers hold
 * the world lock. */
int os_raw_pause(os_thread *t);
void os_raw_resume(os_thread *t);
/* a record for the calling thread, which the platform did not create (the process's main thread) */
os_thread *os_raw_adopt_self(void);

#ifdef __cplusplus
}
#endif
#endif
