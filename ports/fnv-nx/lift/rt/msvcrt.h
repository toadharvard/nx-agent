/* Shared declarations of rt/msvcrt_eh.cc and rt/msvcrt_fp.cc: the guest entries that stand for the msvcrt.dll functions that work on the GUEST state (bridge rows
   `bind=l3`, symbol fnvl_msvcrt_<name>). Include after fnvl.h and imp.h (C++, lifted-code flags). */
#ifndef FNVL_MSVCRT_H
#define FNVL_MSVCRT_H

/* Per-thread state of the msvcrt C++ runtime (Wine's thread_data_t, the fields the guest entries use). All fields are 32-bit guest addresses or counters and a
   ZERO state is the initial one (no exception being handled, no terminate handler), so the storage needs no initialisation beyond cpu_reset's zeroing:
   `struct cpu` ends with `u32 msvcrt[6];` (24 bytes, the last field, zeroed by cpu_reset in rt/enter.cc). If the owner of struct cpu prefers `void *msvcrt`
   instead, it must point at zeroed storage of this size that is allocated when the thread state is created (never at exception time); the accessor
   below, the only place that reaches it, is the same for both forms. */
struct fnvl_msvcrt_thread {
    u32 exc_record;          /* EXCEPTION_RECORD of the exception whose catch block runs (0: none): `throw;` re-raises it (thread_data_t.exc_record) */
    u32 ctx_record;          /* its CONTEXT */
    u32 frame_info_head;     /* list of live cxx frame_info {next, object}: the exception objects a running catch block still owns (_CreateFrameInfo) */
    u32 processing_throw;    /* int: > 0 while an exception is looked up (__uncaught_exception) */
    u32 terminate_handler;   /* guest address set by set_terminate (0: the default, abort) */
    u32 xcptinfo;            /* EXCEPTION_POINTERS* while a signal() handler runs (__pxcptinfoptrs) */
};
static_assert(sizeof(((struct cpu *)0)->msvcrt) == sizeof(struct fnvl_msvcrt_thread), "struct cpu must end with u32 msvcrt[6] (rt/msvcrt.h)");
static inline struct fnvl_msvcrt_thread *fnvl_msvcrt_td(void) { return (struct fnvl_msvcrt_thread *)c->msvcrt; }

/* Pseudo guest addresses of the two exception handlers the runtime itself registers on the guest fs:[0] chain (a registration's Handler is a guest address that
   rt/seh.cc's call_handler resolves through fnvl_dispatch): the dispatcher table must map them to the guest entries below. Both lie below FNVL_SENT, below every
   image and every import key. */
#define FNVL_EH_KEY_CATCH   0x000fffd0u     /* fnvl_msvcrt_eh_nested_catch:   Wine catch_function_nested_handler */
#define FNVL_EH_KEY_COLLIDE 0x000fffe0u     /* fnvl_msvcrt_eh_nested_collide: Wine MSVCRT_nested_handler */
extern "C" {
void fnvl_msvcrt_eh_nested_catch(void);
void fnvl_msvcrt_eh_nested_collide(void);
/* signal() handlers of the process, indexed by the Windows signal numbers (SIGINT 2, SIGILL 4, SIGFPE 8, SIGSEGV 11, SIGTERM 15, SIGBREAK 21, SIGABRT 22):
   0 SIG_DFL, 1 SIG_IGN, else a guest address. _XcptFilter reads it; the body of signal() writes it. */
extern u32 fnvl_msvcrt_sighandlers[23];
}
#endif
