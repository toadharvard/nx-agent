/* msvcrt.dll exception handling on the GUEST state: the guest entries of the bridge rows bound `l3` (symbol fnvl_msvcrt_<name>). C++, lifted-code flags
   (guest registers in w19..w26, c in x27): aarch64-linux-gnu-g++ -x c++ <FLAGS> -I rt -c msvcrt_eh.cc
   Reference: Wine 9.0 dlls/msvcrt (i386): except_i386.c, except.c, cpp.c; every function below names the Wine function it transcribes. The code is written
   against the documented MSVC structures (EH_PROLOG frame, FuncInfo / TryBlockMap / HandlerType / UnwindMap, ThrowInfo / CatchableTypeArray, _JUMP_BUFFER),
   with pointers as 32-bit guest addresses read through M8 / M32.
   Conventions. A guest entry reads its arguments from the guest stack at esp+4.. and ends with `c->ra = M32(sp0); esp = sp0 + 4 [+ 4n for stdcall]`.
   Guest code is called like rt/seh.cc call_handler does: arguments and the sentinel RET_SENT are pushed, fnvl_dispatch(addr)() runs it, and c->ra == RET_SENT
   afterwards means it returned. Anything else means the callee (or something it called) left by a NON-LOCAL transfer (a catch continuation, an __except block,
   longjmp, a handler of an older frame): every function here then returns at once, with registers and c->ra exactly as they are (return value 0 of the
   internal helpers). The same holds for the transfers this file makes itself (jump to an __except block, continue after a catch, longjmp): registers set,
   c->ra = target, return.
   Guest stack: every object the runtime needs (EXCEPTION_POINTERS, registrations, the catch frame) is carved below esp, which is lowered over it, so nested
   guest calls and rt/seh.cc (which builds its records below esp) never overwrite it.
   Per-thread state: struct fnvl_msvcrt_thread (rt/msvcrt.h). Process state: fnvl_msvcrt_sighandlers.
   Platform symbols: free (type_info_dtor), abort (terminate: the default terminate handler of msvcrt), both `lookup` rows of bridge.tsv.
   Not here (no row, no body): the se_translator branch of Wine's cxx_frame_handler: `_set_se_translator` has no bridge row, so no translator can be installed
   (see the report). */
#include "fnvl.h"
#include "imp.h"
#include "msvcrt.h"

extern "C" {
void fnvl_os_RtlUnwind(void);
int fnvl_raise_rec(u32 code, u32 flags, u32 addr, u32 np, const u32 *info, u32 eip, u32 sp);
void *fnvnx_win32_lookup_export(const char *dll, const char *name);      /* the platform's msvcrt exports free and abort (bridge rows bind lookup): the same lookup their import entries use */
u32 fnvl_msvcrt_sighandlers[23];
void fnvl_msvcrt_except_handler3(void);
void fnvl_msvcrt_CxxFrameHandler(void);
void fnvl_msvcrt_CxxThrowException(void);
void fnvl_msvcrt_XcptFilter(void);
void fnvl_msvcrt_terminate(void);
void fnvl_msvcrt_type_info_dtor(void);
void fnvl_msvcrt_setjmp3(void);
void fnvl_msvcrt_longjmp(void);
}

#define RET_SENT 0xDEADBEE0u
#define EH_NONCONTINUABLE 1u
#define EH_UNWINDING 2u
#define EH_EXIT_UNWIND 4u
#define DISP_CONTINUE_EXECUTION 0u
#define DISP_CONTINUE_SEARCH 1u
#define CXX_EXCEPTION 0xe06d7363u
#define CXX_MAGIC_VC6 0x19930520u
#define CXX_MAGIC_VC8 0x19930522u
#define FUNC_DESCR_SYNCHRONOUS 1u
#define FUNC_DESCR_NOEXCEPT 4u
#define TYPE_FLAG_CONST 1u
#define TYPE_FLAG_VOLATILE 2u
#define TYPE_FLAG_REFERENCE 8u
#define CLASS_IS_SIMPLE_TYPE 1u
#define JMP_MAGIC 0x56433230u
#define REC_WORDS 20u                               /* EXCEPTION_RECORD: code, flags, record, address, nparams, info[15] */
#define XCPT_CONTINUE_SEARCH 0u
#define XCPT_CONTINUE_EXECUTION 0xffffffffu

