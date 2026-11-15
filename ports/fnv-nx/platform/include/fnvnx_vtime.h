/* fnvnx_vtime.h - the virtual-time protocol of the platform clock (fnvnx_clock.h), present only in the virtual build.
 *
 * Virtual time (clock_virtual.cpp, definition VC1) is a counter t per thread that what one thread signals, posts or creates carries to
 * the thread that takes it, and a file's times are stamps of t. The real clock has no such thing, so a hook for it exists only in the
 * backend that implements it: with FNVNX_VCLOCK = 1 (the recipe's flag of the virtual build; the same flag builds clock_virtual.cpp
 * instead of clock_real.cpp) the VT_* names below are the clk_* functions of clock_virtual.cpp; without it they expand to nothing but the
 * evaluation of their arguments, no call, no symbol, no function body. A stamp value is 0 in the real build (there is no virtual time
 * to stamp), and VT_WAIT_IDLE answers 0 ("block for real") and VT_FILE_TIMES 0 ("the host file system's"), the answers the contract
 * below gives when no virtual clock exists. A leftover clk_* call of this protocol is a compile error in the real build (undeclared). */
#ifndef FNVNX_VTIME_H
#define FNVNX_VTIME_H

#include <stdint.h>

#ifndef FNVNX_VCLOCK
#define FNVNX_VCLOCK 0
#endif

/* file time kinds of VT_FILE_STAMP (the same in both builds: the call sites name them) */
enum { CLK_FS_CREATE = 1, CLK_FS_WRITE = 2 };

#if FNVNX_VCLOCK

#ifdef __cplusplus
extern "C" {
#endif
void clk_wait_timeout(uint64_t ns);         /* a wait that did not succeed: t += ns, ns == 0: t += q */
uint64_t clk_stamp(void);                   /* the calling thread's t (no advance), carried by what it signals, posts, creates */
void clk_merge(uint64_t other);             /* t = max(t, other) */
void clk_thread_start(uint64_t parent);     /* the first call of a new thread, or a new activation of a service thread: t = the stamp */
/* a posted or sent message: the poster's stamp, and on retrieval the receiver takes it and MSG.time is its t in ms. `stamp`
 * is what clk_msg_post returned, `time` the MSG.time the post wrote. */
uint64_t clk_msg_post(void);
uint32_t clk_msg_retrieve(uint64_t stamp, uint32_t time);
/* a wait about to block with nothing signalled: the audio device runs on the way (handles are the waited HANDLEs, timeout_ns the
 * wait's virtual timeout or UINT64_MAX): 1 = virtual time worked (re-evaluate at once), 0 = block for real */
int clk_wait_idle(const void *const *handles, unsigned n, uint64_t timeout_ns);
/* file times written by the platform: stamps of t (no advance), served back. host is the host path (the file-sharing key's spelling). */
void clk_file_stamp(const char *host, int what);          /* CREATE: creation, access and write time; WRITE: write time */
void clk_file_moved(const char *from, const char *to);
void clk_file_removed(const char *host);
int clk_file_times(const char *host, int64_t ft[3]);      /* 1 and creation/access/write as FILETIME; 0: the host file system's */
/* an event the device signals (DirectSound notification, waveOut CALLBACK_EVENT): a wait on it runs the device */
void clk_device_watch(const void *handle, int on);
#ifdef __cplusplus
}
#endif

#define VT_WAIT_TIMEOUT(ns)        clk_wait_timeout(ns)
#define VT_STAMP()                 clk_stamp()
#define VT_MERGE(t)                clk_merge(t)
#define VT_THREAD_START(parent)    clk_thread_start(parent)
#define VT_MSG_POST()              clk_msg_post()
#define VT_MSG_RETRIEVE(stamp, t)  clk_msg_retrieve(stamp, t)
#define VT_WAIT_IDLE(h, n, to)     clk_wait_idle(h, n, to)
#define VT_FILE_STAMP(host, what)  clk_file_stamp(host, what)
#define VT_FILE_MOVED(from, to)    clk_file_moved(from, to)
#define VT_FILE_REMOVED(host)      clk_file_removed(host)
#define VT_FILE_TIMES(host, ft)    clk_file_times(host, ft)
#define VT_DEVICE_WATCH(h, on)     clk_device_watch(h, on)

#else   /* the real build: no virtual time, no hook */

#define VT_WAIT_TIMEOUT(ns)        ((void)(ns))
#define VT_STAMP()                 ((uint64_t)0)
#define VT_MERGE(t)                ((void)(t))
#define VT_THREAD_START(parent)    ((void)(parent))
#define VT_MSG_POST()              ((uint64_t)0)
#define VT_MSG_RETRIEVE(stamp, t)  ((void)(stamp), (uint32_t)(t))
#define VT_WAIT_IDLE(h, n, to)     ((void)(h), (void)(n), (void)(to), 0)
#define VT_FILE_STAMP(host, what)  ((void)(host), (void)(what))
#define VT_FILE_MOVED(from, to)    ((void)(from), (void)(to))
#define VT_FILE_REMOVED(host)      ((void)(host))
#define VT_FILE_TIMES(host, ft)    ((void)(host), (void)(ft), 0)
#define VT_DEVICE_WATCH(h, on)     ((void)(h), (void)(on))

#endif
#endif
