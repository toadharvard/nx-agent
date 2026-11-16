# Thread suspension and cross-thread observation: rule S

Owner: L5. Agreed with L3a and L3b (lift FLAGLIVE.md, P2').

## The rule

A cross-thread observation of a guest thread is delivered only at a point of **S**. The observations are: SuspendThread taking
effect, GetThreadContext, SetThreadContext, NtGetContextThread, Wow64GetThreadContext, and APC delivery.

- In lifted code, the poll set is **P** = CFG back-edge targets ∪ function entries. Each p in P reads `c->susp` and, when it is
  nonzero, calls `fnvnx_safepoint(c, p)`.
- In the platform, S is: the wait core (the exits of `WaitForSingleObject`/`WaitForMultipleObjects`, `Sleep`, `SleepEx`, the
  reply wait of a cross-thread `SendMessage`) and the thread's own calls (`SuspendThread` on itself).

The platform never stops a thread by the kernel for a user suspension. `os_thread_suspend`, `os_thread_resume` and the
"this backend cannot pause a running thread" path are deleted. `os_world` keeps its pause for the R9 world stop only
(`fnvl_host_world_stop`), and nothing else calls `os_raw_pause`.

## ABI

- `uint32_t susp` is a field of the lift/rt `struct cpu` (append-only, offset 504; sizeof(struct cpu) is 512 by alignment). Only the platform writes it (lifted code reads it with a relaxed atomic load), with a release
  store while holding `w32_wait_lock` (the suspend lock of every thread object). Nonzero means the suspend count is above 0.
- `fnvnx_susp_attach(c, &c->susp, &c->ra)` / `fnvnx_susp_detach(c)` are called by `fnvl_thread_main` (lift/rt/enter.cc) on
  the thread itself, around the life of its `struct cpu`. After detach, no other thread writes the word, so `c` may go away.
- `extern "C" uint32_t fnvnx_safepoint(void *c, uint32_t pc)` (win32/thread.cpp) blocks while the suspend count is above 0 and
  then stores the Eip to continue at in `c->ra` and returns it (today `pc`: see "Not yet" below).

## Delivery argument

1. Every guest thread has a thread object and attaches, the first thread included: `fnvnx_susp_attach` (called by `fnvl_thread_main`
   and `fnvl_attach_main`) makes the first thread's object (its tid, `started`, its OS thread). The pseudo handle resolves to it, so
   `SuspendThread(GetCurrentThread())` and a suspension of the first thread from another thread work like on any thread. A platform
   service thread runs no guest code and has no handle. A safepoint on an unattached thread cannot happen, so `fnvnx_safepoint` stores
   `c->ra` on every path.
2. `SuspendThread(other)` increments the count and publishes `susp = 1` under the lock, then returns the previous count. It
   is asynchronous, as Windows documents.
3. A running lifted thread reaches a point of P after a bounded number of instructions (every cycle of the CFG contains a back-edge
   target, every call enters a function), reads `susp != 0`, and parks in `fnvnx_safepoint` until `ResumeThread` brings the count to 0
   (the release store of 0 and the broadcast are under the same lock).
4. A thread inside the wait core is at a point of S for the whole wait: its suspension takes effect when the wait ends, before
   the exit returns to lifted code (`w32_susp_point`). A suspension is observed by no instruction of the game between its
   delivery and the resume.
5. A suspension that arrives before the thread's frame exists (CREATE_SUSPENDED, or a thread that has not entered lifted code)
   keeps the count; `fnvnx_susp_attach` publishes it, so the first poll parks.
6. Windows and Wine 9.0 (dlls/ntdll/thread.c `NtSuspendThread`/`NtResumeThread`, server/thread.c `suspend_thread`/`resume_thread`):
   the previous count is returned, a thread with count > 0 does not run.

## Every delivery point in the platform

<!-- delivery-functions: fnvnx_susp_attach fnvnx_susp_detach fnvnx_safepoint susp_publish park_locked w32_susp_point -->
| function | file | role |
|---|---|---|
| `fnvnx_safepoint` | win32/thread.cpp | the lifted poll's target |
| `w32_susp_point` | win32/thread.cpp | the exit of the wait core and of `Sleep`; called by `wait_entry` (sync.cpp), `Sleep` (thread.cpp), `send_cross` (msgqueue.cpp) |
| `park_locked` | win32/thread.cpp | the one blocking loop (the self path of `SuspendThread` uses it too) |
| `susp_publish` | win32/thread.cpp | the one writer of `c->susp`, called by `SuspendThread` and `ResumeThread` |
| `fnvnx_susp_attach`, `fnvnx_susp_detach` | win32/thread.cpp | the registration of the thread's frame |

APC delivery: the platform has no `QueueUserAPC`, and `SleepEx`/waits ignore `alertable` (no APC exists to deliver), so
the wait core is the point where it would be delivered, and no other.

## The mechanical check

`platform/test_threads_rule.py` parses every platform source. The observed-context fields of the thread object are those named
`obs_*`. The test fails if any function other than those listed in the `delivery-functions` comment above mentions one, if one of
them is defined outside win32/thread.cpp, or if anything other than `os_world.c` calls `os_raw_pause`/`os_raw_resume`.
There is no per-site list: a new function that touches an `obs_*` field is red until it is a delivery point and is listed here.

## Not yet

`GetThreadContext`, `SetThreadContext`, `NtGetContextThread`, `Wow64GetThreadContext` are not in the platform's export tables
at this commit. Their bodies need the CONTEXT capture and restore of lift/rt/seh.cc (`ctx_capture`/`ctx_restore`, which run on the
target's own registers), so they are delivered in the target's thread at a safepoint, through an entry that L3a exposes. Until
then the rule covers them by construction: a new function that reads a thread's saved context needs an `obs_*` field, which the check
refuses outside the delivery points.