/* ---- guest layouts (offsets of the 32-bit structures) ---- */
/* EXCEPTION_RECORD */ static inline u32 rec_code(u32 r) { return M32(r); }
static inline u32 rec_info(u32 r, u32 i) { return M32(r + 20 + 4 * i); }
/* cxx_function_descr: magic 0, unwind_count 4, unwind_table 8 {prev, handler}, tryblock_count 12, tryblock 16, ipmap 20/24, expect_list 28, flags 32
   tryblock_info (20 bytes): start 0, end 4, catch_level 8, catchblock_count 12, catchblock 16;  catchblock_info (16): flags 0, type_info 4, offset 8, handler 12
   cxx_type_info (28): flags 0, type_info 4, this_offset 8, vbase_descr 12, vbase_offset 16, size 20, copy_ctor 24;  cxx_type_info_table: count 0, info[] 4
   cxx_exception_type (ThrowInfo): flags 0, destructor 4, custom_handler 8, type_info_table 12;  type_info: vtable 0, name 4, mangled[] 8
   cxx_exception_frame: Prev 0, Handler 4, trylevel 8, ebp 12 (the guest ebp of the function is frame + 12; frame - 4 is the saved-esp slot)
   catch_func_nested_frame (36 bytes): Prev 0, Handler 4, cxx_frame 8, descr 12, trylevel 16, frame_info 20 {next 20, object 24, rec 28, context 32}
   MSVCRT_EXCEPTION_FRAME: Prev 0, Handler 4, scopetable 8, trylevel 12, _ebp 16 (the function's ebp is frame + 16; frame - 4 is the EXCEPTION_POINTERS slot);
   SCOPETABLE entry (12 bytes): previousTryLevel 0, lpfnFilter 4, lpfnHandler 8;  _JUMP_BUFFER: Ebp 0, Ebx 4, Edi 8, Esi 12, Esp 16, Eip 20, Registration 24,
   TryLevel 28, Cookie 32, UnwindFunc 36, UnwindData[6] 40 */

static inline u32 fs0_get(void) { return M32(c->fs); }
static inline void fs0_set(u32 v) { M32(c->fs) = v; }          /* the exception list head: NtCurrentTeb()->Tib.ExceptionList, fs:[0] */

/* Run the guest function `fn` (cdecl or stdcall: the caller's esp is restored either way; thiscall: ecx_v). set_ebp: ebp = ebp_v for the call (Wine's call_filter,
   call_handler, call_finally_block frames). ebx, esi, edi, ebp, esp are the callee-saved state across the call (Wine's call_handler asm). -> 1 returned
   (eax in *res), 0 left by a non-local transfer. */
static int gcall(u32 fn, u32 ecx_v, int set_ebp, u32 ebp_v, const u32 *a, u32 n, u32 *res)
{
    u32 sv_ebx = ebx, sv_esi = esi, sv_edi = edi, sv_ebp = ebp, sv_esp = esp;
    for (u32 i = n; i--; ) { esp -= 4; M32(esp) = a[i]; }
    esp -= 4; M32(esp) = RET_SENT;
    ecx = ecx_v; if (set_ebp) ebp = ebp_v;
    REGS(); void (*f)(void) = fnvl_dispatch(fn); f(); REGS();
    if (c->ra != RET_SENT) return 0;
    *res = eax; ebx = sv_ebx; esi = sv_esi; edi = sv_edi; ebp = sv_ebp; esp = sv_esp;
    return 1;
}
static inline int funclet(u32 fn, u32 ebp_v, u32 *res) { return gcall(fn, 0, 1, ebp_v, 0, 0, res); }

/* RtlUnwind(target, 0, rec, 0): the runtime's own guest entry (rt/seh.cc fnvl_os_RtlUnwind), stdcall; 1 returned, 0 non-local */
static int rtl_unwind(u32 target, u32 rec)
{
    u32 sv_esp = esp;
    esp -= 16; M32(esp) = target; M32(esp + 4) = 0; M32(esp + 8) = rec; M32(esp + 12) = 0;
    esp -= 4; M32(esp) = RET_SENT;
    REGS(); fnvl_os_RtlUnwind(); REGS();
    if (c->ra != RET_SENT) return 0;
    esp = sv_esp;
    return 1;
}
static inline int global_unwind2(u32 frame) { return rtl_unwind(frame, 0); }       /* Wine _global_unwind2 */

