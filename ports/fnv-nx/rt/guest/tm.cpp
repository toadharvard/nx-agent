/* The transactional-memory entry points that libstdc++'s cow-stdexcept.o (std::logic_error, std::runtime_error, ...) names.
 * Its transactional clones of the exception classes (_ZGTt*, each in a section of its own, referenced by nothing but their own
 * unwind tables: the object has no .tm_clone_table) call them from inside a `__transaction_atomic` region; they are weak references there
 * because libitm is optional in the toolchain, and the link stage admits no weak reference without a definition.
 *
 * The guest links no _ITM_beginTransaction and no code of the program is compiled with -fgnu-tm, so no transaction can begin and no
 * clone can run.  Each function below is the operation libitm performs in a transaction that runs serial-irrevocably (alone, never
 * rolled back; libitm/method-serial.cc: the access barriers are the plain access, libitm/alloc_cpp.cc: the allocation clones record
 * the block for a rollback that cannot happen):
 *   _ITM_RU1, _ITM_RU4               the plain load                      (libitm.h: read, undo-logged, unsigned 8 / 32 bit)
 *   _ITM_memcpyRnWt, _ITM_memcpyRtWn the plain memcpy                    (read non-transactional / transactional, write the other)
 *   _ZGTtnam, _ZGTtdlPv              ::operator new[] / ::operator delete (the delete libitm defers to the commit is done at once: a
 *                                    transaction that never rolls back cannot tell the order)
 * OPEN: _ITM_addUserCommitAction is NOT such a plain operation.  libitm queues the action and runs it when the outermost transaction
 * COMMITS, after the rest of the transaction body; the body below runs it at once, which is a different order.  It stays only because
 * no transaction exists; the class is open (reported to the L1 lead): the right end state is a reachability certificate for "no TM
 * clone is reachable in the linked set", after which every function of this file becomes fnvnx_reach_violation(fact). */
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <new>

extern "C" {
uint8_t _ITM_RU1(const uint8_t *p) { return *p; }
uint32_t _ITM_RU4(const uint32_t *p) { return *p; }
void _ITM_memcpyRnWt(void *dst, const void *src, size_t n) { memcpy(dst, src, n); }
void _ITM_memcpyRtWn(void *dst, const void *src, size_t n) { memcpy(dst, src, n); }
void _ITM_addUserCommitAction(void (*action)(void *), uint64_t resuming_id, void *arg) {
    (void)resuming_id;   /* libitm requires _ITM_noTransactionId here */
    action(arg);
}
void *_ZGTtnam(size_t n) { return ::operator new[](n); }
void _ZGTtdlPv(void *p) { ::operator delete(p); }
}
