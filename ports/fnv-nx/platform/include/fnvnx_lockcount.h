/* fnvnx_lockcount.h - how many platform locks the calling thread holds, for the rule "no platform lock is held across a
 * call into the game" (fnvnx_guestfn.h: the runtime may not return from a guest call -- a game exception unwinding past the
 * import discards the native frames without running destructors or unlocking).
 *
 * Every lock wrapper of the platform that is in scope of the rule (the DirectShow graph lock: dshow_int.h GraphLock; the
 * mixer lock: mixer.cpp) calls fnvnx_bnd_lock_note(+1) after it acquired and fnvnx_bnd_lock_note(-1) before it releases.
 * The count is a plain thread-local integer. The product's boundary does not look at it; the host test boundary
 * (tests/s4/host_boundary.cpp) records a violation whenever fnvnx_bnd_guest_invoke runs with a count other than 0, and the
 * test mains require that record to stay 0 over the whole run. */
#ifndef FNVNX_LOCKCOUNT_H
#define FNVNX_LOCKCOUNT_H
#ifdef __cplusplus
inline unsigned &fnvnx_bnd_lockcount_ref()
{
    static __thread unsigned n;
    return n;
}
/* the number of in-scope platform locks this thread holds now */
inline unsigned fnvnx_bnd_locks_held() { return fnvnx_bnd_lockcount_ref(); }
inline void fnvnx_bnd_lock_note(int delta) { fnvnx_bnd_lockcount_ref() += (unsigned)delta; }
#endif
#endif