/* the terminate of this file: Wine terminate(). Returns only when the handler left by a non-local transfer (-> 0); otherwise the process ends in abort() */
static int do_terminate(void)
{
    u32 h = fnvl_msvcrt_td()->terminate_handler, r;
    if (h && !gcall(h, 0, 0, 0, 0, 0, &r)) return 0;
    for (;;) ((void (*)(void))fnvnx_win32_lookup_export("msvcrt.dll", "abort"))();          /* abort does not return */
}

/* ---- Wine msvcrt_local_unwind2 / _local_unwind2: run the __finally handlers of `frame` from its current trylevel down to `trylevel` ---- */
static int local_unwind2(u32 frame, u32 trylevel, u32 ebp_v)
{
    u32 reg = esp - 8; esp = reg;                      /* EXCEPTION_REGISTRATION_RECORD of MSVCRT_nested_handler: a collided unwind is reported through it */
    M32(reg) = fs0_get(); M32(reg + 4) = FNVL_EH_KEY_COLLIDE; fs0_set(reg);
    while (M32(frame + 12) != 0xffffffffu && M32(frame + 12) != trylevel) {
        u32 level = M32(frame + 12), st = M32(frame + 8) + 12 * level;
        M32(frame + 12) = M32(st);                      /* previousTryLevel */
        if (!M32(st + 4)) { u32 r; if (!funclet(M32(st + 8), ebp_v, &r)) return 0; }     /* no filter: a __finally block */
    }
    fs0_set(M32(reg)); esp = reg + 8;
    return 1;
}

/* guest entry of Wine MSVCRT_nested_handler: (rec, frame, ctx, dispatch) cdecl; an unwind that meets this registration collides: *dispatch = frame, ExceptionCollidedUnwind */
extern "C" void fnvl_msvcrt_eh_nested_collide(void)
{
    u32 sp0 = esp, rec = M32(sp0 + 4), frame = M32(sp0 + 8), disp = M32(sp0 + 16), res = DISP_CONTINUE_SEARCH;
    if (M32(rec + 4) & (EH_UNWINDING | EH_EXIT_UNWIND)) { M32(disp) = frame; res = 3; }
    eax = res; c->ra = M32(sp0); esp = sp0 + 4;
}

/* ---- Wine _except_handler3 ---- */
static int except3(u32 rec, u32 frame, u32 ctx, u32 *res)
{
    *res = DISP_CONTINUE_SEARCH;
    if (M32(rec + 4) & (EH_UNWINDING | EH_EXIT_UNWIND)) return local_unwind2(frame, 0xffffffffu, frame + 16);     /* unwinding the current frame */
    u32 ptrs = esp - 8; esp = ptrs;                    /* EXCEPTION_POINTERS, read by the filter through [ebp-0x14] */
    M32(ptrs) = rec; M32(ptrs + 4) = ctx; M32(frame - 4) = ptrs;
    u32 trylevel = M32(frame + 12), scope = M32(frame + 8);
    while (trylevel != 0xffffffffu) {
        u32 st = scope + 12 * trylevel, filter = M32(st + 4);
        if (filter) {
            u32 rv; if (!gcall(filter, 0, 1, frame + 16, &ptrs, 1, &rv)) return 0;                       /* call_filter */
            if (rv == XCPT_CONTINUE_EXECUTION) { esp = ptrs + 8; *res = DISP_CONTINUE_EXECUTION; return 1; }
            if (rv == 1) {                              /* EXCEPTION_EXECUTE_HANDLER: unwind all higher frames, this one handles it */
                if (!global_unwind2(frame)) return 0;
                if (!local_unwind2(frame, trylevel, frame + 16)) return 0;
                M32(frame + 12) = M32(st);              /* trylevel of the enclosing block */
                ebp = frame + 16; c->ra = M32(st + 8);   /* call_finally_block: ebp, then jump to the __except block (it restores esp itself) */
                return 0;
            }
        }
        trylevel = M32(st);
    }
    esp = ptrs + 8;
    return 1;
}
extern "C" void fnvl_msvcrt_except_handler3(void)
{
    u32 sp0 = esp, res;
    if (!except3(M32(sp0 + 4), M32(sp0 + 8), M32(sp0 + 12), &res)) return;
    eax = res; c->ra = M32(sp0); esp = sp0 + 4;
}

/* ---- the C++ part: Wine except_i386.c ---- */
static u32 get_this_pointer(u32 off, u32 object)           /* Wine get_this_pointer: off = this_ptr_offsets {this_offset, vbase_descr, vbase_offset} */
{
    if (!object) return 0;
    if ((s32)M32(off + 4) >= 0) {
        object += M32(off + 4);                              /* move this ptr to the vbase descriptor */
        object += M32(M32(object) + M32(off + 8));           /* and fetch the additional offset from it */
    }
    return object + M32(off);
}

static int guest_strcmp(u32 a, u32 b)
{
    for (;; a++, b++) { u8 x = M8(a), y = M8(b); if (x != y) return x < y ? -1 : 1; if (!x) return 0; }
}

/* Wine find_caught_type: the catchable type of the exception type that the catch block (type_info, flags) takes; 0: none */
static u32 find_caught_type(u32 exc_type, u32 catch_ti, u32 catch_flags)
{
    u32 table = M32(exc_type + 12);
    for (u32 i = 0; i < M32(table); i++) {
        u32 type = M32(table + 4 + 4 * i);
        if (!catch_ti) return type;                                              /* catch(...) matches any type */
        if (catch_ti != M32(type + 4) && guest_strcmp(catch_ti + 8, M32(type + 4) + 8)) continue;
        if ((M32(exc_type) & TYPE_FLAG_CONST) && !(catch_flags & TYPE_FLAG_CONST)) continue;
        if ((M32(exc_type) & TYPE_FLAG_VOLATILE) && !(catch_flags & TYPE_FLAG_VOLATILE)) continue;
        return type;
    }
    return 0;
}

static void guest_memmove(u32 d, u32 s, u32 n)
{
    if (d < s) for (u32 i = 0; i < n; i++) M8(d + i) = M8(s + i);
    else for (u32 i = n; i--; ) M8(d + i) = M8(s + i);
}

/* Wine call_copy_ctor (exception_ptr.c, i386): thiscall copy constructor, this = ecx, arguments (src, 1) */
static int call_copy_ctor(u32 fn, u32 dst, u32 src)
{
    u32 a[2] = { src, 1 }, r;
    return gcall(fn, dst, 0, 0, a, 2, &r);
}

/* Wine copy_exception: copy the exception object where the catch block wants it. 0: the copy constructor left non-locally */
static int copy_exception(u32 object, u32 frame, u32 catchblock, u32 type)
{
    u32 ti = M32(catchblock + 4);
    if (!ti || !M8(ti + 8)) return 1;
    if (!M32(catchblock + 8)) return 1;
    u32 dest = frame + 12 + M32(catchblock + 8);               /* (char *)&frame->ebp + offset */
    if (M32(catchblock) & TYPE_FLAG_REFERENCE) M32(dest) = get_this_pointer(type + 8, object);
    else if (M32(type) & CLASS_IS_SIMPLE_TYPE) {
        guest_memmove(dest, object, M32(type + 20));
        if (M32(type + 20) == 4) M32(dest) = get_this_pointer(type + 8, M32(dest));          /* a pointer: adjust it */
    } else {
        u32 src = get_this_pointer(type + 8, object);
        if (M32(type + 24)) return call_copy_ctor(M32(type + 24), dest, src);
        guest_memmove(dest, src, M32(type + 20));
    }
    return 1;
}

/* Wine cxx_local_unwind: run the unwind-map actions of `frame` from its trylevel down to last_level. 0: non-local */
static int cxx_local_unwind(u32 frame, u32 descr, u32 last_level)
{
    u32 trylevel = M32(frame + 8);
    while (trylevel != last_level) {
        if ((s32)trylevel < 0 || trylevel >= M32(descr + 4)) return do_terminate();     /* invalid trylevel: terminate() */
        u32 ent = M32(descr + 8) + 8 * trylevel, h = M32(ent + 4), r;
        if (h && !funclet(h, frame + 12, &r)) return 0;
        trylevel = M32(ent);
    }
    M32(frame + 8) = last_level;
    return 1;
}

/* Wine _CreateFrameInfo / _FindAndUnlinkFrame / _IsExceptionObjectToBeDestroyed: frame_info {next 0, object 4}. A frame that is not in the list is a corrupted
   chain (the native CRT crashes there as well): every unregister follows its own create. */
static void create_frame_info(u32 fi, u32 obj)
{
    struct fnvl_msvcrt_thread *t = fnvl_msvcrt_td();
    M32(fi) = t->frame_info_head; t->frame_info_head = fi; M32(fi + 4) = obj;
}
static void find_and_unlink_frame(u32 fi)
{
    struct fnvl_msvcrt_thread *t = fnvl_msvcrt_td();
    u32 cur = t->frame_info_head;
    if (cur == fi) { t->frame_info_head = M32(cur); return; }
    for (; M32(cur); cur = M32(cur)) if (M32(cur) == fi) { M32(cur) = M32(fi); return; }
}
static int object_to_be_destroyed(u32 obj)
{
    for (u32 cur = fnvl_msvcrt_td()->frame_info_head; cur; cur = M32(cur)) if (M32(cur + 4) == obj) return 0;
    return 1;
}

/* Wine __DestructExceptionObject: the destructor of the thrown object (thiscall, this = object). 0: non-local */
static int destruct_exception_object(u32 rec)
{
    if (rec_code(rec) != CXX_EXCEPTION || M32(rec + 16) != 3) return 1;
    if (rec_info(rec, 0) < CXX_MAGIC_VC6 || rec_info(rec, 0) > CXX_MAGIC_VC8) return 1;
    u32 info = rec_info(rec, 2), object = rec_info(rec, 1), r;
    if (!info || !M32(info + 4)) return 1;
    return gcall(M32(info + 4), object, 0, 0, 0, 0, &r);
}

/* Wine __CxxUnregisterExceptionObject: frame_info {next 0, object 4, rec 8, context 12}. While a catch frame is registered, exc_record is the record of
   the innermost running catch block (non-zero), so the dereference below is always valid. 0: non-local */
static int unregister_exception_object(u32 fi, u32 in_use)
{
    struct fnvl_msvcrt_thread *t = fnvl_msvcrt_td();
    if (M32(fi + 8) == 0xffffffffu) return 1;
    find_and_unlink_frame(fi);
    u32 cur = t->exc_record;
    if (rec_code(cur) == CXX_EXCEPTION && !in_use && object_to_be_destroyed(rec_info(cur, 1)))
        if (!destruct_exception_object(cur)) return 0;
    t->exc_record = M32(fi + 8); t->ctx_record = M32(fi + 12);
    return 1;
}

static int cxx_frame_handler(u32 rec, u32 frame, u32 ctx, u32 dispatch, u32 descr, u32 nested, u32 *res);

/* Wine check_noexcept: a noexcept function that an exception propagates through calls terminate() */
static int check_noexcept(u32 rec, u32 descr, int nested)
{
    if (!nested && rec_code(rec) == CXX_EXCEPTION && M32(descr) >= CXX_MAGIC_VC8 && (M32(descr + 32) & FUNC_DESCR_NOEXCEPT)) return do_terminate();
    return 1;
}

/* `throw;` with no exception being handled: terminate() (the rule of the MSVC runtime's FindHandler; Wine would dereference a null record here) */
static int copy_current_record(u32 rec)
{
    u32 prev = fnvl_msvcrt_td()->exc_record;
    if (!prev) return 0;
    for (u32 i = 0; i < REC_WORDS; i++) M32(rec + 4 * i) = M32(prev + 4 * i);
    M32(rec + 4) = M32(rec + 4) & ~EH_UNWINDING;
    return 1;
}

/* Wine call_catch_block: find the catch block of `frame` for the exception and run it. -> 1 no catch block took it, 0 non-local (the catch continuation
   included) */
static int call_catch_block(u32 rec, u32 ctx, u32 frame, u32 descr, u32 catch_frame, u32 info)
{
    struct fnvl_msvcrt_thread *t = fnvl_msvcrt_td();
    u32 object = rec_info(rec, 1), trylevel = M32(frame + 8), save_esp = M32(frame - 4);
    u32 nf = esp - 36; esp = nf;                                   /* nested_frame on the guest stack */
    t->processing_throw++;
    for (u32 i = 0; i < M32(descr + 12); i++) {
        u32 tb = M32(descr + 16) + 20 * i, start = M32(tb), end = M32(tb + 4);
        if (catch_frame && (s32)M32(catch_frame + 16) > (s32)start) continue;      /* only try blocks inside the current catch block */
        if ((s32)trylevel < (s32)start) continue;
        if ((s32)trylevel > (s32)end) continue;
        for (u32 j = 0; j < M32(tb + 12); j++) {
            u32 cb = M32(tb + 16) + 16 * j;
            if (info) {
                u32 type = find_caught_type(info, M32(cb + 4), M32(cb));
                if (!type) continue;
                if (!copy_exception(object, frame, cb, type)) return 0;           /* copy the exception to its destination on the stack */
            } else if (M32(cb + 4)) continue;                                     /* no C++ exception: only catch(...) */
            create_frame_info(nf + 20, object);                                    /* so the exception is not freed inside RtlUnwind */
            if (!rtl_unwind(catch_frame ? catch_frame : frame, rec)) return 0;    /* unwind the stack */
            if (!cxx_local_unwind(frame, descr, start)) return 0;
            M32(frame + 8) = end + 1;
            M32(nf + 28) = t->exc_record; M32(nf + 32) = t->ctx_record;
            t->exc_record = rec; t->ctx_record = ctx; t->processing_throw--;
            M32(nf + 4) = FNVL_EH_KEY_CATCH; M32(nf + 8) = frame; M32(nf + 12) = descr; M32(nf + 16) = end + 1;      /* a registration for exceptions inside the catch block */
            M32(nf) = fs0_get(); fs0_set(nf);
            u32 addr; if (!funclet(M32(cb + 12), frame + 12, &addr)) return 0;      /* run the catch block: it returns the address to continue at */
            fs0_set(M32(nf));
            M32(frame - 4) = save_esp;
            if (!unregister_exception_object(nf + 20, 0)) return 0;
            esp = M32(frame - 4); ebp = frame + 12; c->ra = addr;                  /* continue_after_catch */
            return 0;
        }
    }
    t->processing_throw--;
    esp = nf + 36;
    return 1;
}

/* Wine cxx_frame_handler (the body of __CxxFrameHandler): -> 1 returned (disposition in *res), 0 non-local */
static int cxx_frame_handler(u32 rec, u32 frame, u32 ctx, u32 dispatch, u32 descr, u32 nested, u32 *res)
{
    *res = DISP_CONTINUE_SEARCH;
    u32 magic = M32(descr);
    if (magic < CXX_MAGIC_VC6 || magic > CXX_MAGIC_VC8) return 1;
    if (magic >= CXX_MAGIC_VC8 && (M32(descr + 32) & FUNC_DESCR_SYNCHRONOUS) && rec_code(rec) != CXX_EXCEPTION) return 1;     /* handle only c++ exceptions */
    if (M32(rec + 4) & (EH_UNWINDING | EH_EXIT_UNWIND)) {
        if (M32(descr + 4) && !nested) return cxx_local_unwind(frame, descr, 0xffffffffu);
        return 1;
    }
    if (!M32(descr + 12)) return check_noexcept(rec, descr, nested != 0);
    if (rec_code(rec) == CXX_EXCEPTION && !rec_info(rec, 1) && !rec_info(rec, 2))            /* rethrow */
        if (!copy_current_record(rec)) return do_terminate();
    u32 exc_type = 0;
    if (rec_code(rec) == CXX_EXCEPTION) {
        exc_type = rec_info(rec, 2);
        if (rec_info(rec, 0) > CXX_MAGIC_VC8 && M32(exc_type + 8)) {                      /* the exception type has its own handler */
            u32 a[8] = { rec, frame, ctx, dispatch, descr, nested ? M32(nested + 16) : 0, nested, 0 };
            return gcall(M32(exc_type + 8), 0, 0, 0, a, 8, res);
        }
    }
    if (!call_catch_block(rec, ctx, frame, descr, nested, exc_type)) return 0;
    return check_noexcept(rec, descr, nested != 0);
}

/* __CxxFrameHandler: eax = the FuncInfo pointer (the thunk's `mov eax, offset FuncInfo; jmp`), cdecl (rec, frame, context, dispatch) */
extern "C" void fnvl_msvcrt_CxxFrameHandler(void)
{
    u32 descr = eax, sp0 = esp, res;
    if (!cxx_frame_handler(M32(sp0 + 4), M32(sp0 + 8), M32(sp0 + 12), M32(sp0 + 16), descr, 0, &res)) return;
    eax = res; c->ra = M32(sp0); esp = sp0 + 4;
}

/* guest entry of Wine catch_function_nested_handler: the registration of a running catch block; (rec, frame = the catch_func_nested_frame, ctx, dispatch) cdecl */
extern "C" void fnvl_msvcrt_eh_nested_catch(void)
{
    u32 sp0 = esp, rec = M32(sp0 + 4), nf = M32(sp0 + 8), ctx = M32(sp0 + 12), res = DISP_CONTINUE_SEARCH;
    if (M32(rec + 4) & (EH_UNWINDING | EH_EXIT_UNWIND)) {
        if (!unregister_exception_object(nf + 20, 0)) return;
    } else {
        if (rec_code(rec) == CXX_EXCEPTION) {
            u32 prev = fnvl_msvcrt_td()->exc_record;
            if ((!rec_info(rec, 1) && !rec_info(rec, 2)) ||
                (rec_code(prev) == CXX_EXCEPTION && rec_info(rec, 1) == rec_info(prev, 1) && rec_info(rec, 2) == rec_info(prev, 2)))      /* the exception was rethrown */
                if (!copy_current_record(rec)) { do_terminate(); return; }
        }
        if (!cxx_frame_handler(rec, M32(nf + 8), ctx, 0, M32(nf + 12), nf, &res)) return;
    }
    eax = res; c->ra = M32(sp0); esp = sp0 + 4;
}

/* _CxxThrowException(object, type), stdcall: RaiseException(CXX_EXCEPTION, EXCEPTION_NONCONTINUABLE, 3, {magic, object, type}). The record is raised at the
   thrower's return address with the thrower's esp after the `ret 8` (rt/seh.cc fnvl_raise_rec, what fnvl_os_RaiseException does for a direct call): the state
   after it returns -- a handler that continued, or a non-local transfer -- is whatever fnvl_raise_rec left. */
extern "C" void fnvl_msvcrt_CxxThrowException(void)
{
    u32 ra = M32(esp), info[3] = { CXX_MAGIC_VC6, M32(esp + 4), M32(esp + 8) }, sp = esp + 12;
    esp = sp;
    fnvl_raise_rec(CXX_EXCEPTION, EH_NONCONTINUABLE, ra, 3, info, ra, sp);
}

/* ?terminate@@YAXXZ (cdecl): Wine terminate() */
extern "C" void fnvl_msvcrt_terminate(void) { do_terminate(); }

/* ??1type_info@@UAE@XZ (thiscall, this in ecx): Wine type_info_dtor frees the lazily allocated undecorated name */
extern "C" void fnvl_msvcrt_type_info_dtor(void)
{
    ((void (*)(void *))fnvnx_win32_lookup_export("msvcrt.dll", "free"))((void *)(unsigned long)(u32)M32(ecx + 4));
    c->ra = M32(esp); esp += 4;
}

/* ---- _XcptFilter: Wine msvcrt_exception_filter over the process's signal() handlers ---- */
#define SIGILL_ 4
#define SIGFPE_ 8
#define SIGSEGV_ 11
static u32 float_signal(u32 status)             /* Wine float_exception_map over the seven STATUS_FLOAT_* codes (the last arm is the only code of the list that remains: UNDERFLOW) */
{
    switch (status) {
    case 0xC000008Du: return 0x82;   /* EXCEPTION_FLT_DENORMAL_OPERAND  -> _FPE_DENORMAL */
    case 0xC000008Eu: return 0x83;   /* EXCEPTION_FLT_DIVIDE_BY_ZERO    -> _FPE_ZERODIVIDE */
    case 0xC000008Fu: return 0x86;   /* EXCEPTION_FLT_INEXACT_RESULT    -> _FPE_INEXACT */
    case 0xC0000090u: return 0x81;   /* EXCEPTION_FLT_INVALID_OPERATION -> _FPE_INVALID */
    case 0xC0000091u: return 0x84;   /* EXCEPTION_FLT_OVERFLOW          -> _FPE_OVERFLOW */
    case 0xC0000092u: return 0x8a;   /* EXCEPTION_FLT_STACK_CHECK       -> _FPE_STACKOVERFLOW */
    default:          return 0x85;   /* EXCEPTION_FLT_UNDERFLOW         -> _FPE_UNDERFLOW */
    }
}
/* run the handler of signal `sig` (not SIG_DFL, not SIG_IGN) for the exception pointers `ptrs`: the handler slot goes back to SIG_DFL first, __pxcptinfoptrs()
   holds the pointers meanwhile. -> 0: non-local */
static int run_sighandler(u32 sig, u32 ptrs, int with_fpe_code, u32 fpe_code)
{
    u32 h = fnvl_msvcrt_sighandlers[sig], r, a[2] = { sig, fpe_code }, *ep = &fnvl_msvcrt_td()->xcptinfo, old = *ep;
    *ep = ptrs; fnvl_msvcrt_sighandlers[sig] = 0;
    if (!gcall(h, 0, 0, 0, a, with_fpe_code ? 2 : 1, &r)) return 0;
    *ep = old;
    return 1;
}
static int xcpt_filter(u32 ptrs, u32 *res)
{
    *res = XCPT_CONTINUE_SEARCH;
    if (!ptrs || !M32(ptrs)) return 1;
    u32 code = rec_code(M32(ptrs)), sig;
    switch (code) {
    case 0xC0000005u: sig = SIGSEGV_; break;                                              /* EXCEPTION_ACCESS_VIOLATION */
    case 0xC000008Du: case 0xC000008Eu: case 0xC000008Fu: case 0xC0000090u: case 0xC0000091u: case 0xC0000092u: case 0xC0000093u: sig = SIGFPE_; break;
    case 0xC000001Du: case 0xC0000096u: sig = SIGILL_; break;                             /* EXCEPTION_ILLEGAL_INSTRUCTION, EXCEPTION_PRIV_INSTRUCTION */
    default: return 1;
    }
    u32 h = fnvl_msvcrt_sighandlers[sig];
    if (h == 0) return 1;                                                                 /* SIG_DFL */
    if (h != 1 && !run_sighandler(sig, ptrs, sig == SIGFPE_, float_signal(code))) return 0;     /* not SIG_IGN */
    *res = XCPT_CONTINUE_EXECUTION;
    return 1;
}
/* _XcptFilter(ex, ptrs), cdecl: Wine _XcptFilter (ex is assumed equal to the record's code) */
extern "C" void fnvl_msvcrt_XcptFilter(void)
{
    u32 sp0 = esp, res;
    if (!xcpt_filter(M32(sp0 + 8), &res)) return;
    eax = res; c->ra = M32(sp0); esp = sp0 + 4;
}

/* ---- setjmp / longjmp: Wine MSVCRT__setjmp3 (DEFINE_SETJMP_ENTRYPOINT + __regs_MSVCRT__setjmp3), MSVCRT_longjmp ---- */
/* _setjmp3(jmp_buf, nb_args, ...), cdecl. The registers and the Esp / Eip of the call are saved as the entry does (Esp = the address of the return address). */
extern "C" void fnvl_msvcrt_setjmp3(void)
{
    u32 sp0 = esp, jb = M32(sp0 + 4); s32 nb = (s32)M32(sp0 + 8);
    M32(jb) = ebp; M32(jb + 4) = ebx; M32(jb + 8) = edi; M32(jb + 12) = esi; M32(jb + 16) = sp0; M32(jb + 20) = M32(sp0);
    M32(jb + 32) = JMP_MAGIC; M32(jb + 36) = 0;
    u32 reg = fs0_get(); M32(jb + 24) = reg;
    if (reg == 0xffffffffu) M32(jb + 28) = 0xffffffffu;
    else {
        if (nb > 0) M32(jb + 36) = M32(sp0 + 12);                                          /* UnwindFunc */
        if (nb > 1) M32(jb + 28) = M32(sp0 + 16); else M32(jb + 28) = M32(reg + 12);       /* TryLevel: the argument, or that of the current frame */
        for (s32 i = 0; i < 6 && i < nb - 2; i++) M32(jb + 40 + 4 * i) = M32(sp0 + 20 + 4 * i);    /* UnwindData */
    }
    eax = 0; c->ra = M32(sp0); esp = sp0 + 4;
}

/* longjmp(jmp_buf, retval), cdecl. Unwind to the saved registration (RtlUnwind when it is not the current head), run the unwind function of the buffer (or
   _local_unwind2 for a buffer without the 'VC20' cookie), then the transfer: ebp, ebx, edi, esi, esp = Esp + 4 (the return address of the _setjmp3 call is
   popped, as that call's `ret` would), c->ra = Eip, eax = retval (1 for 0). The cookie word lies inside the 64-byte buffer the caller passes (IsBadReadPtr is false). */
extern "C" void fnvl_msvcrt_longjmp(void)
{
    u32 sp0 = esp, jb = M32(sp0 + 4), retval = M32(sp0 + 8), reg = M32(jb + 24);
    if (fs0_get() != reg && !global_unwind2(reg)) return;
    if (reg) {
        if (M32(jb + 32) != JMP_MAGIC) { if (!local_unwind2(reg, M32(jb + 28), M32(jb))) return; }
        else if (M32(jb + 36)) { u32 r; if (!gcall(M32(jb + 36), 0, 0, 0, &jb, 1, &r)) return; }       /* UnwindFunc(jmp_buf), stdcall */
    }
    if (!retval) retval = 1;
    ebp = M32(jb); ebx = M32(jb + 4); edi = M32(jb + 8); esi = M32(jb + 12); esp = M32(jb + 16) + 4; c->ra = M32(jb + 20); eax = retval;
}
